"""Sandbox isolation: the compose runtime flags behind each guarantee, and the live suite.

The static half always runs: it reads docker-compose.yml and checks the sandbox service has the
flags docs/agent-design.md promises. The live half runs `sandbox/isolation_check.py` in both modes
against the real container and only runs with SANDBOX_IT=1 (CI sets it after building the image).
"""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _service(name: str) -> dict[str, Any]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service: dict[str, Any] = compose["services"][name]
    return service


def test_sandbox_has_no_network_and_a_read_only_root() -> None:
    sandbox = _service("sandbox")
    assert sandbox["network_mode"] == "none"
    assert sandbox["read_only"] is True
    assert sandbox["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in sandbox["security_opt"]
    assert not str(sandbox["user"]).startswith("0")


def test_sandbox_mounts_only_the_socket_volume() -> None:
    assert _service("sandbox")["volumes"] == ["sandbox-ipc:/ipc"]


def test_sandbox_environment_has_no_app_secrets() -> None:
    environment = _service("sandbox")["environment"]
    assert all(key.startswith("SANDBOX_") for key in environment)


def test_sandbox_memory_limit_includes_swap() -> None:
    """Without memswap_limit Docker adds as much swap again, so 512 MB would really be 1 GB."""
    sandbox = _service("sandbox")
    assert sandbox["mem_limit"] == "512m"
    assert sandbox.get("memswap_limit") == sandbox["mem_limit"]


def test_sandbox_cpu_and_pid_limits() -> None:
    sandbox = _service("sandbox")
    assert float(sandbox["cpus"]) == 1.0
    assert sandbox["pids_limit"] == 64


def test_only_the_api_mounts_the_socket_volume() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        mounts = [str(v) for v in service.get("volumes", [])]
        if any(v.startswith("sandbox-ipc:") for v in mounts):
            assert name in {"api", "sandbox"} or "ci" in service.get("profiles", []), name


@pytest.mark.skipif(os.environ.get("SANDBOX_IT") != "1", reason="set SANDBOX_IT=1 with docker")
@pytest.mark.parametrize("service", ["sandbox-isolation-check", "sandbox-isolation-direct"])
def test_live_isolation_suite(service: str) -> None:
    process = subprocess.run(  # noqa: S603 - fixed argv
        ["docker", "compose", "--profile", "ci", "run", "--rm", service],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert process.returncode == 0, process.stdout[-4000:] + process.stderr[-2000:]
    assert "| FAIL |" not in process.stdout
