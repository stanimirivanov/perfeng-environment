"""Private GitHub API fixture serving exact revisions of one controlled change."""

import hmac
import json
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def serve():
    root = Path("/fixture/change")
    source = json.loads((root / "provider.json").read_text())
    auth = "Bearer " + os.environ["FIXTURE_GITHUB_TOKEN"]
    repo = "/repos/perfeng-fixture/checkout"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if not hmac.compare_digest(self.headers.get("Authorization", ""), auth):
                self.send_error(401)
                return
            if url.path == repo + "/pulls/1" and not query:
                body = json.dumps(source["pull"]).encode()
            elif url.path == repo + "/pulls/1/files" and query == {
                "per_page": ["100"],
                "page": ["1"],
            }:
                body = json.dumps(source["files"]).encode()
            elif url.path == repo + "/contents/api/openapi.yaml" and query.get("ref") in (
                [source["pull"]["base"]["sha"]],
                [source["pull"]["head"]["sha"]],
            ):
                body = (root / query["ref"][0]).read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("0.0.0.0", 8443), Handler) as server:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(root / "github.crt", root / "github.key")
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        server.serve_forever()


if __name__ == "__main__":
    serve()
