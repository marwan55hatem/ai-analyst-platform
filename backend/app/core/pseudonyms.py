"""Customer-id pseudonyms toward the model, and the server-side map back to the real ids.

The model only ever sees codes such as `CUST-7F3K9QAB` for values of the column with the
customer_id role. A code is an HMAC of `tenant|dataset|value` under PSEUDONYM_SECRET, so it is
stable for one dataset (across its versions), different in every other tenant or dataset, and
cannot be reversed without the key. The map from code back to the real id is built per dataset
version from the parquet file, cached next to it in tenant storage, and used only to show real ids
in the UI and exports. It is never sent to the model.
"""

import hashlib
import hmac
import os
import re
import threading
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.core.config import Settings

PREFIX = "CUST-"
SHORT_LENGTH = 8  # 40 bits
LONG_LENGTH = 10  # 50 bits, for the rare codes that collide inside one dataset
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# Models sometimes write the hyphen as a non-breaking hyphen, an en dash or a minus sign.
DASHES = "-‐‑‒–—−"
CODE_PATTERN = re.compile(rf"CUST[{DASHES}][0-9A-HJKMNP-TV-Z]{{1,10}}\b")


def canonical_code(text: str) -> str:
    """Return a code as the map stores it (`CUST-` with a plain hyphen)."""
    return PREFIX + text[len(PREFIX) :] if len(text) > len(PREFIX) else text


LEGACY_PATTERN = re.compile(r"^cust_[0-9a-f]{16}$")
CACHE_SIZE = 8
_cache: "OrderedDict[tuple[str, str, str, str], PseudonymMap]" = OrderedDict()
_lock = threading.Lock()


def _digest(key: bytes, tenant_id: str, dataset_id: str, value: str) -> bytes:
    return hmac.new(key, f"{tenant_id}|{dataset_id}|{value}".encode(), hashlib.sha256).digest()


def _encode(digest: bytes, length: int) -> str:
    number = int.from_bytes(digest[:8], "big")
    chars = []
    for _ in range(length):
        chars.append(CROCKFORD[number & 31])
        number >>= 5
    return "".join(reversed(chars))


def customer_code(key: bytes, tenant_id: str, dataset_id: str, value: str) -> str:
    """Return the short code for one customer id (before collision handling)."""
    return PREFIX + _encode(_digest(key, tenant_id, dataset_id, value), SHORT_LENGTH)


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalise(token: str) -> str:
    """Return the form used to match a typed id: NFKC, Western digits, trimmed, case-folded."""
    return unicodedata.normalize("NFKC", token).translate(_DIGITS).strip().casefold()


def key_id(key: bytes) -> str:
    """Return a short fingerprint of the key, so a key rotation gets a fresh map file."""
    return hashlib.sha256(b"pseudonym-key-id|" + key).hexdigest()[:8]


@dataclass
class PseudonymMap:
    """Both directions for one dataset version's customer ids (server side only)."""

    column: str | None = None
    forward: dict[str, str] = field(default_factory=dict)
    backward: dict[str, str] = field(default_factory=dict)
    typed: dict[str, str] = field(default_factory=dict)
    legacy: dict[str, str] = field(default_factory=dict)
    pattern: re.Pattern[str] = CODE_PATTERN
    hidden: bool = False  # show the pages' `cust_` pseudonym instead of the real id

    def for_display(self, *, hidden: bool) -> "PseudonymMap":
        """Return a view that shows real ids, or the pages' pseudonyms when the id is hidden."""
        return replace(self, hidden=hidden)

    def shown(self, code: str) -> str | None:
        """Return what a person sees for a code: the real id or, when hidden, its pseudonym."""
        real = self.backward.get(code)
        if real is None or not self.hidden:
            return real
        return self._legacy_of().get(real)

    def _legacy_of(self) -> dict[str, str]:
        cached: dict[str, str] | None = self.__dict__.get("_legacy_reverse")
        if cached is None:
            cached = {real: stored for stored, real in self.legacy.items()}
            self.__dict__["_legacy_reverse"] = cached
        return cached

    @classmethod
    def build(
        cls,
        key: bytes,
        tenant_id: str,
        dataset_id: str,
        values: Iterable[str],
        column: str | None = None,
    ) -> "PseudonymMap":
        """Code every distinct value; values whose short codes collide get the long form."""
        from app.ml.modules.segments import pseudonym as legacy_code

        distinct = sorted({str(v) for v in values})
        digests = {v: _digest(key, tenant_id, dataset_id, v) for v in distinct}
        short: dict[str, list[str]] = {}
        for value, digest in digests.items():
            short.setdefault(PREFIX + _encode(digest, SHORT_LENGTH), []).append(value)
        forward: dict[str, str] = {}
        for code, owners in short.items():
            if len(owners) == 1:
                forward[owners[0]] = code
                continue
            for value in owners:
                forward[value] = PREFIX + _encode(digests[value], LONG_LENGTH)
        backward = {code: value for value, code in forward.items()}
        if len(backward) != len(forward):
            raise ValueError("customer pseudonyms collide even in the long form")
        typed: dict[str, str] = {}
        for value, code in forward.items():
            typed.setdefault(normalise(value), code)
        legacy = {legacy_code(key, tenant_id, value): value for value in distinct}
        return cls(column=column, forward=forward, backward=backward, typed=typed, legacy=legacy)

    def __len__(self) -> int:
        """Return the number of distinct customer ids."""
        return len(self.forward)

    def to_pseudonym(self, real: Any) -> str | None:
        """Return the code for a real id, or None when the id is unknown."""
        return None if real is None else self.forward.get(str(real))

    def to_real(self, code: str) -> str | None:
        """Return the real id for a code, or None when the code is not in this dataset."""
        return self.backward.get(code)

    def lookup_token(self, token: str) -> str | None:
        """Return the code for an id the user typed (any case, spaces ignored) or a `cust_` key."""
        found = self.typed.get(normalise(token))
        if found is None and LEGACY_PATTERN.match(token.strip().lower()):
            return self.from_stored(token.strip().lower())
        return found

    def from_stored(self, stored: Any) -> str | None:
        """Return the code for a stored customer key (the raw id or an older `cust_` pseudonym)."""
        real = self.real_from_stored(stored)
        return None if real is None else self.forward.get(real)

    def real_from_stored(self, stored: Any) -> str | None:
        """Return the real id behind a stored customer key, or None when it is unknown."""
        if stored is None:
            return None
        text = str(stored)
        if LEGACY_PATTERN.match(text):
            return self.legacy.get(text)
        return text if text in self.forward else None


