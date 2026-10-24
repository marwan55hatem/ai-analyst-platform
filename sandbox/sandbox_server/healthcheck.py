"""Container health check: GET /health over the unix socket (stdlib only)."""

import http.client
import os
import socket
import sys


class UnixConnection(http.client.HTTPConnection):
    """HTTPConnection that talks to a unix socket path."""

    def __init__(self, path: str, timeout: float = 3.0) -> None:
        """Remember the socket path."""
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        """Open the unix socket instead of TCP."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def main() -> int:
    """Return 0 when the server answers 200."""
    path = os.environ.get("SANDBOX_LISTEN", "unix:/ipc/sandbox.sock").partition(":")[2]
    connection = UnixConnection(path)
    try:
        connection.request("GET", "/health")
        return 0 if connection.getresponse().status == 200 else 1
    except OSError:
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
