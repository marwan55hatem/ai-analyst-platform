"""Each sandbox layer on its own: the other layers are switched off and the attack is still contained.

Layers: (1) static check, (2) resource limits, (3) audit hook, (4) container isolation.
Layers 2 and 3 need Linux (`resource`, POSIX process groups); layer 4 needs Docker and the built
sandbox image (CI builds it; the same checks run there as the `sandbox-container-check` service).
"""

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from app.agent.code_check import check_code

SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
sys.path.insert(0, str(SANDBOX))

linux_only = pytest.mark.skipif(sys.platform == "win32", reason="the sandbox runs on Linux only")

# -- layer 1: static check alone -------------------------------------------------------------------

ATTACKS = {
    "import os": "import os\nos.system('id')",
    "import subprocess": "from subprocess import run\nrun(['id'])",
    "import socket": "import socket\nsocket.create_connection(('1.1.1.1', 80))",
    "dunder walk": "result = ().__class__.__bases__[0].__subclasses__()",
    "globals escape": "result = type(df).__init__.__globals__['__builtins__']",
    "dynamic import": "m = __import__('os')",
    "getattr trick": "f = getattr(df, 'to_csv')",
    "open": "open('/etc/passwd').read()",
    "exec": "exec('1')",
    "pandas file read": "pd.read_csv('/etc/passwd')",
    "module attribute": "pd.io.common.os.listdir('/')",
}


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_static_check_alone_refuses_the_attack(name: str) -> None:
    assert check_code(ATTACKS[name]), name


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_a_refused_attack_never_reaches_a_child_process(
    name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from sandbox_server import executor

    def must_not_run(*_: Any, **__: Any) -> None:
        raise AssertionError("the child process was started")

    monkeypatch.setattr(executor, "_spawn", must_not_run)
    result = executor.run_job(
        ATTACKS[name], "a\n1\n", {"a": "float64"}, executor.Limits(), tmp_root=str(tmp_path)
    )
    assert result.kind == "rejected" and list(tmp_path.iterdir()) == []


# -- layer 2: resource limits alone ----------------------------------------------------------------


def limited(code: str, **limits: Any) -> "subprocess.CompletedProcess[str]":
    """Run plain Python with only the rlimits: no static check, no hook, no restricted builtins."""
    from sandbox_server import executor

    settings = {"timeout_s": 2.0, "memory_mb": 300, "max_processes": 40, **limits}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=30,
        preexec_fn=executor._limit_child(executor.Limits(**settings)),  # noqa: PLW1509
        start_new_session=True,
    )


@linux_only
def test_rlimits_alone_stop_a_memory_bomb() -> None:
    done = limited("b = bytearray(2 * 1024 ** 3)\nprint('allocated')")
    assert "allocated" not in done.stdout and "MemoryError" in done.stderr


@linux_only
def test_rlimits_alone_stop_a_fork_bomb() -> None:
    done = limited(
        """
        import os, time
        kids = []
        try:
            for _ in range(400):
                pid = os.fork()
                if pid == 0:
                    time.sleep(20)
                    os._exit(0)
                kids.append(pid)
            print('forked', len(kids))
        except OSError:
            print('stopped', len(kids))
        for pid in kids:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        """
    )
    assert done.stdout.startswith("stopped"), done
    assert int(done.stdout.split()[1]) < 400


@linux_only
def test_rlimits_alone_stop_a_cpu_loop() -> None:
    done = limited("while True:\n    pass", timeout_s=1.0)
    assert done.returncode != 0  # SIGXCPU from RLIMIT_CPU, long before the 30 s test timeout


@linux_only
def test_rlimits_alone_stop_a_huge_file(tmp_path: Path) -> None:
    target = tmp_path / "big.bin"
    done = limited(
        f"""
        try:
            with open({str(target)!r}, 'wb') as handle:
                for _ in range(64):
                    handle.write(b'x' * 1024 * 1024)
                    handle.flush()
            print('written')
        except OSError:
            print('refused')
        """,
        max_file_mb=8,
    )
    assert done.stdout.strip() == "refused" and target.stat().st_size <= 8 << 20


