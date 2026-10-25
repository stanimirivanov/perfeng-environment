"""Exercise the fixture's JSONPath wait with a real client and inert local API."""

import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
KUBECTL = shutil.which("kubectl")


@unittest.skipUnless(KUBECTL, "kubectl required for native JSONPath wait regression")
class KubeletImageWaitTests(unittest.TestCase):
    def test_empty_image_identity_requires_a_watch_before_success(self):
        assert KUBECTL is not None
        source = (ROOT / "tests/integration/release-workflows/registry.sh").read_text()
        expression = re.search(r"--for=jsonpath='([^']+)'", source)
        assert expression is not None
        watches = []
        pod = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": "probe",
                "namespace": "perf-platform",
                "uid": "probe-uid",
                "resourceVersion": "1",
            },
            "status": {"containerStatuses": [{"name": "pull", "imageID": ""}]},
        }

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                if query.get("watch") == ["true"]:
                    watches.append(True)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    ready = json.loads(json.dumps(pod))
                    ready["metadata"]["resourceVersion"] = "2"
                    ready["status"]["containerStatuses"][0]["imageID"] = (
                        "containerd://sha256:observed"
                    )
                    events = [
                        {"type": "ADDED", "object": pod},
                        {
                            "type": "BOOKMARK",
                            "object": {
                                "apiVersion": "v1",
                                "kind": "Pod",
                                "metadata": {
                                    "resourceVersion": "1",
                                    "annotations": {"k8s.io/initial-events-end": "true"},
                                },
                            },
                        },
                        {"type": "MODIFIED", "object": ready},
                    ]
                    try:
                        self.wfile.write(
                            "".join(json.dumps(event) + "\n" for event in events).encode()
                        )
                    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                        pass  # A satisfied native wait may close its watch immediately.
                    return
                if parsed.path == "/api":
                    response = {"kind": "APIVersions", "versions": ["v1"]}
                elif parsed.path == "/apis":
                    response = {"kind": "APIGroupList", "groups": []}
                elif parsed.path == "/api/v1":
                    response = {
                        "kind": "APIResourceList",
                        "groupVersion": "v1",
                        "resources": [
                            {
                                "name": "pods",
                                "singularName": "pod",
                                "namespaced": True,
                                "kind": "Pod",
                                "verbs": ["get", "list", "watch"],
                            }
                        ],
                    }
                elif parsed.path.endswith("/pods/probe"):
                    response = pod
                elif parsed.path.endswith("/pods"):
                    response = {
                        "apiVersion": "v1",
                        "kind": "PodList",
                        "metadata": {"resourceVersion": "1"},
                        "items": [pod],
                    }
                else:
                    self.send_error(404)
                    return
                body = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                config = directory / "config.json"
                config.write_text(
                    json.dumps(
                        {
                            "apiVersion": "v1",
                            "kind": "Config",
                            "clusters": [],
                            "contexts": [],
                            "users": [],
                        }
                    )
                )
                result = subprocess.run(
                    [
                        KUBECTL,
                        "--kubeconfig",
                        str(config),
                        "--server",
                        f"http://127.0.0.1:{server.server_port}",
                        "--cache-dir",
                        str(directory / "cache"),
                        "--request-timeout=5s",
                        "-n",
                        "perf-platform",
                        "wait",
                        "pod/probe",
                        "-o=json",
                        "--for=jsonpath=" + expression[1],
                        "--timeout=3s",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(
                    watches, "Empty imageID satisfied wait before the non-empty watch update"
                )
                observed = json.loads(result.stdout)
                self.assertEqual(
                    observed["status"]["containerStatuses"][0]["imageID"],
                    "containerd://sha256:observed",
                    "Wait returned the empty-ID event before the populated-ID event",
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
