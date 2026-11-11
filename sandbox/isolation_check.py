"""Isolation suite: one automated check per documented sandbox guarantee (docs/agent-design.md).

Two modes, both run against the real sandbox image with the real runtime flags:

* `--socket` (all layers): runs from a client container that shares only the sandbox-ipc volume and
  posts jobs to the running sandbox server over its unix socket, exactly as the api does.
* `--direct` (static check skipped): runs inside a container built from the sandbox image with the
  sandbox service's flags and calls the executor with the AST guard switched off, so each check
  proves the deeper layers (restricted builtins and imports, audit hook, rlimits, container) on
  their own.

Prints a markdown table `guarantee | layer | expected | measured | result` and exits non-zero when
any check fails. Stdlib only in socket mode.
"""

import http.client
import json
import os
import socket
import sys
import time
from collections.abc import Callable
from typing import Any

SOCKET = "/ipc/sandbox.sock"
DATA_A = "customer,revenue\nTENANT-A-SECRET,10\nTENANT-A-OTHER,32\n"
DATA_B = "customer,revenue\nb-1,5\nb-2,7\nb-3,9\n"
DTYPES = {"customer": "string", "revenue": "float64"}
SECRETS = (
    "JWT_SECRET",
    "DATABASE_URL",
    "GROQ_API_KEY",
    "DEEPSEEK_API_KEY",
    "PSEUDONYM_SECRET",
    "REDIS_URL",
    "MLFLOW_TRACKING_URI",
    "EMAIL_API_KEY",
)
REJECTED = {"rejected"}
STOPPED = {"blocked", "error"}
ROWS: list[tuple[str, str, str, str, bool]] = []


class UnixConnection(http.client.HTTPConnection):
    """HTTPConnection over a unix socket."""

    def __init__(self, path: str, timeout: float) -> None:
        """Remember the socket path."""
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        """Connect to the unix socket."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def _http(method: str, path: str, body: str | None = None) -> tuple[int, dict[str, Any]]:
    connection = UnixConnection(SOCKET, timeout=60)
    try:
        headers = {"Content-Type": "application/json"} if body else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        connection.close()


def socket_run(code: str, data: str = DATA_B) -> dict[str, Any]:
    """Post one job to the running sandbox server."""
    _, body = _http("POST", "/run", json.dumps({"code": code, "data_csv": data, "dtypes": DTYPES}))
    return body


def direct_run(code: str, data: str = DATA_B) -> dict[str, Any]:
    """Run one job through the executor with the static check switched off."""
    from sandbox_server import executor

    executor.guard.check = lambda _code: []  # type: ignore[assignment]
    return executor.run_job(code, data, DTYPES, executor.Limits.from_env()).to_dict()


def record(guarantee: str, layer: str, expected: str, measured: str, passed: bool) -> None:
    """Keep one table row."""
    ROWS.append((guarantee, layer, expected, measured, passed))


def job_check(
    run: Callable[[str], dict[str, Any]],
    guarantee: str,
    layer: str,
    code: str,
    kinds: set[str | None],
    *,
    max_seconds: float | None = None,
    extra: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Run code and compare the outcome kind (and optional time limit and extra predicate)."""
    started = time.monotonic()
    result = run(code)
    seconds = time.monotonic() - started
    kind = result.get("kind")
    passed = kind in kinds and (result.get("ok") is (None in kinds))
    if max_seconds is not None:
        passed = passed and seconds <= max_seconds
    if extra is not None:
        passed = passed and extra(result)
    detail = str(result.get("error") or result.get("result") or "")[:60].replace("|", "/")
    expected = " or ".join(sorted(str(k) if k else "ok" for k in kinds))
    if max_seconds is not None:
        expected += f" within {max_seconds:g} s"
    record(
        guarantee, layer, expected, f"{kind or 'ok'} in {seconds:.1f} s {detail}".strip(), passed
    )
    return result


