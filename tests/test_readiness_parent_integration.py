"""Complete-parent evidence and refusal checks without live infrastructure."""

import copy
import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path
from typing import Any

from jsonschema import ValidationError

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
SPEC = importlib.util.spec_from_file_location(
    "readiness_parent_evidence", FIXTURE / "readiness_parent_evidence.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, str], dict[str, Any], bytes]:
    """Build schema-valid controller-shaped evidence with distinct actual identities."""
    raw = (ROOT / "deployments/local-control-plane.json").read_bytes()
    lock = json.loads(raw)
    observations = json.loads(
        (ROOT / "tests/fixtures/release-readiness-observations.json").read_text()
    )
    parameters = {
        "request-revision": "a" * 40,
        "selection-revision": "b" * 40,
        "request-sha256": "sha256:" + "c" * 64,
        "request-path": "deployments/release-requests/ci.json",
    }
    preflight = {
        "schemaVersion": 1,
        "environment": "perfeng-local",
        "requestPath": parameters["request-path"],
        "kind": "PerfengReleasePreflight",
        "status": "identity-verified",
        "requestRevision": parameters["request-revision"],
        "selectionRevision": parameters["selection-revision"],
        "requestDigest": parameters["request-sha256"],
        "clusterUID": "00000000-0000-0000-0000-000000000003",
        "lockDigests": {
            path: "sha256:" + hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                "deployments/local-control-plane.json",
                "deployments/local-infrastructure.json",
                "deployments/local-native-target.json",
            )
        },
    }
    resources = []
    platform = []
    nodes = {}
    for name, observation in observations.items():
        nodes[name] = {
            "id": name,
            "displayName": name,
            "type": "Pod",
            "phase": "Succeeded",
            "outputs": {"parameters": [{"name": "observation", "value": json.dumps(observation)}]},
        }
    for key, kind, name in (
        ("postgres", "StatefulSet", "postgres"),
        ("seaweedfs", "StatefulSet", "seaweedfs"),
        ("deployment", "Deployment", "control-plane"),
    ):
        o = observations[key]
        resources.append(
            {
                "kind": kind,
                "namespace": "perf-platform",
                "name": name,
                **{k: o[k] for k in ("uid", "generation", "observedGeneration")},
            }
        )
        platform.append(
            {
                "kind": kind,
                "metadata": {"name": name, "uid": o["uid"], "generation": o["generation"]},
                "status": {"observedGeneration": o["observedGeneration"]},
            }
        )
    platform.append(
        {
            "kind": "Service",
            "metadata": {"name": "control-plane", "uid": observations["service"]["uid"]},
        }
    )
    api = {
        "clusterUID": preflight["clusterUID"],
        "platform": platform,
        "application": {"metadata": {"uid": observations["application"]["uid"]}},
    }
    result = {
        "schemaVersion": 1,
        "kind": "PerfengReleaseReadiness",
        "scope": "control-plane-foundation",
        "status": "readiness-verified",
        **{
            k: preflight[k]
            for k in ("requestRevision", "selectionRevision", "requestDigest", "clusterUID")
        },
        "applicationUID": observations["application"]["uid"],
        "serviceUID": observations["service"]["uid"],
        "deploymentRevision": lock["deployment"]["revision"],
        "image": lock["controlPlane"]["image"],
        "resources": resources,
        "checkedEndpoints": ["livez", "readyz", "metrics"],
    }
    for name, kind, value in (
        ("parent", "Steps", result),
        ("verify", "Pod", result),
        ("preflight", "Steps", preflight),
    ):
        nodes[name] = {
            "id": name,
            "displayName": name,
            "type": kind,
            "phase": "Succeeded",
            "outputs": {"parameters": [{"name": "result", "value": json.dumps(value)}]},
        }
    nodes["cluster-uid"] = {
        "id": "cluster-uid",
        "displayName": "cluster-uid",
        "type": "Pod",
        "phase": "Succeeded",
        "outputs": {"parameters": [{"name": "uid", "value": preflight["clusterUID"]}]},
    }
    workflow = {
        "metadata": {"name": "parent", "uid": "workflow-uid"},
        "spec": {
            "workflowTemplateRef": {"name": "perfeng-release-readiness-v2"},
            "arguments": {"parameters": [{"name": k, "value": v} for k, v in parameters.items()]},
        },
        "status": {"phase": "Succeeded", "nodes": nodes},
    }
    return workflow, preflight, parameters, api, raw


