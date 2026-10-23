"""Complete adoption evidence must come from the actual parent and native records."""

import base64
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml
from test_readiness_parent_integration import MODULE as READINESS
from test_readiness_parent_integration import evidence as readiness_evidence

from tests.release_verifier_support import policy

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
SPEC = importlib.util.spec_from_file_location(
    "adoption_parent_evidence", FIXTURE / "adoption_parent_evidence.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"readiness_parent_evidence": READINESS}):
    SPEC.loader.exec_module(MODULE)


def evidence(first: bool = True) -> tuple:
    workflow, preflight, parameters, api, raw_lock = readiness_evidence()
    request = json.loads((ROOT / "tests/fixtures/release-request-v1.json").read_text())
    request["environment"]["clusterUID"] = api["clusterUID"]
    request["selection"]["revision"] = parameters["selection-revision"]
    request["selection"]["lockDigests"] = copy.deepcopy(preflight["lockDigests"])
    raw = (json.dumps(request, sort_keys=True) + "\n").encode()
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    parameters["request-sha256"] = preflight["requestDigest"] = digest
    nodes = workflow["status"]["nodes"]
    for name in ("parent", "verify", "preflight"):
        value = READINESS.outputs(nodes[name])["result"]
        value["requestDigest"] = digest
        nodes[name]["outputs"]["parameters"][0]["value"] = json.dumps(value)
    health = READINESS.outputs(nodes["parent"])["result"]
    nodes["readiness"] = nodes.pop("parent")
    nodes["readiness"].update(id="readiness", displayName="readiness")
    workflow["metadata"].update(name="adoption", uid="00000000-0000-0000-0000-000000000001")
    workflow["spec"]["workflowTemplateRef"]["name"] = "perfeng-release-adopt-existing-v2"
    workflow["spec"]["arguments"]["parameters"] = [
        {"name": k, "value": v} for k, v in parameters.items()
    ]
    root = yaml.safe_load((ROOT / "gitops/bootstrap/local-root-application.yaml").read_text())
    root["metadata"].update(uid="root-uid", resourceVersion="10")
    root["spec"]["syncPolicy"]["automated"] = {"enabled": False}
    root["status"] = {
        "sync": {
            "status": "Synced",
            "revision": parameters["selection-revision"],
            "comparedTo": {"source": copy.deepcopy(root["spec"]["source"])},
        },
        "health": {"status": "Healthy"},
        "operationState": {"phase": "Succeeded"},
    }
    plan = policy.plan_adoption(raw, health, root, None, None)
    receipt = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": plan["name"],
            "namespace": policy.NAMESPACE,
            "uid": "receipt-uid",
            "resourceVersion": "20",
            "labels": policy.labels("release-adoption"),
            "annotations": {
                "perfeng.io/first-workflow-uid": workflow["metadata"]["uid"]
                if first
                else "00000000-0000-0000-0000-000000000002",
                "perfeng.io/first-request-revision": parameters["request-revision"],
            },
        },
        "immutable": True,
        "binaryData": {"request.json": plan["request-b64"], "receipt.json": plan["receipt-b64"]},
    }
    pointer = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": policy.POINTER,
            "namespace": policy.NAMESPACE,
            "uid": "pointer-uid",
            "resourceVersion": "30",
            "labels": policy.labels("current-release"),
        },
        "data": {
            "requestDigest": digest,
            "clusterUID": api["clusterUID"],
            "receiptName": plan["name"],
            "receiptUID": "receipt-uid",
        },
    }
    api.update(receipt=receipt, pointer=pointer)

    def node(name: str, value: Any, output: str = "observation") -> None:
        nodes[name] = {
            "id": name,
            "displayName": name,
            "type": "Pod",
            "phase": "Succeeded",
            "outputs": {"parameters": [{"name": output, "value": json.dumps(value)}]},
        }

    for name, value in (
        ("root", root),
        ("root-after", root),
        ("final-root", root),
        ("current", None if first else pointer),
        ("existing-receipt", None if first else receipt),
        ("stored-receipt", receipt),
        ("final-receipt", receipt),
        ("stored-pointer", pointer),
    ):
        node(name, value)
    for phase, name, r, p in (
        ("plan", "plan", None if first else receipt, None if first else pointer),
        ("pointer", "pointer-plan", receipt, None if first else pointer),
        ("finish", "finish", receipt, pointer),
    ):
        values = policy.plan_adoption(raw, health, root, r, p, None if phase == "plan" else root)
        if phase != "finish":
            values["result"] = None
        nodes[name] = {
            "id": name,
            "displayName": name,
            "type": "Pod",
            "phase": "Succeeded",
            "outputs": {
                "parameters": [
                    {"name": k, "value": json.dumps(v) if k == "result" else v}
                    for k, v in values.items()
                ]
            },
        }
    node("adoption", values["result"], "result")
    nodes["adoption"]["type"] = "Steps"
    for name in ("create-receipt", "create-pointer"):
        nodes[name] = {
            "id": name,
            "displayName": name,
            "type": "Pod" if first else "Skipped",
            "phase": "Succeeded" if first else "Skipped",
        }
    return workflow, parameters, preflight, raw, raw_lock, api, first


