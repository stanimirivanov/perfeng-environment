"""A dropped successful response must be distinguishable from a rejected write."""

import base64
import copy
import http.client
import importlib.util
import json
import os
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml
from test_release_workflows_integration import BASH

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
SPEC = importlib.util.spec_from_file_location(
    "ledger_response_proxy", FIXTURE / "ledger_response_proxy.py"
)
assert SPEC and SPEC.loader
PROXY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROXY)
TOKEN = (
    "header."
    + base64.urlsafe_b64encode(json.dumps({"sub": PROXY.ACCOUNT}).encode()).decode().rstrip("=")
    + ".signature"
)


def receipt():
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "immutable": True,
        "metadata": {
            "name": "receipt." + "a" * 32 + "." + "a" * 32,
            "namespace": "perfeng-release-ledger",
            "labels": {"perfeng.io/purpose": "release-adoption"},
            "annotations": {"perfeng.io/first-workflow-uid": "workflow-uid"},
        },
        "binaryData": {"request.json": "eA==", "receipt.json": "eQ=="},
    }


class API(HTTPServer):
    def __init__(self, expected):
        super().__init__(("127.0.0.1", 0), APIHandler)
        self.expected = expected
        self.requests = []
        self.status = 201
        self.mismatch = False


class APIHandler(BaseHTTPRequestHandler):
    server: API

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append((self.path, self.headers["Authorization"], json.loads(raw)))
        response = copy.deepcopy(self.server.expected)
        response["metadata"].update(uid="committed-uid", resourceVersion="7")
        if self.server.mismatch:
            response["immutable"] = False
        if self.server.status != 201:
            response = {"message": "private-upstream-detail"}
        data = json.dumps(response).encode()
        self.send_response(self.server.status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class LedgerResponseProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.cert, cls.key = PROXY.certificate(Path(cls.directory.name), "localhost")
        cls.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.tls.load_cert_chain(cls.cert, cls.key)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def start(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

        self.addCleanup(close)

    def setUp(self):
        self.expected = receipt()
        self.api = API(self.expected)
        self.api.socket = self.tls.wrap_socket(self.api.socket, server_side=True)
        self.start(self.api)
        self.context = ssl.create_default_context(cafile=str(self.cert))
        self.proxy = PROXY.Proxy(
            ("127.0.0.1", 0), self.expected, f"localhost:{self.api.server_port}", self.context
        )
        self.proxy.tls = self.tls
        self.start(self.proxy)

    def call(
        self, method="POST", path=None, body=None, headers=None, context=None, host="localhost"
    ):
        connection = http.client.HTTPSConnection(
            host, self.proxy.server_port, context=context or self.context, timeout=3
        )
        try:
            connection.request(
                method,
                path or PROXY.ROUTE,
                json.dumps(self.expected).encode() if body is None else body,
                {"Authorization": "Bearer " + TOKEN} if headers is None else headers,
            )
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_real_tls_commits_once_and_drops_status_line_without_forwarding_again(self):
        with self.assertRaises(http.client.RemoteDisconnected):
            self.call()
        self.assertEqual(self.api.requests, [(PROXY.ROUTE, "Bearer " + TOKEN, self.expected)])
        self.assertEqual(
            self.proxy.events,
            [
                {
                    "event": "receipt-committed-response-dropped",
                    "upstreamStatus": 201,
                    "uid": "committed-uid",
                    "contentDigest": PROXY.digest(self.expected),
                    "successfulResponsesForwarded": 0,
                }
            ],
        )
        self.assertEqual(self.call(), (410, b""))
        self.assertEqual(len(self.api.requests), 1)
        self.assertNotIn(TOKEN, json.dumps(self.proxy.events))

    def test_other_routes_methods_credentials_and_payloads_never_reach_api(self):
        changed = copy.deepcopy(self.expected)
        changed["metadata"]["name"] = "other-receipt"
        foreign = (
            "header."
            + base64.urlsafe_b64encode(b'{"sub":"system:serviceaccount:other:default"}')
            .decode()
            .rstrip("=")
            + ".signature"
        )
        for kwargs in (
            {"path": PROXY.ROUTE + "?dryRun=All"},
            {"path": "/api/v1/secrets"},
            {"method": "GET"},
            {"method": "DELETE"},
            {"method": "PATCH"},
            {"headers": {"Authorization": "Bearer ghp_private-token"}},
            {"headers": {"Authorization": "Bearer " + foreign}},
            {"headers": {}},
            {"body": b"not-json"},
            {"body": json.dumps(changed).encode()},
            {
                "body": b"",
                "headers": {
                    "Authorization": "Bearer " + TOKEN,
                    "Content-Length": str(PROXY.LIMIT + 1),
                },
            },
            {
                "body": json.dumps(self.expected)
                .replace('"kind": "ConfigMap"', '"kind": "ConfigMap", "kind": "ConfigMap"')
                .encode()
            },
        ):
            with self.subTest(kwargs=list(kwargs)):
                status, body = self.call(**kwargs)
                self.assertIn(status, (403, 501))
                self.assertEqual(body, b"")
        self.assertFalse(self.api.requests)
        self.assertFalse(self.proxy.attempted)
        self.assertFalse(self.proxy.events)

    def test_upstream_denial_and_bad_commit_are_not_response_loss_proof(self):
        self.api.status = 403
        self.assertEqual(self.call(), (502, b""))
        self.assertEqual(self.proxy.events, [{"event": "upstream-refused", "status": 403}])
        self.assertNotIn("private-upstream-detail", json.dumps(self.proxy.events))

    def test_mismatched_persisted_receipt_never_qualifies(self):
        self.api.mismatch = True
        with self.assertRaises(http.client.RemoteDisconnected):
            self.call()
        self.assertEqual(self.proxy.events, [{"event": "upstream-failed"}])

    def test_tls_verification_remains_enabled_on_both_hops(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.call(context=ssl.create_default_context())
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.call(host="127.0.0.1")
        self.assertFalse(self.api.requests)
        self.proxy.context = ssl.create_default_context()
        with self.assertRaises(http.client.RemoteDisconnected):
            self.call()
        self.assertEqual(self.proxy.events, [{"event": "upstream-failed"}])
        self.assertFalse(self.api.requests)

    def test_transport_timeout_is_sanitized_and_not_retried(self):
        original = http.client.HTTPSConnection.request
        attempts = []

        def request(connection, *args, **kwargs):
            if connection.port == self.api.server_port:
                attempts.append(connection.port)
                raise TimeoutError("private-token")
            return original(connection, *args, **kwargs)

        with patch.object(PROXY.http.client.HTTPSConnection, "request", new=request):
            with self.assertRaises(http.client.RemoteDisconnected):
                self.call()
        self.assertEqual(attempts, [self.api.server_port])
        self.assertEqual(self.proxy.events, [{"event": "upstream-failed"}])
        self.assertNotIn("private-token", json.dumps(self.proxy.events))

    def test_generated_native_create_preserves_manifest_and_keeps_credentials_out_of_flags(self):
        native = yaml.safe_load(
            (ROOT / "gitops/release-workflows/adoption-records.yaml").read_text()
        )
        ledger = yaml.safe_load((FIXTURE / "ledger-workflow.yaml").read_text())
        parameters = {p["name"]: "fixture-value" for p in ledger["spec"]["arguments"]["parameters"]}
        workflow = PROXY.fault_workflow(native, ledger, parameters)
        actual = copy.deepcopy(workflow["spec"]["templates"][0])
        original = next(t for t in native["spec"]["templates"] if t["name"] == "create-receipt")
        flags = actual["resource"].pop("flags")
        patch_spec = json.loads(actual.pop("podSpecPatch"))
        self.assertEqual(actual, original)
        self.assertEqual(
            flags,
            ["--kubeconfig=/fault/client.json", "--raw=" + PROXY.ROUTE],
        )
        self.assertTrue(patch_spec["containers"][0]["volumeMounts"][0]["readOnly"])
        pod, service = list(
            yaml.safe_load_all((FIXTURE / "ledger-response-proxy.yaml").read_text())
        )
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        self.assertFalse(pod["spec"]["enableServiceLinks"])
        self.assertNotIn("env", pod["spec"]["containers"][0])
        planner = yaml.safe_load(
            (ROOT / "gitops/release-workflows/plan-release-adoption-v2.yaml").read_text()
        )
        self.assertEqual(
            pod["spec"]["containers"][0]["image"],
            planner["spec"]["templates"][0]["container"]["image"],
        )
        self.assertEqual(service["spec"]["selector"], pod["metadata"]["labels"])

    def test_proof_requires_failed_native_worker_committed_uid_and_original_provenance(self):
        workflow = {
            "metadata": {"name": "workflow", "uid": "workflow-uid"},
            "spec": {"entrypoint": "create-receipt", "serviceAccountName": "release-adoption"},
            "status": {
                "phase": "Failed",
                "nodes": {"workflow": {"type": "Pod", "phase": "Failed"}},
            },
        }
        committed = copy.deepcopy(self.expected)
        committed["metadata"]["uid"] = "committed-uid"
        records = {"receipt": committed, "pointer": None}
        events = [
            {
                "event": "receipt-committed-response-dropped",
                "upstreamStatus": 201,
                "uid": "committed-uid",
                "contentDigest": PROXY.digest(self.expected),
                "successfulResponsesForwarded": 0,
            }
        ]
        self.assertEqual(
            PROXY.verify(workflow, events, records, self.expected)["receiptUID"], "committed-uid"
        )
        for target, path, value in (
            ("workflow", ("status", "phase"), "Succeeded"),
            ("workflow", ("spec", "shutdown"), "Terminate"),
            ("workflow", ("status", "outputs"), {"parameters": [{"name": "result"}]}),
            (
                "workflow",
                ("status", "nodes", "workflow", "outputs"),
                {"parameters": [{"name": "result"}]},
            ),
            ("records", ("receipt", "metadata", "uid"), "replacement"),
            ("records", ("receipt", "binaryData"), {}),
            ("records", ("pointer",), {}),
            ("events", (0, "successfulResponsesForwarded"), 1),
            ("events", (0, "upstreamStatus"), 403),
            ("events", (0, "event"), "upstream-failed"),
        ):
            items = copy.deepcopy(dict(workflow=workflow, records=records, events=events))
            obj: Any = items[target]
            for key in path[:-1]:
                obj = obj[key]
            obj[path[-1]] = value
            with self.subTest(target=target, path=path), self.assertRaises(AssertionError):
                PROXY.verify(items["workflow"], items["events"], items["records"], self.expected)

    @unittest.skipUnless(BASH, "Bash is required for the real CI refusal guard")
    def test_direct_invocation_and_non_hosted_source_refuse_before_any_tool(self):
        assert BASH is not None
        for command in (
            [BASH, str(FIXTURE / "ledger-response.sh")],
            [
                BASH,
                "-c",
                'mode=boundaries; source "$1"',
                "guard",
                str(FIXTURE / "ledger-response.sh"),
            ],
        ):
            result = subprocess.run(
                command,
                env={**os.environ, "GITHUB_ACTIONS": "false"},
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