def refused(step):
    workflow, preflight, *_ = evidence()
    nodes = workflow["status"]["nodes"]
    workflow["status"]["phase"] = nodes["parent"]["phase"] = "Failed"
    unresolved = {
        "parameters": [{"name": "result", "valueFrom": {"path": "/tmp/readiness-result.json"}}]
    }
    nodes["parent"]["outputs"] = copy.deepcopy(unresolved)
    for name in ("deployment", "service", "verify"):
        del nodes[name]
    if step == "verify-identities":
        for name in ("postgres", "seaweedfs", "application"):
            del nodes[name]
        nodes["preflight"]["phase"] = "Failed"
        nodes["preflight"]["outputs"] = copy.deepcopy(unresolved)
    nodes[step] = {
        "id": step,
        "displayName": step,
        "type": "Pod",
        "phase": "Failed",
        "outputs": copy.deepcopy(unresolved),
    }
    workers = [
        {
            "nodeID": step,
            "exitCode": 2 if step == "verify-identities" else 64,
            "account": "release-readiness",
            "uid": "worker-uid",
        }
    ]
    log = (
        "Preflight refused: request bytes differ from the submitted digest\n"
        if step == "verify-identities"
        else 'Error from server (Forbidden): applications.argoproj.io "perfeng-local-control-plane" '
        'is forbidden: User "system:serviceaccount:argo-workflows:release-readiness" cannot get '
        'resource "applications" in API group "argoproj.io" in the namespace "argocd"'
    )
    if step == "application":
        # Native worker contract observed in trusted-main run 40207959980.
        nodes[step]["message"] = "main: Error (exit code 64): no more retries " + log
        nodes[step]["templateRef"] = {
            "name": "perfeng-observe-release-readiness-v1",
            "template": "application",
        }
    return workflow, workers, log, preflight


