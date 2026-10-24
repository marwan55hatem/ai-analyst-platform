"""API side of the sandbox: package one tenant's role columns and send code over the socket.

The sandbox never sees the storage volume. This module checks the version belongs to the tenant
(through the tenant-bound repository), reads only the role columns with DuckDB, removes personal
data unless the company opted in (a personal-data customer id becomes its pseudonym), samples rows
when the data is larger than SANDBOX_MAX_UPLOAD_MB, and sends CSV text in the request body.
"""

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import duckdb
import httpx
import pandas as pd

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.db.repository import ColumnProfileRepository, DatasetVersionRepository
from app.ml.modules.segments import pseudonym
from app.services.analysis import _roles_for
from app.services.storage import get_storage

DTYPES = {
    "integer": "Int64",
    "float": "float64",
    "boolean": "boolean",
    "datetime": "datetime",
}
SAMPLE_SEED = 42
PROBE_ROWS = 2_000


class SandboxError(Exception):
    """The sandbox could not run the job (busy, unreachable, bad response)."""

    def __init__(self, code: str, message: str) -> None:
        """Keep a stable code and a model-readable message."""
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class SandboxData:
    """The CSV sent to the sandbox and what was left out or changed."""

    csv: str
    dtypes: dict[str, str]
    columns: list[str]
    rows: int
    total_rows: int
    removed_pii: list[str] = field(default_factory=list)
    pseudonymised: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_data(
    db: Any, tenant_id: Any, version: Any, settings: Settings, *, allow_pii: bool
) -> SandboxData:
    """Return the role columns of a version of this tenant as CSV (sampled when too large)."""
    stored = DatasetVersionRepository(db, tenant_id).get_for_query(version.id)
    if stored is None:
        raise NotFoundError("Dataset version not found")
    if not stored.parquet_path:
        raise ConflictError("The dataset version is not ready.", code="dataset_not_ready")
    profiles = {p.name: p for p in ColumnProfileRepository(db, tenant_id).for_version(version.id)}
    roles = _roles_for(db, tenant_id, stored)
    role_of = {column: role for role, column in roles.columns.items() if column in profiles}
    removed = sorted(
        c for c in role_of if profiles[c].pii_flag and not allow_pii and role_of[c] != "customer_id"
    )
    pseudo = sorted(
        c for c in role_of if profiles[c].pii_flag and not allow_pii and role_of[c] == "customer_id"
    )
    columns = [c for c in role_of if c not in removed]
    if not columns:
        raise ConflictError("The dataset has no role columns to analyse.", code="no_role_columns")
    path = str(get_storage().local_path(stored.parquet_path))
    frame, total = _read(path, columns, settings)
    warnings: list[str] = []
    budget = int(settings.sandbox_max_upload_mb * 1024 * 1024)
    csv = frame.to_csv(index=False, lineterminator="\n")
    if len(csv) > budget and len(frame):
        keep = max(1, int(len(frame) * budget / len(csv) * 0.9))
        frame = frame.sample(n=keep, random_state=SAMPLE_SEED).sort_index()
        csv = frame.to_csv(index=False, lineterminator="\n")
        warnings.append(
            f"The data was larger than {settings.sandbox_max_upload_mb:g} MB, so a random sample "
            f"of {keep} of {total} rows was analysed; totals from this code are sample values."
        )
    if pseudo:
        for column in pseudo:
            frame[column] = [
                None if pd.isna(v) else pseudonym(settings.pseudonym_key, str(tenant_id), str(v))
                for v in frame[column]
            ]
        csv = frame.to_csv(index=False, lineterminator="\n")
    dtypes = {
        c: ("string" if c in pseudo else DTYPES.get(profiles[c].inferred_type, "string"))
        for c in columns
    }
    return SandboxData(
        csv=csv,
        dtypes=dtypes,
        columns=columns,
        rows=len(frame),
        total_rows=total,
        removed_pii=removed,
        pseudonymised=pseudo,
        warnings=warnings,
    )


def _read(path: str, columns: list[str], settings: Settings) -> tuple[pd.DataFrame, int]:
    con = duckdb.connect(
        ":memory:",
        config={
            "memory_limit": f"{settings.query_memory_mb}MB",
            "threads": settings.query_threads,
            "autoload_known_extensions": False,
            "autoinstall_known_extensions": False,
        },
    )
    try:
        quoted = ", ".join('"' + c.replace('"', '""') + '"' for c in columns)
        total = int(con.execute("SELECT count(*) FROM read_parquet(?)", [path]).fetchone()[0])  # type: ignore[index]
        probe = con.execute(f"SELECT {quoted} FROM read_parquet(?) LIMIT {PROBE_ROWS}", [path]).df()
        per_row = max(1, len(probe.to_csv(index=False)) // max(1, len(probe)))
        # Never pull far more rows than can be sent: sample in DuckDB before building the CSV.
        limit = int(settings.sandbox_max_upload_mb * 1024 * 1024 / per_row * 1.2) + 1
        if total > limit:
            sql = (
                f"SELECT {quoted} FROM read_parquet(?) "
                f"USING SAMPLE reservoir({limit} ROWS) REPEATABLE ({SAMPLE_SEED})"
            )
        else:
            sql = f"SELECT {quoted} FROM read_parquet(?)"
        frame = con.execute(sql, [path]).df()
    finally:
        con.close()
    return frame, total


class SandboxClient:
    """HTTP client for the sandbox over a unix socket (`unix:///path`) or TCP (`http://...`)."""

    def __init__(
        self, url: str, timeout_s: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        """Build the client; the request timeout leaves 5 s on top of the job timeout."""
        parsed = urlparse(url)
        if transport is None and parsed.scheme == "unix":
            transport = httpx.HTTPTransport(uds=parsed.path)
            base = "http://sandbox"
        else:
            base = url.rstrip("/")
        self._http = httpx.Client(
            base_url=base, transport=transport, timeout=httpx.Timeout(timeout_s + 5, connect=3)
        )

    def run(self, code: str, data: SandboxData) -> dict[str, Any]:
        """Run code on the data; raise SandboxError when the sandbox cannot."""
        body = json.dumps({"code": code, "data_csv": data.csv, "dtypes": data.dtypes})
        try:
            response = self._http.post(
                "/run", content=body, headers={"Content-Type": "application/json"}
            )
        except httpx.TimeoutException as exc:
            raise SandboxError("sandbox_timeout", "The sandbox did not answer in time.") from exc
        except httpx.TransportError as exc:
            raise SandboxError(
                "sandbox_unavailable", "The Python sandbox is not reachable."
            ) from exc
        if response.status_code == 429:
            raise SandboxError("sandbox_busy", "The Python sandbox is busy; try again shortly.")
        if response.status_code != 200:
            raise SandboxError("sandbox_error", f"The sandbox answered {response.status_code}.")
        try:
            result: dict[str, Any] = response.json()
        except ValueError as exc:
            raise SandboxError("sandbox_error", "The sandbox sent an unreadable answer.") from exc
        return result

    def close(self) -> None:
        """Close the connection pool."""
        self._http.close()
