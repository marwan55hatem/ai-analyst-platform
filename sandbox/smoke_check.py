"""Stdlib-only smoke check of the sandbox over its unix socket (used in CI and the walkthrough).

Runs from a container with no network that shares only the sandbox-ipc volume. Prints one line
per check and exits non-zero when any check fails.
"""

import http.client
import json
import socket
import sys
import time

SOCKET = "/ipc/sandbox.sock"
DATA = "region,revenue\nNorth,10\nSouth,32\n"
DTYPES = {"region": "string", "revenue": "float64"}

# (name, code, expected kinds) - payloads that pass the static check test the run-time layers
CHECKS = [
    ("sum works", "result = float(df['revenue'].sum())", {None}),
    ("import os", "import os\nresult = os.listdir('/')", {"rejected"}),
    ("dunder walk", "result = ().__class__.__bases__[0].__subclasses__()", {"rejected"}),
    ("read /etc/passwd", "result = open('/etc/passwd').read()", {"rejected"}),
    (
        "read /etc/passwd at run time",
        "result = np.lib.npyio.DataSource().open('/etc/passwd').read()",
        {"blocked", "error"},
    ),
    (
        "network at run time",
        "result = np.lib.npyio.DataSource().open('http://example.com').read()",
        {"blocked", "error"},
    ),
    ("write outside tmp", "df.to_csv('/ipc/evil.csv')", {"blocked", "error"}),
    ("socket import", "import socket\nsocket.create_connection(('1.1.1.1', 80))", {"rejected"}),
    ("fork bomb", "import os\nwhile True:\n    os.fork()", {"rejected"}),
    ("memory bomb", "x = bytearray(4 * 1024 ** 3)", {"memory_limit"}),
    ("infinite loop", "while True:\n    pass", {"timeout", "cpu_limit", "killed"}),
]


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


def run(code: str) -> dict[str, object]:
    """POST one job and return the decoded response."""
    body = json.dumps({"code": code, "data_csv": DATA, "dtypes": DTYPES})
    connection = UnixConnection(SOCKET, timeout=40)
    try:
        connection.request("POST", "/run", body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        decoded: dict[str, object] = json.loads(response.read())
        return decoded
    finally:
        connection.close()


def main() -> int:
    """Run every check and report."""
    failures = 0
    for name, code, kinds in CHECKS:
        started = time.monotonic()
        result = run(code)
        seconds = time.monotonic() - started
        passed = result.get("kind") in kinds and (result.get("ok") is (None in kinds))
        failures += not passed
        detail = result.get("error") or result.get("result")
        print(f"{'PASS' if passed else 'FAIL'} {name:32} kind={result.get('kind')} "
              f"{seconds:5.1f}s {str(detail)[:70]}")
    try:
        with open("/ipc/evil.csv"):
            print("FAIL file written outside the job folder")
            failures += 1
    except OSError:
        print("PASS no file left on the ipc volume")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