class AdoptionParentIntegrationTests(unittest.TestCase):
    def test_cli_loads_published_policy_and_writes_only_the_verified_result(self):
        workflow, parameters, preflight, raw, _, api, _ = evidence()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, value in (
                ("parent-parameters.json", parameters),
                ("parent-first.result.json", preflight),
                ("adoption-parent-api.json", api),
                ("adoption-parent-first.workflow.json", workflow),
            ):
                (directory / name).write_text(json.dumps(value), encoding="utf-8")
            (directory / "parent-request.json").write_bytes(raw)
            result = subprocess.run(
                [
                    sys.executable,
                    str(FIXTURE / "adoption_parent_evidence.py"),
                    "success",
                    temporary,
                    temporary,
                    "first",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads((directory / "adoption-parent-first.result.json").read_text()),
                MODULE.success(*evidence()),
            )

    def test_first_and_repeat_bind_native_ledger_and_nested_readiness(self):
        for first in (True, False):
            args = evidence(first)
            result = MODULE.success(*args)
            self.assertEqual(result["receiptUID"], args[5]["receipt"]["metadata"]["uid"])
            self.assertEqual(result["pointerUID"], args[5]["pointer"]["metadata"]["uid"])

    def test_stale_partial_or_conflicting_success_is_refused(self):
        for change in (
            "root-version",
            "root-revision",
            "root-auto",
            "request-bytes",
            "stored-uid",
            "pointer-uid",
            "provenance",
            "missing-readiness",
            "early-result",
            "partial-result",
            "repeat-create",
            "receipt-bytes",
        ):
            with self.subTest(change=change):
                args = list(evidence(False))
                nodes = args[0]["status"]["nodes"]
                if change.startswith("root-"):
                    value = READINESS.outputs(nodes["final-root"])["observation"]
                    if change == "root-version":
                        value["metadata"]["resourceVersion"] = "11"
                    elif change == "root-revision":
                        value["status"]["sync"]["revision"] = "c" * 40
                    else:
                        value["spec"]["syncPolicy"]["automated"]["enabled"] = True
                    nodes["final-root"]["outputs"]["parameters"][0]["value"] = json.dumps(value)
                elif change == "request-bytes":
                    args[3] += b" "
                elif change == "stored-uid":
                    args[5]["receipt"]["metadata"]["uid"] = "replaced"
                elif change == "pointer-uid":
                    args[5]["pointer"]["data"]["receiptUID"] = "replaced"
                elif change == "provenance":
                    args[5]["receipt"]["metadata"]["annotations"][
                        "perfeng.io/first-workflow-uid"
                    ] = "not-a-uid"
                elif change == "missing-readiness":
                    nodes["readiness"]["phase"] = "Failed"
                elif change == "early-result":
                    next(
                        p for p in nodes["plan"]["outputs"]["parameters"] if p["name"] == "result"
                    )["value"] = "{}"
                elif change == "partial-result":
                    nodes["adoption"]["outputs"] = {"parameters": []}
                elif change == "repeat-create":
                    nodes["create-receipt"].update(type="Pod", phase="Succeeded")
                else:
                    args[5]["receipt"]["binaryData"]["request.json"] = base64.b64encode(
                        b"changed"
                    ).decode()
                with self.assertRaises((AssertionError, KeyError, ValueError)):
                    MODULE.success(*args)

    def test_root_forbidden_requires_readiness_and_no_downstream_or_result(self):
        workflow = evidence()[0]
        nodes = workflow["status"]["nodes"]
        for name in (
            "current",
            "existing-receipt",
            "plan",
            "create-receipt",
            "stored-receipt",
            "root-after",
            "pointer-plan",
            "create-pointer",
            "stored-pointer",
            "final-receipt",
            "final-root",
            "finish",
        ):
            del nodes[name]
        nodes["adoption"].update(phase="Failed", outputs={})
        nodes["root"].update(phase="Failed", outputs={})
        workflow["status"]["phase"] = "Failed"
        workers = [{"nodeID": "root", "exitCode": 1}]
        log = 'applications.argoproj.io "perfeng-local-root" is forbidden: User "system:serviceaccount:argo-workflows:release-adoption" cannot get resource "applications" in namespace "argocd"'
        MODULE.refusal(workflow, workers, log)
        for change in ("exit", "node", "log", "downstream", "result", "readiness"):
            with self.subTest(change=change):
                w, p, text = copy.deepcopy(workflow), copy.deepcopy(workers), log
                if change == "exit":
                    p[0]["exitCode"] = 254
                elif change == "node":
                    p[0]["nodeID"] = "wrong"
                elif change == "log":
                    text = "timed out"
                elif change == "downstream":
                    w["status"]["nodes"]["plan"] = {
                        "id": "plan",
                        "displayName": "plan",
                        "type": "Pod",
                    }
                elif change == "result":
                    w["status"]["nodes"]["adoption"]["outputs"] = {
                        "parameters": [{"name": "result", "value": "{}"}]
                    }
                else:
                    w["status"]["nodes"]["readiness"]["phase"] = "Failed"
                with self.assertRaises(AssertionError):
                    MODULE.refusal(w, p, text)

    def test_slice_reuses_production_parent_and_keeps_existing_boundaries(self):
        source = (FIXTURE / "adoption-parent.sh").read_text()
        self.assertEqual(
            re.findall(r"^\s*adoption_case (\S+) (\S+)", source, re.M),
            [
                ("receipt-only", "receipt-only"),
                ("recovered", "success"),
                ("first", "success"),
                ("repeat", "success"),
                ("missing-root-read", "refusal"),
                ("corrected", "success"),
                ("root-drift", "root-drift"),
                ("root-corrected", "success"),
                ("concurrent-a", "success"),
                ("concurrent-b", "success"),
                ("changed-request", "changed-request"),
                ("upgrade-request", "upgrade-request"),
                ("missing-receipt", "missing-receipt"),
                ("replaced-receipt", "replaced-receipt"),
            ],
        )
        self.assertIn("--from workflowtemplate/perfeng-release-adopt-existing-v2", source)
        self.assertNotIn("argo retry", source)
        self.assertNotIn("--subresource=status", source)
        self.assertNotIn("delete configmap ", source)
        self.assertIn(
            'argocd_sync perfeng-local-root adoption-root-corrected "$root_revision"', source
        )
        chain = (FIXTURE / "preflight-parent.sh").read_text()
        self.assertLess(
            chain.index('source "$source_dir/root.sh"'),
            chain.index('source "$source_dir/adoption-parent.sh"'),
        )

    def test_plan_refusal_requires_real_root_drift_reads_and_no_later_writes(self):
        workflow, _, _, _, _, api, _ = evidence(False)
        nodes = workflow["status"]["nodes"]
        for name in (
            "create-receipt",
            "stored-receipt",
            "root-after",
            "pointer-plan",
            "create-pointer",
            "stored-pointer",
            "final-receipt",
            "final-root",
            "finish",
        ):
            del nodes[name]
        root = READINESS.outputs(nodes["root"])["observation"]
        root["status"]["sync"]["status"] = "OutOfSync"
        root["status"]["resources"] = [
            {"kind": "Namespace", "name": "perf-sut", "status": "OutOfSync"}
        ]
        nodes["root"]["outputs"]["parameters"][0]["value"] = json.dumps(root)
        nodes["plan"].update(
            phase="Failed",
            outputs={"parameters": [{"name": "result", "valueFrom": {"path": "/tmp/result"}}]},
        )
        nodes["adoption"].update(phase="Failed", outputs={})
        workflow["status"]["phase"] = "Failed"
        workers = [{"nodeID": "plan", "exitCode": 2}]
        records = sorted([api["receipt"], api["pointer"]], key=lambda o: o["metadata"]["name"])
        log = "root is stale, syncing or not at the reviewed selection"
        MODULE.refusal(workflow, workers, log, step="plan", records=records)
        for change in (
            "exit",
            "node",
            "log",
            "readiness",
            "root",
            "selection",
            "records",
            "health",
            "condition",
            "other-drift",
            "result",
            "downstream",
        ):
            with self.subTest(change=change):
                w, p, stored, text = (
                    copy.deepcopy(workflow),
                    copy.deepcopy(workers),
                    copy.deepcopy(records),
                    log,
                )
                if change == "exit":
                    p[0]["exitCode"] = 1
                elif change == "node":
                    p[0]["nodeID"] = "root"
                elif change == "log":
                    text = "image pull failed"
                elif change == "readiness":
                    w["status"]["nodes"]["readiness"]["phase"] = "Failed"
                elif change in {"root", "selection", "health", "condition", "other-drift"}:
                    value = READINESS.outputs(w["status"]["nodes"]["root"])["observation"]
                    if change == "health":
                        value["status"]["health"]["status"] = "Degraded"
                    elif change == "condition":
                        value["status"]["conditions"] = [{"type": "ComparisonError"}]
                    elif change == "other-drift":
                        value["status"]["resources"][0]["name"] = "perf-platform"
                    else:
                        value["status"]["sync"]["status" if change == "root" else "revision"] = (
                            "Synced" if change == "root" else "c" * 40
                        )
                    w["status"]["nodes"]["root"]["outputs"]["parameters"][0]["value"] = json.dumps(
                        value
                    )
                elif change == "records":
                    stored[0]["metadata"]["uid"] = "replaced"
                elif change == "result":
                    w["status"]["nodes"]["plan"]["outputs"] = {
                        "parameters": [{"name": "result", "value": "{}"}]
                    }
                else:
                    w["status"]["nodes"]["create-pointer"] = {
                        "id": "write",
                        "displayName": "create-pointer",
                        "type": "Pod",
                    }
                with self.assertRaises(AssertionError):
                    MODULE.refusal(w, p, text, step="plan", records=stored)


if __name__ == "__main__":
    unittest.main()
