"""Child process that runs one job; started by path with `python -I runner.py <workdir>`.

It must stay self-contained (no imports from this package) because `-I` keeps the script's folder
off sys.path. Order matters: load the data and the libraries first, then install the audit hook,
then run the user's code with restricted builtins. Output goes to `output.json`, opened before the
hook is installed.
"""

import builtins
import io
import json
import math
import sys
import types
from typing import Any

MAX_RESULT_CHARS = 256_000
STDOUT_LIMIT = 64_000
ALLOWED_IMPORTS = frozenset(
    {
        "pandas",
        "numpy",
        "scipy",
        "statsmodels",
        "sklearn",
        "math",
        "statistics",
        "json",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "re",
        "decimal",
        "fractions",
        "random",
        "operator",
        "string",
        "typing",
        "dataclasses",
    }
)
BLOCKED_EVENT_PREFIXES = (
    "subprocess.",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.fork",
    "os.forkpty",
    "os.kill",
    "os.killpg",
    "os.putenv",
    "os.unsetenv",
    "os.chdir",
    "os.chmod",
    "os.chown",
    "os.link",
    "os.symlink",
    "os.truncate",
    "os.setxattr",
    "os.removexattr",
    "signal.",
    "socket.",
    "ctypes.",
    "winreg.",
    "urllib.",
    "http.",
    "ftplib.",
    "smtplib.",
    "telnetlib.",
    "webbrowser.",
    "pty.",
    "fcntl.",
    "mmap.",
    "resource.setrlimit",
    "sys.setrecursionlimit",
    "pickle.",
    "sqlite3.",
    "shutil.",
    "builtins.input",
    "pdb.",
    "cpython.run_",
)
PATH_EVENTS = {
    "open",
    "os.listdir",
    "os.scandir",
    "os.walk",
    "glob.glob",
    "os.remove",
    "os.rename",
    "os.rmdir",
    "os.mkdir",
    "os.utime",
}
WRITE_EVENTS = {"os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.utime"}
TWO_PATH_EVENTS = {"os.rename"}  # args are (source, destination, ...): both must be allowed


class SandboxViolation(Exception):
    """Raised by the audit hook when the code tries something outside the sandbox."""


def _install_hook(workdir: str, read_roots: tuple[str, ...]) -> None:
    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event.startswith(BLOCKED_EVENT_PREFIXES):
            raise SandboxViolation(f"blocked operation: {event}")
        if event not in PATH_EVENTS or not args:
            return
        writing = event in WRITE_EVENTS or _is_write_open(event, args)
        paths = args[:2] if event in TWO_PATH_EVENTS else args[:1]
        for item in paths:
            _check_path(item, workdir, read_roots, writing)

    sys.addaudithook(hook)


def _check_path(
    path: Any, workdir: str, read_roots: tuple[str, ...], writing: bool
) -> None:
    """Allow a path inside the job folder, or a read under the Python install; else raise."""
    if isinstance(path, int) or path is None:
        return
    text = path.decode(errors="replace") if isinstance(path, bytes) else str(path)
    real = _normalise(text)
    if real.startswith(workdir) or (not writing and real.startswith(read_roots)):
        return
    raise SandboxViolation(f"blocked file access: {text}")


def _normalise(path: str) -> str:
    import os.path

    return os.path.realpath(path)


def _is_write_open(event: str, args: tuple[Any, ...]) -> bool:
    """Return whether an `open` event writes (args are path, mode string or None, flags)."""
    import os

    if event != "open" or len(args) < 2:
        return False
    mode = args[1]
    if isinstance(mode, str) and any(flag in mode for flag in "wax+"):
        return True
    flags = args[2] if len(args) > 2 else None
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    return isinstance(flags, int) and bool(flags & write_flags)


def _restricted_import(
    name: str,
    globals_: Any = None,
    locals_: Any = None,
    fromlist: Any = (),
    level: int = 0,
) -> types.ModuleType:
    if level or name.split(".")[0] not in ALLOWED_IMPORTS:
        raise ImportError(f"import of '{name}' is not allowed in the sandbox")
    return __import__(name, globals_, locals_, fromlist, level)


