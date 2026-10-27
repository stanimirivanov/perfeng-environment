"""Prove no-op Application response loss, one-target transport and adoption gating."""

import http.client
import json
import os
import ssl
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch

import test_maintenance_guard_conflict as guard_tests
import test_maintenance_patch_response as deployment
import yaml
from test_release_workflows_integration import BASH

PROXY, RESPONSE = deployment.PROXY, deployment.MODULE
with patch.dict(sys.modules, {"maintenance_patch_response": RESPONSE}):
    MODULE = deployment.load("maintenance_application_response")
FIXTURE, ROOT = deployment.FIXTURE, deployment.ROOT


def evidence(resource):
    workflow, workers, parameters, raw, lock, before, *_ = guard_tests.case(resource)
    workflow["metadata"]["namespace"] = "argo-workflows"
    workflow["spec"]["workflowTemplateRef"] = {"name": RESPONSE.parent_name(resource)}
    nodes = workflow["status"]["nodes"]
    barrier = nodes.pop(guard_tests.MODULE.BARRIER)
    barrier.update(id=RESPONSE.BARRIER, displayName=RESPONSE.BARRIER)
    nodes[RESPONSE.BARRIER] = barrier
    del nodes["pause-" + resource]["templateRef"]
    nodes["pause-" + resource]["templateName"] = "fault-patch-" + resource
    target = next(
        o
        for o in before["applications"]["items"]
        if o["metadata"]["name"] == PROXY.TARGETS[resource][3]
    )
    target["metadata"]["generation"] = 7
    target["metadata"]["resourceVersion"] = RESPONSE.partial.maintenance.readiness.outputs(
        nodes["guard-" + resource]
    )["observation"]["resourceVersion"]
    expected = {
        "before": deepcopy(target),
        "patch": RESPONSE.authority.prepare(resource, target)[0],
    }
    before["server-pods"] = before.pop("serverPod")
    after = deepcopy(before)
    event = {
        "event": resource + "-committed-response-dropped",
        "upstreamStatus": 200,
        "uid": target["metadata"]["uid"],
        "resourceVersion": target["metadata"]["resourceVersion"],
        "patchDigest": RESPONSE.ledger.digest(expected["patch"]),
        "successfulResponsesForwarded": 0,
    }
    return workflow, workers, parameters, raw, lock, before, after, expected, [event]