def _customer_column(db: Any, tenant_id: Any, version: Any) -> str | None:
    from app.services.analysis import _roles_for

    return _roles_for(db, tenant_id, version).columns.get("customer_id")


def _map_key(version: Any, settings: Settings, column: str) -> str:
    """Name the map file by key and column, so a key rotation or a role change gets a new one."""
    folder = PurePosixPath(str(version.parquet_path)).parent
    column_id = hashlib.sha256(column.encode()).hexdigest()[:8]
    return str(folder / f"pseudonyms-{key_id(settings.pseudonym_key)}-{column_id}.parquet")


def load_pseudonym_map(
    db: Any, tenant_id: Any, version: Any, settings: Settings
) -> PseudonymMap | None:
    """Return the map for a tenant-owned version, or None when it has no customer_id column."""
    from app.db.repository import DatasetVersionRepository
    from app.services.storage import get_storage

    stored = DatasetVersionRepository(db, tenant_id).get_for_query(version.id)
    if stored is None or not stored.parquet_path:
        return None
    column = _customer_column(db, tenant_id, stored)
    if column is None:
        return None
    cache_key = (str(tenant_id), str(stored.id), key_id(settings.pseudonym_key), column)
    with _lock:
        if cache_key in _cache:
            _cache.move_to_end(cache_key)
            return _cache[cache_key]
    storage = get_storage()
    values = _distinct_values(str(storage.local_path(stored.parquet_path)), column, settings)
    mapping = PseudonymMap.build(
        settings.pseudonym_key, str(tenant_id), str(stored.dataset_id), values, column=column
    )
    _save_map_file(storage, _map_key(stored, settings, column), mapping)
    with _lock:
        _cache[cache_key] = mapping
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return mapping


def map_file_path(db: Any, tenant_id: Any, version: Any, settings: Settings) -> str | None:
    """Return the local path of the (real, code) parquet file for SQL joins, building it once."""
    from app.services.storage import get_storage

    mapping = load_pseudonym_map(db, tenant_id, version, settings)
    if mapping is None or mapping.column is None:
        return None
    storage = get_storage()
    key = _map_key(version, settings, mapping.column)
    _save_map_file(storage, key, mapping)  # rebuilt if it was removed or is unreadable
    return str(storage.local_path(key))


def _distinct_values(parquet: str, column: str, settings: Settings) -> list[str]:
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
        quoted = '"' + column.replace('"', '""') + '"'
        rows = con.execute(
            f"SELECT DISTINCT CAST({quoted} AS VARCHAR) FROM read_parquet(?) "  # noqa: S608
            f"WHERE {quoted} IS NOT NULL",
            [parquet],
        ).fetchall()
    finally:
        con.close()
    return [str(r[0]) for r in rows]


def _readable(path: Any) -> bool:
    try:
        pq.read_metadata(path)
    except (OSError, pa.ArrowException):
        return False
    return True


def _save_map_file(storage: Any, key: str, mapping: PseudonymMap) -> None:
    """Write the map file once; a missing or unreadable file is (re)written atomically."""
    if storage.exists(key) and _readable(storage.local_path(key)):
        return
    frame = pd.DataFrame(
        {"real": list(mapping.forward), "code": list(mapping.forward.values())},
        dtype="string",
    )
    path = storage.new_path(key)
    part = path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")
    try:
        frame.to_parquet(part, index=False)
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def clear_cache() -> None:
    """Forget cached maps (tests and key rotation)."""
    with _lock:
        _cache.clear()
