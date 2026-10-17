"""The API saves uploads and the worker converts them, so both must share STORAGE_DIR."""

from pathlib import Path
from typing import Any

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _compose() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return loaded


def _storage_mount(service: dict[str, Any]) -> tuple[str, str]:
    mounts = [
        v for v in service["volumes"] if v.split(":")[1] == service["environment"]["STORAGE_DIR"]
    ]
    assert len(mounts) == 1, "STORAGE_DIR must be a volume mount"
    source, target = mounts[0].split(":")[:2]
    return source, target


def test_api_and_worker_mount_the_same_named_volume_at_storage_dir() -> None:
    compose = _compose()
    api = _storage_mount(compose["services"]["api"])
    worker = _storage_mount(compose["services"]["worker"])
    assert api == worker
    source, _ = api
    assert source in compose["volumes"], "storage must be a named volume, not a host path"


def test_api_and_worker_get_identical_upload_limits() -> None:
    services = _compose()["services"]
    for key in ("STORAGE_DIR", "MAX_UPLOAD_MB", "MAX_ROWS"):
        assert services["api"]["environment"][key] == services["worker"]["environment"][key]
