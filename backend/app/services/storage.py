"""File storage behind a small interface so an S3-compatible backend can be added later."""

import contextlib
import os
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import get_settings
from app.core.errors import PayloadTooLargeError

CHUNK_BYTES = 1024 * 1024


def version_prefix(tenant_id: uuid.UUID, dataset_id: uuid.UUID, version: int) -> str:
    """Return the storage prefix of one dataset version: <tenant>/<dataset>/<version>."""
    return f"{tenant_id}/{dataset_id}/{int(version)}"


class StorageBackend(Protocol):
    """Minimal storage contract: keys are relative, '/'-separated, server-generated."""

    def save_stream(self, key: str, source: BinaryIO, max_bytes: int) -> int:
        """Store `source` under `key`; raise PayloadTooLargeError past `max_bytes`."""

    def local_path(self, key: str) -> Path:
        """Return a local filesystem path for reading `key` (downloading if remote)."""

    def new_path(self, key: str) -> Path:
        """Return a local path where a worker may write the object for `key`."""

    def exists(self, key: str) -> bool:
        """Return True if `key` exists."""

    def delete_prefix(self, prefix: str) -> None:
        """Delete everything under `prefix`."""

    def save_bytes(self, key: str, data: bytes) -> int:
        """Store `data` under `key` atomically; return bytes written."""

    def delete(self, key: str) -> None:
        """Delete one object; a missing object is ignored."""


def report_key(tenant_id: uuid.UUID) -> str:
    """Return a new, unguessable storage key for a report PDF: <tenant>/reports/<hex>.pdf."""
    return f"{tenant_id}/reports/{uuid.uuid4().hex}.pdf"


class LocalStorage:
    """Store files under a root directory (STORAGE_DIR)."""

    def __init__(self, root: str | Path) -> None:
        """Create the root directory if needed."""
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("storage key escapes the storage root")
        return path

    def save_stream(self, key: str, source: BinaryIO, max_bytes: int) -> int:
        """Copy `source` to `key` in chunks, enforcing `max_bytes`; return bytes written."""
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        written = 0
        try:
            with partial.open("wb") as out:
                while chunk := source.read(CHUNK_BYTES):
                    written += len(chunk)
                    if written > max_bytes:
                        raise PayloadTooLargeError(
                            f"File is larger than the {max_bytes // (1024 * 1024)} MB limit"
                        )
                    out.write(chunk)
            os.replace(partial, target)
        except BaseException:
            with contextlib.suppress(OSError):
                partial.unlink()
            raise
        return written

    def local_path(self, key: str) -> Path:
        """Return the path of an existing object."""
        return self._resolve(key)

    def new_path(self, key: str) -> Path:
        """Return a writable path, creating parent directories."""
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def exists(self, key: str) -> bool:
        """Return True if the object exists."""
        return self._resolve(key).is_file()

    def delete_prefix(self, prefix: str) -> None:
        """Remove a directory tree; missing prefixes are ignored."""
        path = self._resolve(prefix)
        shutil.rmtree(path, ignore_errors=True)

    def save_bytes(self, key: str, data: bytes) -> int:
        """Write `data` to a .part file and rename it into place."""
        target = self.new_path(key)
        partial = target.with_name(target.name + ".part")
        try:
            partial.write_bytes(data)
            os.replace(partial, target)
        except BaseException:
            with contextlib.suppress(OSError):
                partial.unlink()
            raise
        return len(data)

    def delete(self, key: str) -> None:
        """Remove one file; a missing file is ignored."""
        with contextlib.suppress(FileNotFoundError):
            self._resolve(key).unlink()


def get_storage() -> StorageBackend:
    """Return the configured storage backend."""
    return LocalStorage(get_settings().storage_dir)