def _safe_builtins() -> dict[str, Any]:
    blocked = {
        "open",
        "eval",
        "exec",
        "compile",
        "input",
        "breakpoint",
        "help",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "memoryview",
        "exit",
        "quit",
        "__import__",
        "__loader__",
        "__spec__",
        "__build_class__",
    }
    safe = {k: v for k, v in vars(builtins).items() if k not in blocked}
    safe["__import__"] = _restricted_import
    safe["__build_class__"] = builtins.__build_class__
    return safe


class _CappedWriter(io.TextIOBase):
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.size = 0
        self.truncated = False

    def write(self, text: str) -> int:
        if self.size < STDOUT_LIMIT:
            room = STDOUT_LIMIT - self.size
            self.parts.append(text[:room])
            self.size += min(len(text), room)
        if len(text) and self.size >= STDOUT_LIMIT:
            self.truncated = True
        return len(text)

    def value(self) -> str:
        return "".join(self.parts)


def _jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        raise ValueError("result is nested too deeply")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in value]
    for converter in ("to_dict", "tolist", "item", "isoformat"):
        method = getattr(value, converter, None)
        if callable(method):
            converted = method(orient="records") if _is_frame(value, converter) else method()
            return _jsonable(converted, depth + 1)
    return str(value)


def _is_frame(value: Any, converter: str) -> bool:
    return converter == "to_dict" and type(value).__name__ == "DataFrame"


def _load(workdir: str) -> tuple[dict[str, Any], Any]:
    with open(f"{workdir}/job.json", encoding="utf-8") as handle:
        job: dict[str, Any] = json.load(handle)
    import numpy as np
    import pandas as pd

    frame = pd.read_csv(
        f"{workdir}/data.csv",
        dtype={k: v for k, v in job.get("dtypes", {}).items() if v != "datetime"},
        parse_dates=[k for k, v in job.get("dtypes", {}).items() if v == "datetime"],
        keep_default_na=True,
    )
    job["_np"] = np
    job["_pd"] = pd
    return job, frame


def main(workdir: str) -> int:
    """Run the job in `workdir`; always write output.json and return 0 unless setup failed."""
    import os.path

    # -I ignores PYTHONDONTWRITEBYTECODE; lazy imports must not try to write .pyc files
    sys.dont_write_bytecode = True
    workdir = os.path.realpath(workdir)
    output = open(f"{workdir}/output.json", "w", encoding="utf-8")  # noqa: SIM115
    job, frame = _load(workdir)
    read_roots = tuple(
        {os.path.realpath(p) for p in (sys.prefix, sys.base_prefix, sys.exec_prefix)}
        | {os.path.realpath(p) for p in sys.path if p}
    )
    np, pd = job.pop("_np"), job.pop("_pd")
    stdout = _CappedWriter()
    scope: dict[str, Any] = {
        "__builtins__": _safe_builtins(),
        "__name__": "__sandbox__",
        "pd": pd,
        "np": np,
        "df": frame,
    }
    report: dict[str, Any] = {"ok": True, "error": None, "kind": None}
    _install_hook(workdir + os.sep, read_roots)
    real_stdout = sys.stdout
    sys.stdout = stdout
    try:
        exec(compile(job["code"], "<sandbox>", "exec"), scope)  # noqa: S102
    except MemoryError:
        report = {"ok": False, "error": "the code ran out of memory", "kind": "memory_limit"}
    except SandboxViolation as exc:
        report = {"ok": False, "error": str(exc), "kind": "blocked"}
    except RecursionError:
        report = {"ok": False, "error": "maximum recursion depth exceeded", "kind": "error"}
    except BaseException as exc:  # noqa: BLE001 - report every failure of user code
        report = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000], "kind": "error"}
    finally:
        sys.stdout = real_stdout
    report["stdout"] = stdout.value()
    report["stdout_truncated"] = stdout.truncated
    try:
        result = _jsonable(scope.get("result"))
        chart = _jsonable(scope.get("chart"))
        encoded = json.dumps({"result": result, "chart": chart}, allow_nan=False)
    except (ValueError, TypeError, MemoryError, SandboxViolation) as exc:
        report.update(ok=False, error=f"result is not JSON-serialisable: {exc}"[:500], kind="error")
        encoded = json.dumps({"result": None, "chart": None})
    if len(encoded) > MAX_RESULT_CHARS:
        report.update(ok=False, error="result is larger than 256 KB", kind="error")
        encoded = json.dumps({"result": None, "chart": None})
    report.update(json.loads(encoded))
    output.write(json.dumps(report, allow_nan=False))
    output.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