class ApplicationResponseEvidenceTests(unittest.TestCase):
    def test_noop_accepts_unchanged_version_but_never_other_ownership_spec_or_transition(self):
        for resource in ("root", "child"):
            *_, expected, _ = evidence(resource)
            original = expected["before"]
            self.assertEqual(PROXY.target(expected), resource)
            self.assertTrue(PROXY.committed(expected, deepcopy(original)))
            for mutate in (
                lambda o: o["metadata"].update(uid="foreign"),
                lambda o: o["metadata"].update(ownerReferences=[{}]),
                lambda o: o["metadata"].update(generation=8),
                lambda o: o["spec"]["syncPolicy"]["automated"].update(enabled=True),
                lambda o: o.update(operation={"sync": {}}),
            ):
                bad = deepcopy(original)
                mutate(bad)
                self.assertFalse(PROXY.committed(expected, bad))
            changed = deepcopy(expected)
            changed["before"]["spec"]["syncPolicy"]["automated"]["enabled"] = True
            with self.assertRaises(ValueError):
                PROXY.target(changed)
            refresh = deepcopy(original)
            refresh["metadata"]["generation"] = 8
            self.assertEqual(PROXY.committed(expected, refresh, observed=True), resource == "root")

    def test_copies_and_real_failed_prefix_refuse_later_writes_and_wrong_event(self):
        native = yaml.safe_load(
            (ROOT / "gitops/release-workflows/maintenance-resources-v1.yaml").read_text()
        )
        for resource in ("root", "child"):
            copy = RESPONSE.template(resource)
            original = next(
                t for t in native["spec"]["templates"] if t["name"] == "patch-" + resource
            )
            adapted = next(
                t for t in copy["spec"]["templates"] if t["name"] == "fault-patch-" + resource
            )
            self.assertEqual(adapted["resource"]["manifest"], original["resource"]["manifest"])
            self.assertEqual(adapted["resource"]["flags"][:-1], original["resource"]["flags"])
            self.assertEqual(adapted["activeDeadlineSeconds"], original["activeDeadlineSeconds"])
            workflow, workers, parameters, raw, lock, before, after, expected, events = evidence(
                resource
            )
            RESPONSE.ready(workflow, parameters, raw, lock, before, resource=resource)
            MODULE.denial.retained(before, after)
            self.assertEqual(
                RESPONSE.failed(workflow, workers, expected, events, after, resource=resource)[
                    "resource"
                ],
                resource,
            )
            bad = deepcopy(events)
            bad[0]["event"] = "upstream-failed"
            with self.assertRaises(AssertionError):
                RESPONSE.failed(workflow, workers, expected, bad, after, resource=resource)
            late = deepcopy(workflow)
            late["status"]["nodes"]["late"] = {
                "id": "late",
                "displayName": "pause-deployment",
                "type": "Pod",
                "phase": "Succeeded",
            }
            extra = [
                *workers,
                {
                    "nodeID": "late",
                    "uid": "late",
                    "account": "release-maintenance-pause",
                    "exitCode": 0,
                },
            ]
            with self.assertRaises(AssertionError):
                RESPONSE.failed(late, extra, expected, events, after, resource=resource)

    def test_cli_prepares_target_specific_private_assets_and_withholds_invalid_proof(self):
        for resource in ("root", "child"):
            workflow, workers, parameters, raw, _, before, after, expected, events = evidence(
                resource
            )
            prefix = "maintenance-" + resource + "-response"
            with tempfile.TemporaryDirectory() as temporary:
                fixture = Path(temporary)
                results = fixture / "results"
                results.mkdir()

                def save(name, value, directory=fixture):
                    (directory / (name + ".json")).write_text(json.dumps(value))

                for label, state in (("before", before), ("after", after)):
                    for key, value in state.items():
                        save(f"{prefix}-{label}.{key}", value)
                save("parent-parameters", parameters)
                (fixture / "parent-request.json").write_bytes(raw)
                for suffix, value in (
                    ("workflow", workflow),
                    ("target", expected["before"]),
                    ("workers", workers),
                    ("barrier-pods", {"items": []}),
                ):
                    save(prefix + "." + suffix, value)
                barrier = deepcopy(workflow)
                barrier["status"]["phase"] = barrier["status"]["nodes"][RESPONSE.BARRIER][
                    "phase"
                ] = "Running"
                del barrier["status"]["nodes"]["pause-" + resource]
                save(prefix + ".barrier", barrier)
                (fixture / f"{prefix}.state.txt").write_text("ready")
                with patch(
                    "sys.argv", ["fixture", "prepare", resource, str(fixture), str(results)]
                ):
                    MODULE.main()
                self.assertTrue((fixture / (prefix + "-tls/ledger-response.crt")).is_file())
                client = json.loads((fixture / f"{prefix}.client.json").read_text())
                self.assertIn(
                    RESPONSE.fixture_name(resource), client["clusters"][0]["cluster"]["server"]
                )
                pod, service = yaml.safe_load_all((fixture / f"{prefix}.proxy.yaml").read_text())
                self.assertFalse(pod["spec"]["automountServiceAccountToken"])
                self.assertEqual(pod["metadata"]["labels"], service["spec"]["selector"])
                (fixture / f"{prefix}.proxy.log").write_text(json.dumps(events[0]) + "\n")
                with patch("sys.argv", ["fixture", "verify", resource, str(fixture), str(results)]):
                    MODULE.main()
                outcome = results / f"{prefix}.outcome.json"
                self.assertEqual(
                    json.loads(outcome.read_text())["applicationUID"],
                    expected["before"]["metadata"]["uid"],
                )
                outcome.unlink()
                save(f"{prefix}-after.server-pods", {"items": []})
                with (
                    patch("sys.argv", ["fixture", "verify", resource, str(fixture), str(results)]),
                    self.assertRaises(AssertionError),
                ):
                    MODULE.main()
                self.assertFalse(outcome.exists())

    @unittest.skipUnless(BASH, "Bash required for actual adoption failure ordering")
    def test_shell_never_runs_fresh_adoption_after_unproven_response_or_retention(self):
        assert BASH
        library = (
            (FIXTURE / "maintenance-application-response.sh")
            .read_text()
            .split("\nfor maintenance_application_resource", 1)[0]
        )
        setup = r"""
set -euo pipefail
fixture=$1 results=$1/results source_dir=unused KUBECONFIG=$1/kubeconfig
mode=preflight-parent scenario=initial GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
current_stage=parent adoption_baseline=first adoption_retention_baseline=$fixture/maintenance-recovered
mkdir -p "$results"; touch "$fixture/owned" "$results/maintenance-recovery.outcome.txt"
fail=$3
start_stage() { echo "stage $*" >> "$fixture/trace"; }; complete_stage() { echo complete >> "$fixture/trace"; }
maintenance_write_snapshot() { :; }; maintenance_partial_workers() { echo '[]'; }
root_retained() {
  case "$2" in
    *-before) echo retain-before >> "$fixture/trace"; [[ $fail != baseline-retention ]] ;;
    *-after) echo retain-after >> "$fixture/trace"; [[ $fail != retention ]] ;;
    *) return 2 ;;
  esac
}
adoption_case() { echo adoption >> "$fixture/trace"; [[ $fail != adoption ]]; }
cmp() { echo compare >> "$fixture/trace"; [[ $fail != records ]]; }
k() { echo '{}'; }; a() { echo '{}'; }; argo() { :; }; jq() { cat >/dev/null; echo worker; }
timeout() { echo wait >> "$fixture/trace"; [[ $fail == unexpected ]] && return 0; [[ $fail == timeout ]] && return 124; return 1; }
uv() { echo "uv $*" >> "$fixture/trace"; [[ $* != *" $fail root "* ]]; }
source "$2"
maintenance_application_response_case root
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "cases.sh"
            source.write_text(library, newline="\n")
            script = directory / "run.sh"
            script.write_text(setup, newline="\n")
            for mode in (
                "pass",
                "baseline-retention",
                "prepare",
                "retention",
                "unexpected",
                "timeout",
                "verify",
                "adoption",
                "records",
            ):
                fixture = directory / mode
                fixture.mkdir()
                process = subprocess.run(
                    [BASH, script.as_posix(), fixture.as_posix(), source.as_posix(), mode],
                    cwd=ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                with self.subTest(mode=mode):
                    diagnostic = (
                        f"scenario={mode}, exit={process.returncode}, stderr={process.stderr}"
                    )
                    trace_path = fixture / "trace"
                    self.assertTrue(trace_path.is_file(), diagnostic)
                    trace = trace_path.read_text()
                    lines = trace.splitlines()
                    self.assertEqual(lines[0], "stage maintenance-root-response", diagnostic)
                    self.assertEqual(process.returncode, 0 if mode == "pass" else 1, diagnostic)
                    boundary = {
                        "pass": "complete",
                        "baseline-retention": "retain-before",
                        "retention": "retain-after",
                        "unexpected": "wait",
                        "timeout": "wait",
                        "adoption": "adoption",
                        "records": "compare",
                    }
                    if mode in boundary:
                        self.assertEqual(lines[-1], boundary[mode], diagnostic)
                    else:
                        self.assertIn(f" {mode} root ", lines[-1], diagnostic)
                    if mode == "retention":
                        self.assertTrue(any(" prepare root " in line for line in lines))
                        self.assertIn("wait", lines)
                        self.assertFalse(any(" verify root " in line for line in lines))
                    if mode == "baseline-retention":
                        self.assertFalse(any(line.startswith("uv ") for line in lines))
                    if mode not in {"pass", "adoption", "records"}:
                        self.assertNotIn("adoption\n", trace)
                    if mode != "pass":
                        self.assertNotIn("complete\n", trace)


class ApplicationAPI(deployment.API):
    def __init__(self, expected):
        HTTPServer.__init__(self, ("127.0.0.1", 0), ApplicationAPIHandler)
        self.expected = expected
        self.reads, self.writes = [], []
        self.status = 200


class ApplicationAPIHandler(deployment.APIHandler):
    def do_PATCH(self):
        self.server.writes.append(
            (
                self.path,
                self.headers["Authorization"],
                json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
            )
        )
        self.respond(deepcopy(self.server.expected["before"]))


class ApplicationResponseTransportTests(unittest.TestCase):
    start = deployment.MaintenancePatchTransportTests.start

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.cert, cls.key = RESPONSE.ledger.certificate(Path(cls.directory.name), "localhost")
        cls.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.tls.load_cert_chain(cls.cert, cls.key)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def use(self, resource):
        *_, self.expected, _ = evidence(resource)
        self.context = ssl.create_default_context(cafile=str(self.cert))
        self.api = ApplicationAPI(self.expected)
        self.api.socket = self.tls.wrap_socket(self.api.socket, server_side=True)
        self.start(self.api)
        self.proxy = PROXY.Proxy(
            ("127.0.0.1", 0), self.expected, f"localhost:{self.api.server_port}", self.context
        )
        self.proxy.tls = self.tls
        self.start(self.proxy)

    def call(self, method, path):
        connection = http.client.HTTPSConnection(
            "localhost", self.proxy.server_port, context=self.context, timeout=5
        )
        try:
            connection.request(
                method,
                path,
                json.dumps(self.expected["patch"]).encode(),
                {
                    "Authorization": "Bearer " + deployment.TOKEN,
                    "Content-Type": "application/json-patch+json",
                },
            )
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_each_proxy_forwards_only_its_application_once_even_when_version_is_unchanged(self):
        for resource in ("root", "child"):
            self.use(resource)
            route = PROXY.TARGETS[resource][4]
            for other in PROXY.TARGETS:
                if other != resource:
                    self.assertEqual(
                        self.call("GET", PROXY.TARGETS[other][4] + "?timeout=10s"), (403, b"")
                    )
                    self.assertEqual(
                        self.call("PATCH", PROXY.TARGETS[other][4] + "?timeout=10s"), (403, b"")
                    )
            self.assertFalse(self.api.reads or self.api.writes)
            self.assertEqual(self.call("GET", route + "?timeout=10s")[0], 200)
            with self.assertRaises(http.client.RemoteDisconnected):
                self.call("PATCH", route + "?timeout=10s&fieldManager=kubectl-patch")
            self.assertEqual(len(self.api.writes), 1)
            self.assertEqual(
                self.proxy.events[0]["resourceVersion"],
                self.expected["before"]["metadata"]["resourceVersion"],
            )
            self.assertEqual(self.call("PATCH", route + "?timeout=10s"), (410, b""))
            self.assertEqual(len(self.api.writes), 1)

    @unittest.skipUnless(deployment.KUBECTL, "kubectl required for actual Application discovery")
    def test_actual_kubectl_get_and_patch_use_bounded_application_route_and_drop_success(self):
        assert deployment.KUBECTL
        for resource in ("root", "child"):
            self.use(resource)
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
                    "users": [{"name": "caller", "user": {"token": deployment.TOKEN}}],
                    "contexts": [
                        {"name": "fault", "context": {"cluster": "fault", "user": "caller"}}
                    ],
                    "current-context": "fault",
                }
                (directory / "client.json").write_text(json.dumps(config))
                (directory / "patch.json").write_text(json.dumps(self.expected["patch"]))
                result = subprocess.run(
                    [
                        deployment.KUBECTL,
                        "--kubeconfig",
                        str(directory / "client.json"),
                        "--cache-dir",
                        str(directory / "cache"),
                        "patch",
                        "applications.argoproj.io",
                        self.expected["before"]["metadata"]["name"],
                        "--namespace=argocd",
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
                self.assertTrue(
                    all(
                        path == PROXY.TARGETS[resource][4] + "?timeout=10s"
                        for path in self.api.reads
                    )
                )
                self.assertEqual(len(self.proxy.events), 1)
                self.assertEqual(
                    self.proxy.events[0]["event"], resource + "-committed-response-dropped"
                )


if __name__ == "__main__":
    unittest.main()