def healthy_after(run: Callable[[str], dict[str, Any]], layer: str, after: str) -> None:
    """Check a trivial job succeeds right after a crash or hang."""
    started = time.monotonic()
    result = run("result = 1 + 1")
    seconds = time.monotonic() - started
    ok = result.get("ok") is True and result.get("result") == 2 and seconds <= 3
    if layer == "all":
        status, body = _http("GET", "/health")
        ok = ok and status == 200 and body.get("status") == "ok"
    record(
        f"healthy after {after}",
        layer,
        "health ok and 1+1 = 2 within 3 s",
        f"result {result.get('result')} in {seconds:.1f} s",
        ok,
    )


def socket_checks() -> None:
    """Every guarantee through all layers, as the api calls it."""
    run, layer = socket_run, "all"
    job_check(
        run,
        "no network",
        layer,
        "result = np.lib.npyio.DataSource().open('http://1.1.1.1').read()",
        STOPPED,
    )
    job_check(
        run,
        "no network (import socket)",
        layer,
        "import socket\nsocket.create_connection(('1.1.1.1', 53))",
        REJECTED,
    )
    job_check(run, "read-only root filesystem", layer, "df.to_csv('/usr/evil.csv')", STOPPED)
    job_check(
        run,
        "no storage volume",
        layer,
        "result = np.lib.npyio.DataSource().open('/app/storage/x').read()",
        STOPPED,
    )
    job_check(
        run,
        "no app environment (import os)",
        layer,
        "import os\nresult = dict(os.environ)",
        REJECTED,
    )
    job_check(run, "memory limit", layer, "x = bytearray(600 * 1024 ** 2)", {"memory_limit"})
    healthy_after(run, layer, "out of memory")
    job_check(
        run,
        "CPU and time limit",
        layer,
        "while True:\n    pass",
        {"timeout", "cpu_limit", "killed"},
        max_seconds=20,
    )
    healthy_after(run, layer, "a hang")
    job_check(run, "process limit (fork)", layer, "import os\nwhile True:\n    os.fork()", REJECTED)
    job_check(
        run,
        "file size limit",
        layer,
        "pd.DataFrame({'a': ['x' * 1000] * 41000}).to_csv('big.csv')",
        {"error", "killed", "blocked"},
    )
    healthy_after(run, layer, "a file size kill")
    for name in (
        "open('/etc/passwd')",
        "eval('1')",
        "exec('x=1')",
        "__import__('os')",
        "getattr(df, 'x')",
        "compile('1','f','eval')",
    ):
        job_check(run, f"blocked builtin {name.split('(')[0]}", layer, f"result = {name}", REJECTED)
    for module in (
        "os",
        "sys",
        "subprocess",
        "socket",
        "ctypes",
        "pickle",
        "shutil",
        "importlib",
        "urllib.request",
    ):
        job_check(run, f"blocked module {module}", layer, f"import {module}", REJECTED)
    job_check(run, "crash in user code", layer, "def f():\n    return f()\nf()", {"error"})
    healthy_after(run, layer, "a crash")
    job_check(
        run,
        "stdout cap 64 KB",
        layer,
        "print('x' * 200000)",
        {None},
        extra=lambda r: bool(r.get("stdout_truncated")) and len(str(r.get("stdout"))) <= 64_000,
    )
    job_check(run, "result cap 256 KB", layer, "result = 'x' * 300000", {"error"})
    job_check(
        run,
        "tenant data only (B sees B rows)",
        layer,
        "result = df['customer'].tolist()",
        {None},
        extra=lambda r: r.get("result") == ["b-1", "b-2", "b-3"],
    )


