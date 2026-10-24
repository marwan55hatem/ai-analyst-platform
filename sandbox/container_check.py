"""Container layer on its own: raw Python with NO sandbox_server code, no audit hook, no rlimits.

Run inside the sandbox image with the sandbox service's runtime flags (see the `sandbox-container-check`
compose service). Every check tries something hostile with plain stdlib calls; the container (no
network, read-only root, non-root uid, no capabilities, pid and memory cgroup limits, no storage
volume) alone must stop it. Prints one line per check and exits non-zero when any check fails.
"""

import os
import socket
import subprocess
import sys

TMP_FILL_MB = 80  # the /tmp tmpfs is 64 MB


def refused(action) -> bool:  # type: ignore[no-untyped-def]
    """Return True when the action raises an OSError (including PermissionError)."""
    try:
        action()
    except OSError:
        return True
    return False


def no_network() -> bool:
    def connect() -> None:
        socket.create_connection(("1.1.1.1", 80), timeout=3)

    interfaces = sorted(os.listdir("/sys/class/net"))
    return refused(connect) and refused(lambda: socket.getaddrinfo("example.com", 80)) and (
        interfaces == ["lo"]
    )


def read_only_root() -> bool:
    return refused(lambda: open("/etc/container-check", "w")) and refused(
        lambda: open("/usr/local/evil.py", "w")
    )


def no_storage_volume() -> bool:
    mounts = [line.split()[4] for line in open("/proc/self/mountinfo").read().splitlines()]
    return not os.path.exists("/app/storage") and not any(
        m.startswith(("/app", "/ipc")) for m in mounts
    )


def not_root_and_no_caps() -> bool:
    status = dict(line.split(":\t", 1) for line in open("/proc/self/status").read().splitlines() if ":\t" in line)
    caps = int(status["CapEff"].strip(), 16)
    return os.getuid() != 0 and caps == 0 and status["NoNewPrivs"].strip() == "1" and refused(
        lambda: os.setuid(0)
    )


def tmpfs_is_capped() -> bool:
    path = "/tmp/fill.bin"
    try:
        with open(path, "wb") as handle:
            for _ in range(TMP_FILL_MB):
                handle.write(b"x" * 1024 * 1024)
                handle.flush()
        return False
    except OSError:
        return True
    finally:
        if os.path.exists(path):
            os.unlink(path)


def pid_limit() -> bool:
    children: list[int] = []
    stopped = False
    try:
        for _ in range(300):
            pid = os.fork()
            if pid == 0:
                signal_wait()
            children.append(pid)
    except OSError:
        stopped = True
    finally:
        for pid in children:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
    return stopped and len(children) < 300


def signal_wait() -> None:
    import time

    time.sleep(60)
    os._exit(0)


def memory_limit() -> bool:
    code = "b = b'x' * (1500 * 1024 * 1024); print(len(b))"
    process = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, timeout=60)
    return process.returncode != 0 and b"1572864000" not in process.stdout


CHECKS = [
    ("no network at all", no_network),
    ("root filesystem is read-only", read_only_root),
    ("no storage volume mounted", no_storage_volume),
    ("non-root, no capabilities, no-new-privileges", not_root_and_no_caps),
    ("/tmp tmpfs is capped", tmpfs_is_capped),
    ("pid limit stops a fork bomb", pid_limit),
    ("memory limit stops a memory bomb", memory_limit),
]


def main() -> int:
    """Run every check and report."""
    failures = 0
    for name, check in CHECKS:
        try:
            passed = bool(check())
        except Exception as exc:  # noqa: BLE001 - a crash is a failed check
            print(f"FAIL {name:48} {type(exc).__name__}: {exc}")
            failures += 1
            continue
        failures += not passed
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
