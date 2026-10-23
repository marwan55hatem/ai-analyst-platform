"""Run analysis code on a CSV without the database: a typed `data` table and a module context."""

import csv
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from app.core.config import get_settings
from app.ml.base import AnalysisContext, DataFacts
from app.ml.engine import open_analysis
from app.ml.quality import QualityContext
from app.ml.roles import RoleMap
from tests.ground_truth_fixtures import generator

# columns of the ground-truth CSV -> roles (the profiler finds the same ones)
ROLES = {
    "order_id": "order_id",
    "date": "order_date",
    "customer_id": "customer_id",
    "product": "product",
    "category": "category",
    "region": "region",
    "channel": "channel",
    "quantity": "quantity",
    "price": "unit_price",
    "revenue": "revenue",
    "cost": "cost",
}


def generate(tmp_path: Path, **options: Any) -> tuple[Path, dict[str, Any]]:
    """Write a ground-truth CSV into its own folder under tmp_path; return (csv, facts)."""
    folder = tmp_path / f"gen-{uuid.uuid4().hex[:8]}"
    csv_path, _, facts = generator().generate(folder, **options)
    return csv_path, facts


def write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    """Write dict rows as a CSV with LF line endings."""
    with path.open("w", newline="\n", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read a CSV into dict rows."""
    with csv_path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def to_parquet(csv_path: Path) -> Path:
    """Convert a CSV to Parquet next to it (types sniffed by DuckDB, ids kept as text)."""
    target = csv_path.with_suffix(".parquet")
    src, dst = csv_path.as_posix(), target.as_posix()
    duckdb.sql(
        f"COPY (SELECT * FROM read_csv('{src}', header = true, "
        "types = {'customer_id': 'VARCHAR', 'order_id': 'VARCHAR'})) "
        f"TO '{dst}' (FORMAT parquet)"
    )
    return target


def role_map(csv_path: Path, extra: dict[str, str] | None = None) -> RoleMap:
    """The ground-truth roles that exist in this CSV (plus `extra`)."""
    with csv_path.open(encoding="utf-8") as handle:
        header = handle.readline().strip().split(",")
    columns = {role: name for role, name in ROLES.items() if name in header}
    if "discount_pct" in header:
        columns["discount"] = "discount_pct"
    columns.update(extra or {})
    return RoleMap(columns=columns)


@contextmanager
def module_context(
    csv_path: Path,
    *,
    extra_roles: dict[str, str] | None = None,
    pii_roles: frozenset[str] = frozenset(),
    config: dict[str, Any] | None = None,
) -> Iterator[AnalysisContext]:
    """Yield an AnalysisContext over the CSV (clean quality, no duplicates)."""
    roles = role_map(csv_path, extra_roles)
    with open_analysis(to_parquet(csv_path), roles) as connection:
        yield AnalysisContext(
            tenant_id=uuid.UUID(int=7),
            version_id=uuid.UUID(int=8),
            roles=roles,
            db=connection,
            settings=get_settings(),
            quality=QualityContext.from_version(None, None, []),
            config=dict(config or {}),
            facts=DataFacts(pii_roles=pii_roles),
        )
