"""Explicit isolated Linux Caddy TLS smoke; no cluster, publication or trust-store mutation."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import ssl
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from scripts import native_connectivity as connectivity
from scripts import native_gateway_host as host
from scripts import native_gateway_tool as tool
from scripts.native_gateway import wait_port
from scripts.native_gateway_processes import ProcessScope


def smoke(binary: Path, root: Path = tool.ROOT) -> None:
    """Exercise actual version/provenance, TLS, Host preservation and isolated process cleanup."""
    tool.metadata(binary)
    observed: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed.append((self.headers.get("Host", ""), self.path))
            self.send_response(403 if self.path == "/denied" else 200)
            self.end_headers()
            self.wfile.write(b"<Code>AccessDenied</Code>" if self.path == "/denied" else b"ready\n")

        def log_message(self, format: str, *args: object) -> None:
            pass

    servers: list[ThreadingHTTPServer] = []
    scope = ProcessScope()
    try:
        for _ in range(3):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        ports: list[int] = []
        reservations: list[socket.socket] = []
        try:
            for _ in range(3):
                reservation = socket.socket()
                reservation.bind(("127.0.0.1", 0))
                reservations.append(reservation)
                ports.append(reservation.getsockname()[1])
        finally:
            for reservation in reservations:
                reservation.close()
        with tempfile.TemporaryDirectory(prefix="perfeng-gateway-smoke-") as temporary:
            isolated = Path(temporary).resolve()
            host.secure_permissions(isolated)
            (isolated / "native-gateway").mkdir()
            # An isolated delivery fixture exercises installation, not published-asset authentication.
            selected = {
                "schemaVersion": 1,
                "platform": "linux/amd64",
                "caddyVersion": "v2.11.7",
                "goVersion": tool.GO_VERSION,
                "release": {
                    "tag": "native-gateway-" + "a" * 40,
                    "assetId": 1,
                    "sha256": tool.digest(binary),
                    "sizeBytes": binary.stat().st_size,
                    "sourceRevision": "a" * 40,
                },
            }
            (isolated / "native-gateway/serving-release.json").write_text(
                json.dumps(selected), encoding="utf-8"
            )
            with patch.object(
                tool, "download", side_effect=lambda _, target: shutil.copyfile(binary, target)
            ):
                installed = tool.setup(isolated)
                if tool.setup(isolated) != installed:
                    raise ValueError("Repeat setup selected another executable")
            binary = installed
            configuration = (root / "native-gateway/Caddyfile").read_text(encoding="utf-8")
            for original, replacement in zip(
                (8443, 9443, 10443, 18080, 18333, 18081),
                (*ports, *(server.server_port for server in servers)),
                strict=True,
            ):
                configuration = configuration.replace(f":{original}", f":{replacement}")
            config = isolated / "Caddyfile"
            config.write_text(configuration, encoding="utf-8")
            scope.start(
                "smoke-caddy",
                [str(binary.resolve()), "run", "--config", str(config), "--adapter", "caddyfile"],
                isolated,
            )
            try:
                for port in ports:
                    wait_port(scope, port, time.monotonic() + 30)
                deadline = time.monotonic() + 30
                while not host.ca_file(isolated).exists():
                    scope.check()
                    if time.monotonic() >= deadline:
                        raise ValueError("Isolated CA was not created")
                    time.sleep(0.1)
                context = ssl.create_default_context(cafile=str(host.ca_file(isolated)))
                for index, port in enumerate(ports):
                    path = "/denied" if index == 1 else "/readyz?probe=fixed"
                    expected = (
                        (403, b"<Code>AccessDenied</Code>") if index == 1 else (200, b"ready\n")
                    )
                    ready_deadline = time.monotonic() + 30
                    while True:
                        scope.check()
                        try:
                            response = connectivity.read_endpoint(
                                f"https://127.0.0.1:{port}{path}", context
                            )
                            break
                        except ValueError:
                            if time.monotonic() >= ready_deadline:
                                raise ValueError(
                                    f"Isolated TLS route {port} did not become ready"
                                ) from None
                            time.sleep(0.1)
                    if response != expected:
                        raise ValueError("Isolated TLS response differs")
                    if observed[-1] != (f"127.0.0.1:{port}", path):
                        raise ValueError("Gateway changed the Host or request target")
            finally:
                scope.close()
            if any(process.poll() is None for _, process in scope.children):
                raise ValueError("Owned Caddy child survived shutdown")
        print(
            "PASS isolated Linux Caddy installation/repeat setup, version/build, three TLS routes, Host/URI preservation and owned-child shutdown. Delivery was a test fixture, not published-download acceptance. No cluster or system trust was changed.",
            flush=True,
        )
    finally:
        scope.close()
        for server in servers:
            server.shutdown()
            server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        smoke(args.binary)
    else:
        print(
            "Preview only; add --execute for isolated loopback TLS tests. No listener or CA was created."
        )


if __name__ == "__main__":
    main()
