"""Check reversible maintenance transport, committed response loss and recovery gates."""

import base64
import http.client
import importlib.util
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import test_maintenance_guard_conflict as guard_tests
import yaml
from test_release_workflows_integration import BASH

FIXTURE = guard_tests.FIXTURE
ROOT = FIXTURE.parents[2]
KUBECTL = shutil.which("kubectl")


def load(name):
    spec = importlib.util.spec_from_file_location(name, FIXTURE / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(FIXTURE), *sys.path]):
        spec.loader.exec_module(module)
    return module


PROXY = load("maintenance_patch_proxy")
with patch.dict(sys.modules, {"maintenance_patch_proxy": PROXY}):
    MODULE = load("maintenance_patch_response")
TOKEN = (
    "header."
    + base64.urlsafe_b64encode(json.dumps({"sub": PROXY.ACCOUNT}).encode()).decode().rstrip("=")
    + ".signature"
)


def evidence():
    workflow, workers, parameters, raw, lock, before, *_ = guard_tests.case("deployment")
    workflow["metadata"]["namespace"] = "argo-workflows"
    workflow["spec"]["workflowTemplateRef"] = {"name": MODULE.NAME}
    nodes = workflow["status"]["nodes"]
    barrier = nodes.pop(guard_tests.MODULE.BARRIER)
    barrier.update(id=MODULE.BARRIER, displayName=MODULE.BARRIER)
    nodes[MODULE.BARRIER] = barrier
    failed = nodes["pause-deployment"]
    del failed["templateRef"]
    failed["templateName"] = "fault-patch-deployment"
    before["deployment"]["metadata"]["resourceVersion"] = (
        MODULE.partial.maintenance.readiness.outputs(nodes["guard-deployment"])["observation"][
            "resourceVersion"
        ]
    )
    expected = {
        "before": deepcopy(before["deployment"]),
        "patch": MODULE.authority.prepare("deployment", before["deployment"])[0],
    }
    after = deepcopy(before)
    after["deployment"]["metadata"].update(
        resourceVersion="99", generation=before["deployment"]["metadata"]["generation"] + 1
    )
    after["deployment"]["spec"]["replicas"] = 0
    after["deployment"]["status"].update(
        observedGeneration=after["deployment"]["metadata"]["generation"],
        replicas=0,
        readyReplicas=0,
        updatedReplicas=0,
        availableReplicas=0,
    )
    after["serverPod"]["items"] = []
    after["pods"] = after["pods"][:2]
    after["server"][0]["spec"]["replicas"] = after["workloads"][0]["spec"]["replicas"] = 0
    after["podList"] = {
        "apiVersion": "v1",
        "kind": "PodList",
        "metadata": {"resourceVersion": "100"},
        "items": [],
    }
    event = {
        "event": "deployment-committed-response-dropped",
        "upstreamStatus": 200,
        "uid": expected["before"]["metadata"]["uid"],
        "resourceVersion": "98",
        "patchDigest": MODULE.ledger.digest(expected["patch"]),
        "successfulResponsesForwarded": 0,
    }
    return workflow, workers, parameters, raw, lock, before, after, expected, [event]


