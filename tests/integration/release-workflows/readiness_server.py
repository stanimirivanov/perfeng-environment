"""Synthetic HTTP peer for the unchanged readiness verifier in disposable CI."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

MODES = {"healthy", "readyz-unavailable", "metrics-redirect", "metrics-invalid"}
PATHS = ("/livez", "/readyz", "/metrics", "/redirect-target")


class FixtureServer(HTTPServer):
    """Keep mode and probe counts on one disposable server instance."""

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, Handler)
        self.mode = "healthy"
        self.counts = dict.fromkeys(PATHS, 0)


class Handler(BaseHTTPRequestHandler):
    server: FixtureServer

    def log_message(self, format: str, *args: object) -> None:
        """Do not retain arbitrary request/error context in fixture logs."""

    def reply(self, status: int, body: bytes, location: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        mode = self.path.removeprefix("/fixture-mode/")
        if self.client_address[0] != "127.0.0.1":
            self.reply(403, b"")
        elif not self.path.startswith("/fixture-mode/") or mode not in MODES:
            self.reply(400, b"")
        else:
            self.server.mode = mode
            self.server.counts = dict.fromkeys(PATHS, 0)
            self.reply(200, b"")

    def do_GET(self) -> None:
        if self.path == "/fixture-state":
            if self.client_address[0] != "127.0.0.1":
                self.reply(403, b"")
            else:
                self.reply(200, json.dumps(self.server.counts).encode())
            return
        if self.path not in PATHS:
            self.reply(404, b"")
            return
        self.server.counts[self.path] += 1
        if self.path == "/readyz" and self.server.mode == "readyz-unavailable":
            self.reply(503, b"unavailable\n")
        elif self.path == "/metrics" and self.server.mode == "metrics-redirect":
            self.reply(302, b"", "/redirect-target")
        elif self.path == "/metrics" and self.server.mode == "metrics-invalid":
            self.reply(200, b"# perfeng_control_plane_ready 1\n")
        else:
            body = b"perfeng_control_plane_ready 1\n" if self.path == "/metrics" else b"ok\n"
            self.reply(200, body)


if __name__ == "__main__":
    with FixtureServer(("0.0.0.0", 8080)) as server:
        server.serve_forever()
