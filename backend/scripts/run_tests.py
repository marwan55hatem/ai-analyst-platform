"""Run the backend tests serially (default) or in parallel with pytest-xdist.

TEST_WORKERS=1 (default) runs exactly one plain pytest process, as before.
TEST_WORKERS=N (N > 1) runs everything not marked `serial` on N xdist workers (one database,
storage folder and MLflow store per worker), then runs the `serial` tests in a second,
single-process pass. Coverage data from both passes is combined into one report
(terminal + coverage.json), which scripts/check_coverage.py then gates.
"""

import os
import subprocess
import sys

NO_TESTS_COLLECTED = 5
REPORT = ["--cov-report=term-missing", "--cov-report=json"]


def pytest(*args: str) -> int:
    """Run pytest in a child process and return its exit code."""
    return subprocess.call([sys.executable, "-m", "pytest", *args])


def workers() -> int:
    """Return the requested worker count (TEST_WORKERS, default 1)."""
    raw = os.environ.get("TEST_WORKERS", "1").strip() or "1"
    try:
        return max(1, int(raw))
    except ValueError:
        sys.exit(f"TEST_WORKERS must be a whole number, got {raw!r}")


def main(extra: list[str]) -> int:
    """Run the serial or the two-pass parallel suite and return the exit code."""
    count = workers()
    if count == 1:
        return pytest("--cov=app", *REPORT, *extra)
    print(f"run_tests: {count} xdist workers, then a serial pass for @pytest.mark.serial tests")
    parallel = pytest(
        "-n", str(count), "--dist", "loadfile", "-m", "not serial", "--cov=app", "--cov-report=",
        *extra,
    )  # fmt: skip
    serial = pytest("-m", "serial", "--cov=app", "--cov-append", *REPORT, *extra)
    if serial == NO_TESTS_COLLECTED:
        serial = 0
    return parallel or serial


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