class MaintenancePatchEvidenceTests(unittest.TestCase):
    def test_named_route_accepts_only_native_timeout_and_patch_query_options(self):
        for patch_request in (False, True):
            self.assertTrue(PROXY.named_request(PROXY.ROUTE, patch=patch_request))
            self.assertTrue(PROXY.named_request(PROXY.ROUTE + "?timeout=10s", patch=patch_request))
            for query in (
                "timeout=0s",
                "timeout=11s",
                "timeout=",
                "timeout=10s&timeout=10s",
                "dryRun=All",
                "watch=true",
                "timeout",
                "timeout=10s&other=value",
            ):
                with self.subTest(patch=patch_request, query=query):
                    self.assertFalse(
                        PROXY.named_request(PROXY.ROUTE + "?" + query, patch=patch_request)
                    )
        options = "?fieldManager=kubectl-patch&fieldValidation=Strict&timeout=10s"
        self.assertTrue(PROXY.named_request(PROXY.ROUTE + options, patch=True))
        self.assertFalse(PROXY.named_request(PROXY.ROUTE + options, patch=False))
        self.assertFalse(PROXY.named_request(PROXY.ROUTE + "-other?timeout=10s", patch=True))
        self.assertFalse(PROXY.named_request("https://other" + PROXY.ROUTE, patch=True))

    def test_prepare_binds_fresh_guard_and_projects_no_runner_credential_to_proxy(self):
        workflow, _, parameters, raw, _, before, _, expected, _ = evidence()
        workflow["status"]["phase"] = "Running"
        workflow["status"]["nodes"][MODULE.BARRIER]["phase"] = "Running"
        del workflow["status"]["nodes"]["pause-deployment"]
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            results = fixture / "results"
            results.mkdir()
            for key, value in before.items():
                name = "server-pods" if key == "serverPod" else key
                (fixture / f"maintenance-response-before.{name}.json").write_text(json.dumps(value))
            for name, value in (
                ("maintenance-response.barrier", workflow),
                ("maintenance-response.barrier-pods", {"items": []}),
                ("maintenance-response.target", expected["before"]),
                ("parent-parameters", parameters),
            ):
                (fixture / (name + ".json")).write_text(json.dumps(value))
            (fixture / "parent-request.json").write_bytes(raw)
            (fixture / "maintenance-response.state.txt").write_text("ready")
            with patch("sys.argv", ["fixture", "prepare", str(fixture), str(results)]):
                MODULE.main()
            client = json.loads((fixture / "maintenance-response.client.json").read_text())
            self.assertEqual(
                client["clusters"][0]["cluster"],
                {"server": PROXY.ADDRESS, "certificate-authority": "/fault/ca.crt"},
            )
            self.assertEqual(
                client["users"][0]["user"],
                {"tokenFile": "/var/run/secrets/kubernetes.io/serviceaccount/token"},
            )
            pod, service = yaml.safe_load_all(
                (fixture / "maintenance-response.proxy.yaml").read_text()
            )
            self.assertFalse(pod["spec"]["automountServiceAccountToken"])
            self.assertEqual(service["spec"]["selector"], pod["metadata"]["labels"])
            self.assertEqual(
                set(v["name"] for v in pod["spec"]["volumes"]), {"fault", "tls", "api-ca"}
            )
            self.assertNotIn("env", pod["spec"]["containers"][0])
            self.assertNotIn("envFrom", pod["spec"]["containers"][0])
            output = fixture / "maintenance-response.expected.json"
            self.assertEqual(json.loads(output.read_text()), expected)
            output.unlink()
            stale = deepcopy(expected["before"])
            stale["metadata"]["resourceVersion"] = "stale"
            (fixture / "maintenance-response.target.json").write_text(json.dumps(stale))
            with (
                patch("sys.argv", ["fixture", "prepare", str(fixture), str(results)]),
                self.assertRaises(AssertionError),
            ):
                MODULE.main()
            self.assertFalse(output.exists())

    def test_template_preserves_payload_guards_authority_and_deadlines(self):
        result = MODULE.template()
        native = yaml.safe_load(
            (ROOT / "gitops/release-workflows/maintenance-resources-v1.yaml").read_text()
        )
        original = next(t for t in native["spec"]["templates"] if t["name"] == "patch-deployment")
        adapted = next(
            t for t in result["spec"]["templates"] if t["name"] == "fault-patch-deployment"
        )
        self.assertEqual(adapted["resource"]["manifest"], original["resource"]["manifest"])
        self.assertEqual(adapted["resource"]["flags"][:-1], original["resource"]["flags"])
        self.assertEqual(adapted["activeDeadlineSeconds"], original["activeDeadlineSeconds"])
        self.assertEqual(result["spec"]["serviceAccountName"], "release-maintenance-pause")
        self.assertNotIn("retryStrategy", adapted)

    def test_real_prefix_committed_event_and_exact_retention_are_required(self):
        workflow, workers, parameters, raw, lock, before, after, expected, events = evidence()
        MODULE.ready(workflow, parameters, raw, lock, before)
        MODULE.partial.maintenance.retained(before, after, paused=True)
        self.assertEqual(
            MODULE.failed(workflow, workers, expected, events, after)["deploymentUID"],
            expected["before"]["metadata"]["uid"],
        )
        for change in (
            "event",
            "digest",
            "forwarded",
            "worker",
            "checkpoint",
            "generation",
            "spec",
            "shutdown",
        ):
            w, p, e, state = (
                deepcopy(workflow),
                deepcopy(workers),
                deepcopy(events),
                deepcopy(after),
            )
            if change == "event":
                e[0]["event"] = "upstream-failed"
            elif change == "digest":
                e[0]["patchDigest"] = "other"
            elif change == "forwarded":
                e[0]["successfulResponsesForwarded"] = 1
            elif change == "worker":
                next(v for v in p if v["nodeID"] == "pause-deployment")["exitCode"] = 0
            elif change == "checkpoint":
                w["status"]["nodes"]["checkpoint"] = {
                    "id": "checkpoint",
                    "displayName": "checkpoint",
                    "type": "Pod",
                    "phase": "Succeeded",
                }
            elif change == "generation":
                state["deployment"]["metadata"]["generation"] += 1
            elif change == "spec":
                state["deployment"]["spec"]["replicas"] = 1
            else:
                w["spec"]["shutdown"] = "Stop"
            with self.subTest(change=change), self.assertRaises(AssertionError):
                MODULE.failed(w, p, expected, e, state)
        bad = deepcopy(before)
        bad["records"] = []
        with self.assertRaises(AssertionError):
            MODULE.ready(workflow, parameters, raw, lock, bad)

    def test_cli_does_not_publish_response_loss_for_wrong_commit_or_changed_records(self):
        workflow, workers, parameters, raw, _, before, after, expected, events = evidence()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            results = fixture / "results"
            results.mkdir()

            def save(name, value):
                (fixture / (name + ".json")).write_text(json.dumps(value))

            for suffix, value in (
                ("workflow", workflow),
                ("barrier", workflow),
                ("workers", workers),
                ("expected", expected),
            ):
                save("maintenance-response." + suffix, value)
            save("parent-parameters", parameters)
            (fixture / "parent-request.json").write_bytes(raw)
            (fixture / "maintenance-response.proxy.log").write_text(json.dumps(events[0]) + "\n")
            for label, state in (("before", before), ("after", after)):
                for key, value in state.items():
                    save(
                        "maintenance-response-"
                        + label
                        + "."
                        + {"serverPod": "server-pods", "podList": "pod-list"}.get(key, key),
                        value,
                    )
            with patch("sys.argv", ["fixture", "verify", str(fixture), str(results)]):
                MODULE.main()
            outcome = results / "maintenance-response.outcome.json"
            self.assertEqual(
                json.loads(outcome.read_text())["decision"],
                "maintenance-patch-response-loss-verified",
            )
            outcome.unlink()
            save("maintenance-response-after.records", [])
            with (
                patch("sys.argv", ["fixture", "verify", str(fixture), str(results)]),
                self.assertRaises(AssertionError),
            ):
                MODULE.main()
            self.assertFalse(outcome.exists())

    @unittest.skipUnless(BASH, "Bash required for recovery failure ordering")
    def test_shell_never_recovers_or_advances_baseline_after_failed_evidence(self):
        assert BASH
        setup = r"""
set -euo pipefail
fixture=$1 results=$1/results source_dir=unused KUBECONFIG=$1/kubeconfig
mode=preflight-parent scenario=initial GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
current_stage=parent adoption_baseline=first root_server_pods_file=original
adoption_retention_baseline=$fixture/maintenance-partial-deployment-recovered
mkdir -p "$results"; touch "$fixture/owned" "$results/maintenance-partial-deployment.recovered.json"
fail=$3
trap 'echo "baseline=$adoption_retention_baseline" >> "$fixture/trace"' EXIT
start_stage() { echo "stage $*" >> "$fixture/trace"; }; complete_stage() { echo complete >> "$fixture/trace"; }
maintenance_snapshot() { :; }; maintenance_partial_root() { :; }; maintenance_partial_workers() { echo '[]'; }
root_state() { :; }; root_retained() { :; }; root_persistent_retained() { [[ $fail != retention ]]; }
maintenance_deployment_pods() { echo '{}'; }; maintenance_deployment_settled() { [[ $fail != settle ]]; }
argocd_case() { echo sync >> "$fixture/trace"; [[ $fail != sync ]]; }
server_case() { echo http >> "$fixture/trace"; [[ $fail != http ]]; }
adoption_case() { echo adoption >> "$fixture/trace"; [[ $fail != adoption ]]; }
cmp() { [[ $fail != records ]]; }
k() { echo '{}'; }; a() { echo '{}'; }; argo() { :; }
jq() { cat >/dev/null; echo worker; }
uv() { echo "uv $*" >> "$fixture/trace"; [[ $* != *" $fail "* ]]; }
"""
        # Use the actual full shell; bypass only the polling loop's clock/JSON
        # adapters. Wait results depend on stage to distinguish expected failure.
        setup += r"""
timeout() {
  if [[ $* == 1830s* ]]; then [[ $fail == unexpected ]] && return 0; return 1; fi
  [[ $fail != checkpoint-wait ]]
}
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "run.sh"
            script.write_text(setup + '\nsource "$2"\n', newline="\n")
            for fail in (
                "pass",
                "prepare",
                "unexpected",
                "settle",
                "retention",
                "verify",
                "checkpoint-wait",
                "checkpoint",
                "sync",
                "http",
                "recovered",
                "adoption",
                "records",
            ):
                fixture = directory / fail
                fixture.mkdir()
                process = subprocess.run(
                    [
                        BASH,
                        script.as_posix(),
                        fixture.as_posix(),
                        (FIXTURE / "maintenance-patch-response.sh").as_posix(),
                        fail,
                    ],
                    cwd=ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                trace = (fixture / "trace").read_text()
                with self.subTest(fail=fail):
                    self.assertEqual(process.returncode == 0, fail == "pass", process.stderr)
                    if fail in {
                        "prepare",
                        "unexpected",
                        "settle",
                        "retention",
                        "verify",
                        "checkpoint-wait",
                        "checkpoint",
                    }:
                        self.assertNotIn("sync\n", trace)
                    if fail not in {"pass", "adoption", "records"}:
                        self.assertIn(
                            "baseline="
                            + fixture.as_posix()
                            + "/maintenance-partial-deployment-recovered",
                            trace,
                        )
                    if fail in {"adoption", "records"}:
                        # EXIT runs inside the candidate's local scope; it is
                        # not evidence that the caller's baseline advanced.
                        self.assertEqual(trace.splitlines().count("complete"), 2)


class API(HTTPServer):
    def __init__(self, expected):
        super().__init__(("127.0.0.1", 0), APIHandler)
        self.expected = expected
        self.writes = []
        self.reads = []
        self.status = 200
        self.mismatch = False


class APIHandler(BaseHTTPRequestHandler):
    server: API

    def log_message(self, format, *args):
        pass

    def respond(self, value):
        data = json.dumps(value).encode()
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.reads.append(self.path)
        self.respond(self.server.expected["before"])

    def do_PATCH(self):
        self.server.writes.append(
            (
                self.path,
                self.headers["Authorization"],
                json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
            )
        )
        obj = deepcopy(self.server.expected["before"])
        obj["metadata"].update(resourceVersion="99", generation=obj["metadata"]["generation"] + 1)
        obj["spec"]["replicas"] = 0 if not self.server.mismatch else 1
        self.respond(obj)


class MaintenancePatchTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.cert, cls.key = MODULE.ledger.certificate(Path(cls.directory.name), "localhost")
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
        *_, self.expected, _ = evidence()
        self.context = ssl.create_default_context(cafile=str(self.cert))
        self.api = API(self.expected)
        self.api.socket = self.tls.wrap_socket(self.api.socket, server_side=True)
        self.start(self.api)
        self.proxy = PROXY.Proxy(
            ("127.0.0.1", 0), self.expected, f"localhost:{self.api.server_port}", self.context
        )
        self.proxy.tls = self.tls
        self.start(self.proxy)

    def call(self, method="PATCH", path=None, body=None, token=TOKEN, context=None):
        connection = http.client.HTTPSConnection(
            "localhost", self.proxy.server_port, context=context or self.context, timeout=5
        )
        try:
            connection.request(
                method,
                path or PROXY.ROUTE,
                json.dumps(self.expected["patch"]).encode() if body is None else body,
                {"Authorization": "Bearer " + token, "Content-Type": "application/json-patch+json"},
            )
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_exact_tls_patch_commits_and_drops_once_other_requests_never_forward(self):
        for method, path, body, token in (
            ("POST", PROXY.ROUTE, None, TOKEN),
            ("PATCH", PROXY.ROUTE + "?dryRun=All", None, TOKEN),
            ("PATCH", PROXY.ROUTE + "-other", None, TOKEN),
            ("PATCH", PROXY.ROUTE, b"[]", TOKEN),
            ("PATCH", PROXY.ROUTE, None, "github-token"),
        ):
            self.assertEqual(self.call(method, path, body, token), (403, b""))
        self.assertFalse(self.api.writes)
        self.assertEqual(self.call("GET", PROXY.ROUTE + "?timeout=10s")[0], 200)
        self.assertEqual(self.api.reads, [PROXY.ROUTE + "?timeout=10s"])
        with self.assertRaises(http.client.RemoteDisconnected):
            self.call(path=PROXY.ROUTE + "?fieldManager=kubectl-patch&timeout=10s")
        self.assertEqual(len(self.api.writes), 1)
        self.assertEqual(self.proxy.events[0]["event"], "deployment-committed-response-dropped")
        self.assertEqual(self.call(), (410, b""))
        self.assertEqual(len(self.api.writes), 1)
        self.assertNotIn(TOKEN, json.dumps(self.proxy.events))

    def test_tls_or_upstream_rejection_and_wrong_commit_never_qualify(self):
        with self.assertRaises(ssl.SSLError):
            self.call(context=ssl.create_default_context())
        self.assertFalse(self.api.writes)
        self.api.mismatch = True
        with self.assertRaises(http.client.RemoteDisconnected):
            self.call()
        self.assertEqual(self.proxy.events, [{"event": "upstream-failed"}])

    def test_untrusted_upstream_tls_cannot_receive_the_patch(self):
        self.proxy.context = ssl.create_default_context()
        with self.assertRaises(http.client.RemoteDisconnected):
            self.call()
        self.assertFalse(self.api.writes)
        self.assertEqual(self.proxy.events, [{"event": "upstream-failed"}])

    def test_api_forbidden_is_not_a_committed_response_loss(self):
        self.api.status = 403
        with self.assertRaises(http.client.RemoteDisconnected):
            self.call()
        self.assertEqual(self.proxy.events, [{"event": "upstream-failed"}])

    @unittest.skipUnless(KUBECTL, "kubectl required for real client discovery/PATCH")
    def test_actual_kubectl_discovery_named_get_and_json_patch_reach_only_expected_write(self):
        assert KUBECTL
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [
                    {
                        "name": "fault",
                        "cluster": {
                            "server": f"https://localhost:{self.proxy.server_port}",
                            "certificate-authority": str(self.cert),
                        },
                    }
                ],
                "users": [{"name": "caller", "user": {"token": TOKEN}}],
                "contexts": [{"name": "fault", "context": {"cluster": "fault", "user": "caller"}}],
                "current-context": "fault",
            }
            (directory / "client.json").write_text(json.dumps(config))
            (directory / "patch.json").write_text(json.dumps(self.expected["patch"]))
            result = subprocess.run(
                [
                    KUBECTL,
                    "--kubeconfig",
                    str(directory / "client.json"),
                    "--cache-dir",
                    str(directory / "cache"),
                    "patch",
                    "deployments.apps",
                    "control-plane",
                    "--namespace=perf-platform",
                    "--type=json",
                    "--patch-file",
                    str(directory / "patch.json"),
                    "--request-timeout=10s",
                    "-o",
                    "json",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(len(self.api.writes), 1, result.stderr)
            self.assertTrue(self.api.reads)
            self.assertTrue(all(path == PROXY.ROUTE + "?timeout=10s" for path in self.api.reads))
            self.assertEqual(len(self.proxy.events), 1)
            self.assertEqual(self.proxy.events[0]["event"], "deployment-committed-response-dropped")


if __name__ == "__main__":
    unittest.main()
