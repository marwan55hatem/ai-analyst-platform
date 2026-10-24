"""Sandbox escape suite: the run-time layers (child process, rlimits, audit hook, restricted builtins).

Runs on Linux only (the sandbox needs `resource` and POSIX process groups); CI is Linux. Payloads go
through `_spawn` directly so the static check cannot hide a weakness in the run-time layers; the
full path (static check first) is tested through `run_job` and the HTTP server. The container
layers (no network, read-only root, no storage volume) are checked by `sandbox-check` in CI.
"""

import json
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the sandbox runs on Linux only")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sandbox"))

DATA = "region,revenue\nNorth,10\nSouth,32\n"
DTYPES = {"region": "string", "revenue": "float64"}
REAL = "type(df).__init__.__globals__['__builtins__']"  # reaches the real builtins at run time


def spawn(code: str, timeout_s: float = 4.0) -> Any:
    """Run code with the run-time layers only (no static check)."""
    from sandbox_server import executor

    workdir = tempfile.mkdtemp(prefix="escape-")
    Path(workdir, "data.csv").write_text(DATA, encoding="utf-8", newline="\n")
    Path(workdir, "job.json").write_text(
        json.dumps({"code": code, "dtypes": DTYPES}), encoding="utf-8", newline="\n"
    )
    started = time.monotonic()
    try:
        result = executor._spawn(workdir, executor.Limits(timeout_s=timeout_s))
    finally:
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def test_normal_code_runs_and_returns_json() -> None:
    result = spawn("result = {'total': float(df['revenue'].sum()), 'rows': len(df)}\nprint('hi')")
    assert result.ok and result.result == {"total": 42.0, "rows": 2}
    assert result.stdout.strip() == "hi"


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("read /etc/passwd via real open", f"result = {REAL}['open']('/etc/passwd').read()"),
        (
            "read the storage volume",
            f"result = {REAL}['open']('/app/storage/x/data.parquet').read()",
        ),
        ("list another tenant's folder", "result = pd.io.common.os.listdir('/app/storage')"),
        ("os.system", "pd.io.common.os.system('id')"),
        ("fork", "pd.io.common.os.fork()"),
        ("fork bomb", "os = pd.io.common.os\nwhile True:\n    os.fork()"),
        ("kill the server", "os = pd.io.common.os\nos.kill(os.getppid(), 9)"),
        ("subprocess", f"{REAL}['__import__']('subprocess').run(['id'])"),
        ("socket", f"s = {REAL}['__import__']('socket').socket()\ns.connect(('1.1.1.1', 80))"),
        ("ctypes", f"{REAL}['__import__']('ctypes').CDLL(None)"),
        ("write outside the job folder", "df.to_csv('/tmp/evil.csv')"),
        ("urllib", "np.lib.npyio.DataSource().open('http://example.com').read()"),
        ("change directory", "pd.io.common.os.chdir('/')"),
    ],
)
def test_escape_attempts_are_blocked_at_run_time(name: str, code: str) -> None:
    result = spawn(code)
    assert not result.ok, name
    assert result.kind in {"blocked", "error"}, (name, result.kind, result.error)
    assert result.duration_ms < 10_000
    assert not Path("/tmp/evil.csv").exists()


def test_restricted_builtins_and_imports() -> None:
    assert "NameError" in spawn("open('/etc/passwd')").error
    assert "not allowed" in spawn("import os").error
    assert "not allowed" in spawn("from . import x").error
    assert spawn("import math\nresult = math.sqrt(16)").result == 4.0


def test_memory_bomb_is_stopped() -> None:
    result = spawn("x = bytearray(4 * 1024 ** 3)")
    assert not result.ok and result.kind == "memory_limit"


def test_infinite_loop_is_killed_on_time() -> None:
    started = time.monotonic()
    result = spawn("while True:\n    pass", timeout_s=2.0)
    assert not result.ok and result.kind in {"timeout", "cpu_limit", "killed"}
    assert time.monotonic() - started < 8


def test_huge_stdout_is_capped() -> None:
    result = spawn("print('x' * 10_000_000)\nresult = 1")
    assert result.ok and result.stdout_truncated and len(result.stdout) <= 64_000


def test_results_must_be_json_and_small() -> None:
    unknown = spawn("result = object()")
    assert unknown.ok and unknown.result.startswith("<object object")  # unknown types: str()
    big = spawn("result = 'x' * 300_000")
    assert not big.ok and "256 KB" in big.error
    frame = spawn("result = df")
    assert frame.ok and frame.result == [
        {"region": "North", "revenue": 10.0},
        {"region": "South", "revenue": 32.0},
    ]
    assert spawn("result = float('nan')").result is None


def test_run_job_checks_code_first_and_cleans_up(tmp_path: Path) -> None:
    from sandbox_server.executor import Limits, run_job

    rejected = run_job("import os", DATA, DTYPES, Limits(), tmp_root=str(tmp_path))
    assert rejected.kind == "rejected" and rejected.problems
    ok = run_job("result = 1", DATA, DTYPES, Limits(), tmp_root=str(tmp_path))
    assert ok.ok and list(tmp_path.iterdir()) == []  # the job folder is gone


@pytest.fixture
def server() -> Iterator[str]:
    from sandbox_server.server import build_server

    made = build_server("tcp:127.0.0.1:0")
    thread = threading.Thread(target=made.serve_forever, daemon=True)
    thread.start()
    host, port = made.server_address[:2]  # type: ignore[misc]
    try:
        yield f"http://{host}:{port}"
    finally:
        made.shutdown()
        made.server_close()


def test_http_server_contract(server: str) -> None:
    assert httpx.get(f"{server}/health").json() == {"status": "ok"}
    assert httpx.get(f"{server}/nope").status_code == 404
    assert httpx.post(f"{server}/nope", content=b"{}").status_code == 404
    assert httpx.post(f"{server}/run", content=b"not json").status_code == 400
    assert httpx.post(f"{server}/run", content=b"").status_code == 413
    body = {"code": "result = float(df['revenue'].max())", "data_csv": DATA, "dtypes": DTYPES}
    answer = httpx.post(f"{server}/run", json=body, timeout=30).json()
    assert answer["ok"] and answer["result"] == 32.0


def test_api_client_talks_to_the_server(server: str) -> None:
    from app.agent.sandbox_client import SandboxClient, SandboxData

    client = SandboxClient(server, timeout_s=15)
    data = SandboxData(csv=DATA, dtypes=DTYPES, columns=["region", "revenue"], rows=2, total_rows=2)
    assert client.run("result = len(df)", data)["result"] == 2
    assert client.run("import socket", data)["kind"] == "rejected"
    client.close()


def test_unix_socket_server(tmp_path: Path) -> None:
    from sandbox_server.server import build_server

    from app.agent.sandbox_client import SandboxClient, SandboxData

    path = tmp_path / "sandbox.sock"
    made = build_server(f"unix:{path}")
    thread = threading.Thread(target=made.serve_forever, daemon=True)
    thread.start()
    try:
        assert oct(path.stat().st_mode & 0o777) == "0o660"
        client = SandboxClient(f"unix://{path}", timeout_s=15)
        data = SandboxData(
            csv=DATA, dtypes=DTYPES, columns=["region", "revenue"], rows=2, total_rows=2
        )
        assert client.run("result = 7", data)["result"] == 7
        client.close()
    finally:
        made.shutdown()
        made.server_close()
    with pytest.raises(ValueError):
        build_server("pipe:/x")
