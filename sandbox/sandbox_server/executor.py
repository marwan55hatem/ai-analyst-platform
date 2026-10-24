"""Runs one job in a fresh child process with resource limits and a wall-clock kill."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sandbox_server import guard

RUNNER = Path(__file__).with_name("runner.py")
STDERR_LIMIT = 8_000


@dataclass(frozen=True)
class Limits:
    """Per-job limits; the container adds its own memory, CPU and pid limits on top."""

    timeout_s: float = 15.0
    memory_mb: int = 400
    max_processes: int = 48
    max_file_mb: int = 32
    max_open_files: int = 64

    @classmethod
    def from_env(cls) -> "Limits":
        """Read SANDBOX_* overrides from the environment."""
        return cls(
            timeout_s=float(os.environ.get("SANDBOX_TIMEOUT_S", "15")),
            memory_mb=int(os.environ.get("SANDBOX_CHILD_MEMORY_MB", "400")),
            max_processes=int(os.environ.get("SANDBOX_MAX_PROCESSES", "48")),
        )


@dataclass
class JobResult:
    """What the API receives back for one job."""

    ok: bool
    kind: str | None = None
    error: str | None = None
    stdout: str = ""
    stdout_truncated: bool = False
    result: Any = None
    chart: Any = None
    duration_ms: int = 0
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON body for the response."""
        return {
            "ok": self.ok,
            "kind": self.kind,
            "error": self.error,
            "stdout": self.stdout,
            "stdout_truncated": self.stdout_truncated,
            "result": self.result,
            "chart": self.chart,
            "duration_ms": self.duration_ms,
            "problems": self.problems,
        }


def run_job(
    code: str, data_csv: str, dtypes: dict[str, str], limits: Limits, tmp_root: str | None = None
) -> JobResult:
    """Check the code, run it on the data in a temp folder and always remove the folder."""
    started = time.monotonic()
    problems = guard.check(code)
    if problems:
        return JobResult(
            ok=False, kind="rejected", error="the code failed the safety check", problems=problems
        )
    workdir = tempfile.mkdtemp(prefix="job-", dir=tmp_root)
    try:
        Path(workdir, "data.csv").write_text(data_csv, encoding="utf-8", newline="\n")
        job = {"code": code, "dtypes": dtypes}
        Path(workdir, "job.json").write_text(json.dumps(job), encoding="utf-8", newline="\n")
        result = _spawn(workdir, limits)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def _limit_child(limits: Limits) -> Any:
    import resource

    def apply() -> None:
        memory = limits.memory_mb * 1024 * 1024
        cpu = int(limits.timeout_s) + 1
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes))
        size = limits.max_file_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
        resource.setrlimit(resource.RLIMIT_NOFILE, (limits.max_open_files, limits.max_open_files))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


def _child_env(workdir: str) -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": workdir,
        "TMPDIR": workdir,
        "LANG": "C.UTF-8",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _spawn(workdir: str, limits: Limits) -> JobResult:
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-I", str(RUNNER), workdir],
        cwd=workdir,
        env=_child_env(workdir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=_limit_child(limits),  # noqa: PLW1509 - single-threaded child setup only
        start_new_session=True,
    )
    stderr = _drain(process)
    try:
        process.wait(timeout=limits.timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        return JobResult(
            ok=False, kind="timeout", error=f"the code ran longer than {limits.timeout_s:g} s"
        )
    finally:
        _kill_group(process)
    return _read_output(workdir, process.returncode, "".join(stderr))


def _drain(process: subprocess.Popen[bytes]) -> list[str]:
    """Read both pipes in threads (capped) so a chatty child can never block on a full pipe."""
    kept: list[str] = []

    def reader(stream: Any, keep: bool) -> None:
        size = 0
        for chunk in iter(lambda: stream.read(4096), b""):
            if keep and size < STDERR_LIMIT:
                kept.append(chunk[: STDERR_LIMIT - size].decode(errors="replace"))
                size += len(chunk)

    for stream, keep in ((process.stdout, False), (process.stderr, True)):
        threading.Thread(target=reader, args=(stream, keep), daemon=True).start()
    return kept


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _read_output(workdir: str, returncode: int | None, stderr: str) -> JobResult:
    path = Path(workdir, "output.json")
    report: dict[str, Any] = {}
    if path.exists():
        try:
            report = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            report = {}
    if report:
        return JobResult(
            ok=bool(report.get("ok")),
            kind=report.get("kind"),
            error=report.get("error"),
            stdout=str(report.get("stdout", "")),
            stdout_truncated=bool(report.get("stdout_truncated")),
            result=report.get("result"),
            chart=report.get("chart"),
        )
    return JobResult(ok=False, kind=_kind_for(returncode, stderr), error=_error_for(stderr))


def _kind_for(returncode: int | None, stderr: str) -> str:
    if returncode in (-signal.SIGXCPU, -signal.SIGKILL):
        return "cpu_limit" if returncode == -signal.SIGXCPU else "killed"
    if "MemoryError" in stderr:
        return "memory_limit"
    return "error"


def _error_for(stderr: str) -> str:
    if "MemoryError" in stderr:
        return "the job ran out of memory while loading"
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return lines[-1][:500] if lines else "the job stopped without a result"
