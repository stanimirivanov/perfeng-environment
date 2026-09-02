"""Local functional fixture only: deterministic responses, not deterministic latency."""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MODES = {
    "ok",
    "cart-failure",
    "missing-order",
    "malformed-order",
    "search-failure",
    "preferences-failure",
}
MAX_BODY_BYTES = 16384


def handler_for(mode: str = "ok") -> type[BaseHTTPRequestHandler]:
    if mode not in MODES:
        raise ValueError("Unknown fixture mode")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, format: str, *args: object) -> None:
            # Do not log authorization headers, bodies, user IDs, or query strings.
            pass

        def respond(self, status: int, body: object, *, raw: bool = False) -> None:
            data = str(body).encode("utf-8") if raw else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(data)

        def read_body(self) -> dict[str, object] | None:
            if self.headers.get("Transfer-Encoding"):
                self.respond(400, {"error": "unsupported transfer encoding"})
                return None
            lengths = self.headers.get_all("Content-Length", [])
            if (
                len(lengths) != 1
                or len(lengths[0]) > 10
                or not lengths[0].isascii()
                or not lengths[0].isdigit()
            ):
                self.respond(400, {"error": "one content length required"})
                return None
            size = int(lengths[0])
            if size > MAX_BODY_BYTES:
                self.respond(413, {"error": "request too large"})
                return None
            try:
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ValueError("incomplete body")
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError("object required")
            except (ValueError, TimeoutError, RecursionError):
                self.respond(400, {"error": "JSON object required"})
                return None
            return value

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/healthz":
                self.respond(200, {"status": "ok"})
            elif path == "/":
                self.respond(
                    200, {"service": "perfeng-sample-api", "version": "0.2.0", "mode": mode}
                )
            elif path == "/api/v1/cart":
                self.respond(500 if mode == "cart-failure" else 200, {"cartId": "example-cart"})
            elif path == "/api/v1/search":
                self.respond(
                    500 if mode == "search-failure" else 200, {"results": ["fixture-result"]}
                )
            elif re.fullmatch(r"/api/v1/users/[^/]+", path):
                self.respond(200, {"id": "fixture-user"})
            else:
                self.respond(404, {"error": "not found"})

        def do_POST(self) -> None:
            if urlsplit(self.path).path != "/api/v1/checkout":
                self.respond(404, {"error": "not found"})
                return
            body = self.read_body()
            if body is None:
                return
            if body.get("cartId") != "example-cart":
                self.respond(400, {"error": "unknown fixture cart"})
            elif mode == "malformed-order":
                self.respond(201, "not-json", raw=True)
            else:
                self.respond(201, {} if mode == "missing-order" else {"orderId": "fixture-order"})

        def do_PUT(self) -> None:
            if not re.fullmatch(r"/api/v1/users/[^/]+/preferences", urlsplit(self.path).path):
                self.respond(404, {"error": "not found"})
                return
            if self.read_body() is not None:
                self.respond(500 if mode == "preferences-failure" else 200, {"updated": True})

    return Handler


def create_server(
    host: str = "127.0.0.1", port: int = 8080, mode: str = "ok"
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), handler_for(mode))


def main() -> None:
    host = os.environ.get("FIXTURE_HOST", "127.0.0.1")
    port = int(os.environ.get("FIXTURE_PORT", "8080"))
    mode = os.environ.get("FIXTURE_MODE", "ok")
    with create_server(host, port, mode) as server:
        print(f"Local sample API ready; mode={mode}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