@linux_only
def test_rlimits_alone_stop_a_file_descriptor_flood() -> None:
    done = limited(
        """
        files = []
        try:
            for _ in range(500):
                files.append(open('/dev/null'))
            print('opened', len(files))
        except OSError:
            print('refused', len(files))
        """,
        max_open_files=32,
    )
    assert done.stdout.startswith("refused"), done


# -- layer 3: audit hook alone ---------------------------------------------------------------------

DRIVER = """
import builtins, importlib.util, sys
spec = importlib.util.spec_from_file_location('runner', {runner!r})
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runner._safe_builtins = lambda: dict(vars(builtins))  # layer off: the code gets the real builtins
sys.exit(runner.main(sys.argv[1]))
"""

HOOK_ATTACKS = {
    "read /etc/passwd": "result = open('/etc/passwd').read()",
    "list another tenant's folder": "result = __import__('os').listdir('/app')",
    "write outside the job folder": "open('/tmp/layer-test-evil', 'w').write('x')",
    "move a file out of the job folder": (
        "import os\nopen('inside.txt', 'w').write('x')\nos.rename('inside.txt', '/tmp/layer-moved')"
    ),
    "subprocess": "__import__('subprocess').run(['id'])",
    "os.system": "__import__('os').system('id')",
    "fork": "__import__('os').fork()",
    "kill the parent": "import os\nos.kill(os.getppid(), 9)",
    "socket": "__import__('socket').socket().connect(('127.0.0.1', 9))",
    "ctypes": "__import__('ctypes').CDLL(None)",
    "sqlite file outside the job folder": "__import__('sqlite3').connect('/tmp/layer-test.db')",
    "pickle": "__import__('pickle').loads(b'cbuiltins\\neval\\n.')",
    "urllib": "__import__('urllib.request').request.urlopen('http://example.com')",
}


def hook_only(code: str, tmp_path: Path) -> dict[str, Any]:
    """Run the child with only the audit hook: no static check, no builtins limits, no rlimits."""
    from sandbox_server import executor

    workdir = tmp_path / "job"
    workdir.mkdir()
    (workdir / "data.csv").write_text("a\n1\n", encoding="utf-8")
    (workdir / "job.json").write_text(
        json.dumps({"code": code, "dtypes": {"a": "float64"}}), encoding="utf-8"
    )
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER.format(runner=str(executor.RUNNER)), encoding="utf-8")
    subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(driver), str(workdir)],
        capture_output=True,
        timeout=60,
        check=False,
    )
    output = workdir / "output.json"
    report: dict[str, Any] = json.loads(output.read_text()) if output.exists() else {}
    return report


@linux_only
def test_hook_alone_still_lets_normal_work_run(tmp_path: Path) -> None:
    report = hook_only("result = int(df['a'].sum())", tmp_path)
    assert report["ok"] is True and report["result"] == 1


@linux_only
@pytest.mark.parametrize("name", sorted(HOOK_ATTACKS))
def test_hook_alone_contains_the_attack(name: str, tmp_path: Path) -> None:
    report = hook_only(HOOK_ATTACKS[name], tmp_path)
    assert report.get("ok") is False and report.get("kind") == "blocked", (name, report)
    for leftover in ("/tmp/layer-test-evil", "/tmp/layer-moved", "/tmp/layer-test.db"):
        assert not Path(leftover).exists(), leftover


# -- layer 4: container isolation alone ------------------------------------------------------------

IMAGE = "ai-analyst-sandbox:dev"


def _image_available() -> bool:
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
    return probe.returncode == 0


@pytest.mark.skipif(not _image_available(), reason="needs Docker and the built sandbox image")
def test_container_alone_contains_raw_python() -> None:
    check = SANDBOX / "container_check.py"
    done = subprocess.run(  # noqa: S603
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            "10001:1000",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:size=64m,mode=1777",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "64",
            "-v",
            f"{check}:/check/container_check.py:ro",
            IMAGE,
            "python",
            "-I",
            "/check/container_check.py",
        ],  # fmt: skip
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0 and "FAIL" not in done.stdout, done.stdout + done.stderr
    assert done.stdout.count("PASS") >= 5
