"""Exercise the fixture's JSONPath wait with a real client and inert local API."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from test_release_workflows_integration import BASH

ROOT = Path(__file__).resolve().parents[1]
KUBECTL = shutil.which("kubectl")


@unittest.skipUnless(KUBECTL, "kubectl required for native JSONPath wait regression")
class KubeletImageWaitTests(unittest.TestCase):
    def test_empty_image_identity_requires_a_watch_before_success(self):
        assert KUBECTL is not None
        source = (ROOT / "tests/integration/release-workflows/registry.sh").read_text()
        expression = re.search(r"--for=jsonpath='([^']+)'", source)
        assert expression is not None
        result, watches = self.run_wait("--for=jsonpath=" + expression[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(watches, "Empty imageID satisfied wait before the non-empty watch update")
        observed = json.loads(result.stdout)
        self.assertEqual(
            observed["status"]["containerStatuses"][0]["imageID"], "containerd://sha256:observed"
        )

    def test_bootstrap_waits_through_null_and_false_establishment(self):
        if not BASH:
            self.skipTest("Bash required for the actual bounded native waits")
        source = (ROOT / "tests/integration/release-workflows/argocd.sh").read_text()
        command = re.search(r"argocd_crd_ready\(\) \{.*?\n\}", source, re.S)
        assert command is not None
        result, watches = self.run_wait(command[0], bootstrap=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(len(watches), 2)
        self.assertIn((True, "False"), watches)
        self.assertEqual(result.stdout.count("condition met"), 2)

    def run_wait(self, argument, *, bootstrap=False):
        assert KUBECTL is not None
        watches = []
        pod: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": "probe",
                "namespace": "perf-platform",
                "uid": "probe-uid",
                "resourceVersion": "1",
            },
            "status": {
                "containerStatuses": [{"name": "pull", "imageID": ""}],
                "conditions": None,
                "acceptedNames": {},
            },
        }
        latest = [pod]
        stage_marker: Path | None = None

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                if query.get("watch") == ["true"]:
                    establishing = stage_marker is not None and stage_marker.exists()
                    conditions = latest[0]["status"]["conditions"]
                    watches.append((establishing, conditions[-1]["status"] if conditions else None))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    ready = json.loads(json.dumps(pod))
                    ready["metadata"]["resourceVersion"] = "3"
                    ready["status"]["containerStatuses"][0]["imageID"] = (
                        "containerd://sha256:observed"
                    )
                    ready["status"]["conditions"] = [{"type": "Established", "status": "True"}]
                    ready["status"]["acceptedNames"] = {"kind": "Pod"}
                    intermediate = json.loads(json.dumps(pod))
                    intermediate["metadata"]["resourceVersion"] = "2"
                    intermediate["status"]["conditions"] = [
                        {"type": "NamesAccepted", "status": "True"},
                        {"type": "Established", "status": "False"},
                    ]
                    intermediate["status"]["acceptedNames"] = {"kind": "Pod"}
                    initial = latest[0]
                    events = [
                        {"type": "ADDED", "object": initial},
                        {
                            "type": "BOOKMARK",
                            "object": {
                                "apiVersion": "v1",
                                "kind": "Pod",
                                "metadata": {
                                    "resourceVersion": initial["metadata"]["resourceVersion"],
                                    "annotations": {"k8s.io/initial-events-end": "true"},
                                },
                            },
                        },
                    ]
                    if bootstrap and not establishing:
                        events.append({"type": "MODIFIED", "object": intermediate})
                        latest[0] = intermediate
                    else:
                        events += [
                            {"type": "MODIFIED", "object": intermediate},
                            {"type": "MODIFIED", "object": ready},
                        ]
                        latest[0] = ready
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
                    response = latest[0]
                elif parsed.path.endswith("/pods"):
                    response = {
                        "apiVersion": "v1",
                        "kind": "PodList",
                        "metadata": {"resourceVersion": latest[0]["metadata"]["resourceVersion"]},
                        "items": [latest[0]],
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
                stage_marker = directory / "establishing"
                config = directory / "config.json"
                config.write_text(
                    json.dumps(
                        {
                            "apiVersion": "v1",
                            "kind": "Config",
                            "clusters": [
                                {
                                    "name": "inert",
                                    "cluster": {"server": f"http://127.0.0.1:{server.server_port}"},
                                }
                            ],
                            "contexts": [
                                {
                                    "name": "kind-perfeng-local",
                                    "context": {
                                        "cluster": "inert",
                                        "user": "inert",
                                        "namespace": "perf-platform",
                                    },
                                }
                            ],
                            "users": [{"name": "inert", "user": {}}],
                            "current-context": "kind-perfeng-local",
                        }
                    )
                )
                args = [
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
                    argument,
                    "--timeout=3s",
                ]
                if bootstrap:
                    assert BASH is not None
                    args = [
                        BASH,
                        "-c",
                        # Observe the second invocation without replacing the native client.
                        "kubectl() {\n"
                        '  for arg; do if [[ $arg == --for=condition=Established ]]; then : > "$WAIT_STAGE"; fi; done\n'
                        '  "$REAL_KUBECTL" "$@" "--cache-dir=$WAIT_CACHE"\n'
                        "}\nexport -f kubectl\n"
                        + argument
                        + "\nKUBECONFIG=$1\nargocd_crd_ready pod/probe Pod\n",
                        "bootstrap-watch",
                        config.as_posix(),
                    ]
                try:
                    result = subprocess.run(
                        args,
                        env={
                            **os.environ,
                            "PATH": str(Path(KUBECTL).parent) + os.pathsep + os.environ["PATH"],
                            "REAL_KUBECTL": Path(KUBECTL).as_posix(),
                            "WAIT_STAGE": stage_marker.as_posix(),
                            "WAIT_CACHE": (directory / "cache").as_posix(),
                        },
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                except subprocess.TimeoutExpired as error:
                    self.fail(
                        f"Native wait timed out: {watches!r}; {error.stdout!r}; {error.stderr!r}"
                    )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        return result, watches