def direct_checks() -> None:
    """Run the same guarantees with the static check off: deeper layers must hold alone."""
    run, layer = direct_run, "no static check"
    os_ = "os = pd.io.common.os\n"
    job_check(
        run,
        "no network",
        layer,
        "import socket\nsocket.create_connection(('1.1.1.1', 53))",
        STOPPED,
    )
    job_check(
        run,
        "no network (pandas internals)",
        layer,
        "s = pd.io.common.os\nresult = pd.io.common.urlopen('http://1.1.1.1')",
        STOPPED,
    )
    job_check(run, "read-only root filesystem", layer, "df.to_csv('/usr/evil.csv')", STOPPED)
    job_check(
        run,
        "no storage volume",
        layer,
        os_ + "result = [os.path.exists(p) for p in ('/app', '/app/storage', '/storage')]",
        {None},
        extra=lambda r: r.get("result") == [False, False, False],
    )
    job_check(
        run,
        "no app environment",
        layer,
        os_ + "result = sorted(os.environ)",
        {None},
        extra=lambda r: isinstance(r.get("result"), list) and not set(r["result"]) & set(SECRETS),
    )
    job_check(run, "process limit (fork)", layer, os_ + "while True:\n    os.fork()", STOPPED)
    job_check(
        run, "memory limit", layer, "x = bytearray(600 * 1024 ** 2)", {"memory_limit", "error"}
    )
    job_check(
        run,
        "time limit (blocking read, no CPU)",
        layer,
        os_ + "r, w = os.pipe()\nos.read(r, 1)",
        {"timeout", "blocked"},
        max_seconds=20,
    )
    job_check(
        run,
        "file size limit 32 MB",
        layer,
        "pd.DataFrame({'a': ['x' * 1000] * 41000}).to_csv('big.csv')",
        {"error", "killed"},
    )
    for name in ("open('/etc/passwd')", "eval('1')", "exec('x=1')", "__import__('os')"):
        job_check(
            run, f"blocked builtin {name.split('(')[0]}", layer, f"result = {name}", {"error"}
        )
    for module in ("os", "socket", "subprocess", "ctypes"):
        job_check(run, f"blocked module {module}", layer, f"import {module}", {"error"})
    tenant_separation(run, layer, os_)
    stdout_tmp = os.listdir("/tmp")
    record(
        "no job folder left in /tmp",
        layer,
        "/tmp has no job-* entries",
        f"{sorted(stdout_tmp)}",
        not any(n.startswith("job-") for n in stdout_tmp),
    )


def tenant_separation(run: Callable[..., dict[str, Any]], layer: str, os_: str) -> None:
    """Job A leaves a marker; job B (other tenant) must not see it or A's data."""
    first = run(os_ + "df.to_csv('marker.csv')\nresult = os.getcwd()", DATA_A)
    folder = str(first.get("result") or "")
    record(
        "job folder removed after the job",
        layer,
        "A's folder is gone",
        f"{folder} exists={os.path.exists(folder)}",
        bool(folder) and not os.path.exists(folder),
    )
    second = run(
        os_ + f"result = np.lib.npyio.DataSource().open('{folder}/marker.csv').read()", DATA_B
    )
    record(
        "other tenant's job folder unreadable",
        layer,
        "blocked or error",
        str(second.get("kind")),
        second.get("kind") in STOPPED,
    )
    listing = run(os_ + "result = os.listdir('/tmp')", DATA_B)
    record(
        "cannot list other jobs in /tmp",
        layer,
        "blocked",
        str(listing.get("kind")),
        listing.get("kind") in STOPPED,
    )
    seen = run("result = df['customer'].tolist()", DATA_B)
    record(
        "tenant B sees only B rows",
        layer,
        "['b-1', 'b-2', 'b-3']",
        str(seen.get("result")),
        seen.get("result") == ["b-1", "b-2", "b-3"],
    )


def main(argv: list[str]) -> int:
    """Run the chosen mode and print the table."""
    mode = argv[1] if len(argv) > 1 else "--socket"
    if mode == "--socket":
        socket_checks()
    elif mode == "--direct":
        direct_checks()
    else:
        print(f"unknown mode {mode}", file=sys.stderr)
        return 2
    print("| guarantee | layer | expected | measured | result |")
    print("|---|---|---|---|---|")
    for guarantee, layer, expected, measured, passed in ROWS:
        print(
            f"| {guarantee} | {layer} | {expected} | {measured} | {'PASS' if passed else 'FAIL'} |"
        )
    failures = sum(not row[4] for row in ROWS)
    print(f"\n{len(ROWS) - failures}/{len(ROWS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
