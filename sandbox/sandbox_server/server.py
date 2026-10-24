"""HTTP over a unix socket (default) or TCP: GET /health and POST /run.

The container runs with `network_mode: none`, so the only way in is the socket on the dedicated
`sandbox-ipc` volume that is mounted in the api and sandbox containers only.
"""

import json
import os
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from sandbox_server.executor import Limits, run_job

MAX_REQUEST_MB = int(os.environ.get("SANDBOX_MAX_REQUEST_MB", "40"))
CONCURRENCY = int(os.environ.get("SANDBOX_CONCURRENCY", "1"))
_slots = threading.BoundedSemaphore(CONCURRENCY)


class Handler(BaseHTTPRequestHandler):
    """Tiny JSON API; every response is JSON and errors carry a `kind`."""

    server_version = "sandbox/1"
    protocol_version = "HTTP/1.1"

    def address_string(self) -> str:
        """Unix sockets have no peer address."""
        return "ipc"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Log one line per request to stderr without request bodies."""
        sys.stderr.write(f"sandbox {self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")

    def do_GET(self) -> None:  # noqa: N802
        """Answer the health check."""
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"kind": "not_found", "error": "unknown path"})

    def do_POST(self) -> None:  # noqa: N802
        """Run one job if a slot is free."""
        if self.path != "/run":
            self._send(404, {"kind": "not_found", "error": "unknown path"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_REQUEST_MB * 1024 * 1024:
            self._send(413, {"kind": "too_large", "error": "request body missing or too large"})
            return
        body = self.rfile.read(length)
        try:
            job = json.loads(body)
            code, data_csv = str(job["code"]), str(job["data_csv"])
            dtypes = {str(k): str(v) for k, v in dict(job.get("dtypes") or {}).items()}
        except (ValueError, KeyError, TypeError):
            self._send(400, {"kind": "bad_request", "error": "invalid job body"})
            return
        if not _slots.acquire(timeout=2):
            self._send(429, {"kind": "busy", "error": "the sandbox is busy, try again"})
            return
        try:
            result = run_job(code, data_csv, dtypes, Limits.from_env())
        finally:
            _slots.release()
        self._send(200, result.to_dict())

    def _send(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class UnixHTTPServer(socketserver.ThreadingUnixStreamServer):
    """Threaded HTTP server bound to a unix socket file."""

    daemon_threads = True

    def get_request(self) -> tuple[Any, Any]:
        """Give handlers a (host, port)-like peer so BaseHTTPRequestHandler is happy."""
        request, _ = super().get_request()
        return request, ("ipc", 0)


class TCPHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Threaded TCP server, used by tests and the internal-network fallback."""

    daemon_threads = True


def build_server(listen: str) -> socketserver.BaseServer:
    """Create the server for `unix:/path` or `tcp:host:port`."""
    kind, _, address = listen.partition(":")
    if kind == "unix":
        if os.path.exists(address):
            os.unlink(address)
        old = os.umask(0o117)
        try:
            server: socketserver.BaseServer = UnixHTTPServer(address, Handler)
        finally:
            os.umask(old)
        os.chmod(address, 0o660)
        return server
    if kind == "tcp":
        host, _, port = address.rpartition(":")
        return TCPHTTPServer((host, int(port)), Handler)
    raise ValueError(f"SANDBOX_LISTEN must be unix:/path or tcp:host:port, got {listen!r}")


def main() -> None:
    """Serve forever on SANDBOX_LISTEN."""
    listen = os.environ.get("SANDBOX_LISTEN", "unix:/ipc/sandbox.sock")
    server = build_server(listen)
    sys.stderr.write(f"sandbox listening on {listen}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
