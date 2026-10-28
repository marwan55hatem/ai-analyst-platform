"""The OpenAPI dump that feeds the frontend's generated client."""

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dump_openapi.py"
_spec = importlib.util.spec_from_file_location("dump_openapi", _SCRIPT)
assert _spec and _spec.loader
dump_openapi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dump_openapi)


def test_schema_has_the_routes_the_frontend_uses() -> None:
    schema = dump_openapi.build_schema()
    paths = schema["paths"]
    for path in (
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/me",
        "/api/v1/datasets",
        "/api/v1/datasets/{dataset_id}/cleaning-plan/apply",
        "/api/v1/analysis-runs/{run_id}",
    ):
        assert path in paths, path


def test_render_is_deterministic_and_lf_terminated() -> None:
    schema = dump_openapi.build_schema()
    first = dump_openapi.render(schema)
    assert first == dump_openapi.render(json.loads(first))
    assert first.endswith("}\n")
    assert "\r" not in first


def test_main_writes_the_file(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "openapi.json"
    assert dump_openapi.main([str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["openapi"].startswith("3.")