class ReadinessParentIntegrationTests(unittest.TestCase):
    def test_success_binds_request_native_observations_and_actual_resource_identity(self):
        original = evidence()
        MODULE.success(*original)
        for change in (
            "cluster",
            "request",
            "lock",
            "uid",
            "generation",
            "observation",
            "nested",
            "child",
            "endpoints",
        ):
            with self.subTest(change=change):
                workflow, preflight, parameters, api, raw = copy.deepcopy(original)
                nodes = workflow["status"]["nodes"]
                if change == "cluster":
                    api["clusterUID"] = "foreign"
                elif change == "request":
                    parameters["selection-revision"] = "0" * 40
                elif change == "lock":
                    raw += b"\n"
                elif change == "uid":
                    api["platform"][0]["metadata"]["uid"] = "foreign"
                elif change == "generation":
                    api["platform"][0]["metadata"]["generation"] += 1
                elif change == "observation":
                    nodes["postgres"]["outputs"]["parameters"][0]["value"] = "{}"
                elif change == "nested":
                    nodes["preflight"]["outputs"]["parameters"][0]["value"] = "{}"
                elif change == "child":
                    nodes["verify"]["phase"] = "Failed"
                else:
                    value = json.loads(nodes["parent"]["outputs"]["parameters"][0]["value"])
                    value["checkedEndpoints"] = ["livez"]
                    for name in ("parent", "verify"):
                        nodes[name]["outputs"]["parameters"][0]["value"] = json.dumps(value)
                with self.assertRaises((AssertionError, KeyError, ValidationError)):
                    MODULE.success(workflow, preflight, parameters, api, raw)

    def test_refusals_allow_nested_preflight_but_reject_wrong_worker_downstream_or_result(self):
        for step in ("verify-identities", "application"):
            workflow, workers, log, preflight = refused(step)
            MODULE.refusal(workflow, workers, step, log, preflight)
            for change in ("exit", "worker", "timeout", "downstream", "result", "root-result"):
                with self.subTest(step=step, change=change):
                    w, p = copy.deepcopy((workflow, workers))
                    if change == "exit":
                        p[0]["exitCode"] = 254
                    elif change == "worker":
                        p[0]["nodeID"] = "unrelated"
                    elif change == "timeout":
                        log_value = "connection timed out"
                    elif change == "downstream":
                        w["status"]["nodes"]["verify"] = {
                            "id": "verify",
                            "type": "Pod",
                            "displayName": "verify",
                            "phase": "Running",
                        }
                    else:
                        target = "parent" if change == "root-result" else step
                        w["status"]["nodes"][target]["outputs"] = {
                            "parameters": [
                                {"name": "result", "value": '{"kind":"PerfengReleaseReadiness"}'}
                            ]
                        }
                    with self.assertRaises(AssertionError):
                        MODULE.refusal(
                            w, p, step, log_value if change == "timeout" else log, preflight
                        )

    def test_native_forbidden_requires_exact_executor_identity_and_diagnostic(self):
        original = refused("application")
        for structured in (False, True):
            workflow, workers, log, preflight = copy.deepcopy(original)
            if structured:
                log = json.dumps({"level": "fatal", "msg": "no more retries " + log})
            MODULE.refusal(workflow, workers, "application", log, preflight)
        for change in ("legacy-exit", "account", "uid", "template", "node-message", "log-message"):
            with self.subTest(change=change):
                workflow, workers, log, preflight = copy.deepcopy(original)
                if change == "legacy-exit":
                    workers[0]["exitCode"] = 1
                elif change == "account":
                    workers[0]["account"] = "other"
                elif change == "uid":
                    workers[0]["uid"] = ""
                elif change == "template":
                    workflow["status"]["nodes"]["application"]["templateRef"]["template"] = "other"
                elif change == "node-message":
                    workflow["status"]["nodes"]["application"]["message"] = "connection timed out"
                else:
                    log = "connection timed out"
                with self.assertRaises(AssertionError):
                    MODULE.refusal(workflow, workers, "application", log, preflight)
        for old, new in (
            ("(Forbidden)", "(Unauthorized)"),
            ("perfeng-local-control-plane", "other-application"),
            ("release-readiness", "release-adoption"),
            ("cannot get", "cannot patch"),
            ('resource "applications"', 'resource "appprojects"'),
            ('API group "argoproj.io"', 'API group "other.io"'),
            ('namespace "argocd"', 'namespace "argo-workflows"'),
        ):
            with self.subTest(old=old, new=new):
                workflow, workers, log, preflight = copy.deepcopy(original)
                log = log.replace(old, new)
                workflow["status"]["nodes"]["application"]["message"] = (
                    "main: Error (exit code 64): no more retries " + log
                )
                with self.assertRaises(AssertionError):
                    MODULE.refusal(workflow, workers, "application", log, preflight)

    def test_phase_uses_complete_parent_after_real_argocd_without_synthetic_parameters(self):
        source = (FIXTURE / "readiness-parent.sh").read_text()
        self.assertIn("--from workflowtemplate/perfeng-release-readiness-v2", source)
        self.assertNotIn("-p preflight-result", source)
        self.assertNotIn("argo retry", source)
        self.assertEqual(
            re.findall(r"^readiness_parent_case (\S+) \S+ (\S+)", source, re.M),
            [
                ("first", "success"),
                ("repeat", "success"),
                ("wrong-digest", "verify-identities"),
                ("missing-read-grant", "application"),
                ("corrected", "success"),
            ],
        )
        chain = (FIXTURE / "preflight-parent.sh").read_text()
        self.assertLess(
            chain.index('source "$source_dir/argocd.sh"'),
            chain.index('source "$source_dir/readiness-parent.sh"'),
        )
        self.assertIn(
            'names.add(("WorkflowTemplate", "perfeng-release-readiness-v2"))',
            (FIXTURE / "run.sh").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
