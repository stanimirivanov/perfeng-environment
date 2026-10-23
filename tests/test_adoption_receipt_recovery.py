"""Full-parent receipt-only recovery preserves persisted bytes and original identity."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from test_adoption_parent_integration import FIXTURE, MODULE, READINESS, evidence

from tests.release_verifier_support import policy


def receipt_only_failure():
    args = list(evidence(True))
    workflow, _, _, _, _, api, _ = args
    workflow["status"]["phase"] = "Failed"
    nodes = workflow["status"]["nodes"]
    nodes["adoption"].update(phase="Failed", outputs={})
    nodes["create-pointer"].update(phase="Failed", outputs={})
    for name in ("stored-pointer", "final-receipt", "final-root", "finish"):
        del nodes[name]
    api["pointer"] = None
    workers = [{"nodeID": "create-pointer", "exitCode": 1}]
    log = 'configmaps "perfeng-local-current" is forbidden: exceeded quota: ci-adoption-receipt-only, requested: count/configmaps=1, used: count/configmaps=2, limited: count/configmaps=2'
    return args[:6], workers, log


def recovered():
    args = list(evidence(False))
    workflow, _, _, raw, _, api, _ = args
    workflow["metadata"]["uid"] = "00000000-0000-0000-0000-000000000002"
    receipt = api["receipt"]
    receipt["metadata"]["annotations"]["perfeng.io/first-workflow-uid"] = (
        "00000000-0000-0000-0000-000000000001"
    )
    nodes = workflow["status"]["nodes"]
    for name in ("existing-receipt", "stored-receipt", "final-receipt"):
        nodes[name]["outputs"]["parameters"][0]["value"] = json.dumps(receipt)
    nodes["current"]["outputs"]["parameters"][0]["value"] = "null"
    health = READINESS.outputs(nodes["readiness"])["result"]
    root = READINESS.outputs(nodes["root"])["observation"]
    for name in ("plan", "pointer-plan"):
        expected = policy.plan_adoption(
            raw, health, root, receipt, None, root if name == "pointer-plan" else None
        )
        expected["result"] = None
        nodes[name]["outputs"]["parameters"] = [
            {"name": k, "value": json.dumps(v) if k == "result" else v} for k, v in expected.items()
        ]
    nodes["create-pointer"].update(type="Pod", phase="Succeeded")
    return args


def quotas():
    original = yaml.safe_load((FIXTURE / "adoption-receipt-quota.yaml").read_text())
    original["metadata"].update(uid="quota-uid", resourceVersion="1")
    current = copy.deepcopy(original)
    current["metadata"]["resourceVersion"] = "3"
    current["status"] = {"hard": {"count/configmaps": "2"}, "used": {"count/configmaps": "2"}}
    return original, current


class AdoptionReceiptRecoveryTests(unittest.TestCase):
    def test_committed_receipt_and_only_quota_pointer_failure_are_required(self):
        args, workers, log = receipt_only_failure()
        MODULE.partial(*args, workers, log)
        for change in (
            "worker",
            "exit",
            "log",
            "receipt",
            "pointer",
            "provenance",
            "create-receipt",
            "early-result",
            "finish",
        ):
            with self.subTest(change=change):
                values, p, text = copy.deepcopy(args), copy.deepcopy(workers), log
                nodes = values[0]["status"]["nodes"]
                if change == "worker":
                    p[0]["nodeID"] = "plan"
                elif change == "exit":
                    p[0]["exitCode"] = 254
                elif change == "log":
                    text = "connection refused"
                elif change == "receipt":
                    values[5]["receipt"]["metadata"]["uid"] = "replaced"
                elif change == "pointer":
                    values[5]["pointer"] = {"metadata": {"uid": "exists"}}
                elif change == "provenance":
                    values[0]["metadata"]["uid"] = "foreign"
                elif change == "create-receipt":
                    nodes["create-receipt"]["phase"] = "Failed"
                elif change == "early-result":
                    next(
                        p
                        for p in nodes["pointer-plan"]["outputs"]["parameters"]
                        if p["name"] == "result"
                    )["value"] = "{}"
                else:
                    nodes["finish"] = {"id": "finish", "displayName": "finish", "type": "Pod"}
                with self.assertRaises((AssertionError, KeyError, ValueError)):
                    MODULE.partial(*values, p, text)

    def test_fresh_recovery_creates_only_pointer_and_keeps_failed_workflow_provenance(self):
        args = recovered()
        result = MODULE.success(*args, receipt_only=True)
        partial_args, _, _ = receipt_only_failure()
        self.assertEqual(args[5]["receipt"], partial_args[5]["receipt"])
        self.assertEqual(result["receiptUID"], partial_args[5]["receipt"]["metadata"]["uid"])
        with self.assertRaises(AssertionError):
            MODULE.success(*args)
        for name in ("create-receipt", "create-pointer"):
            bad = copy.deepcopy(args)
            bad[0]["status"]["nodes"][name]["phase"] = (
                "Skipped" if name == "create-pointer" else "Succeeded"
            )
            with self.assertRaises(AssertionError):
                MODULE.success(*bad, receipt_only=True)

    def test_quota_increase_requires_original_uid_fresh_version_and_initialized_usage(self):
        original, current = quotas()
        patch = MODULE.quota_patch(original, current)
        self.assertEqual(
            patch[:2],
            [
                {"op": "test", "path": "/metadata/uid", "value": "quota-uid"},
                {"op": "test", "path": "/metadata/resourceVersion", "value": "3"},
            ],
        )
        self.assertEqual(
            [p for p in patch if p["op"] != "test"],
            [{"op": "replace", "path": "/spec/hard", "value": {"count/configmaps": "3"}}],
        )
        for field, value in (
            ("metadata.uid", "replaced"),
            ("metadata.namespace", "other"),
            ("metadata.deletionTimestamp", "now"),
            ("metadata.resourceVersion", ""),
            ("metadata.labels", {}),
            ("spec.hard", {"count/configmaps": "3"}),
            ("status.used", {"count/configmaps": "1"}),
        ):
            with self.subTest(field=field):
                bad = copy.deepcopy(current)
                first, second = field.split(".")
                bad[first][second] = value
                with self.assertRaises(AssertionError):
                    MODULE.quota_patch(original, bad)

    def test_cli_checks_partial_parent_and_emits_guarded_quota_patch(self):
        args, workers, log = receipt_only_failure()
        workflow, parameters, preflight, raw, _, api = args
        original, current = quotas()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            for name, value in (
                ("parent-parameters.json", parameters),
                ("parent-first.result.json", preflight),
                ("adoption-parent-receipt-only.workflow.json", workflow),
                ("adoption-parent-receipt-only.workers.json", workers),
                ("adoption-parent-api.json", api),
                ("adoption-quota-created.json", original),
                ("adoption-quota-current.json", current),
            ):
                (path / name).write_text(json.dumps(value), encoding="utf-8")
            (path / "parent-request.json").write_bytes(raw)
            (path / "adoption-parent-worker.log").write_text(log, encoding="utf-8")
            for mode in ("receipt-only", "quota-patch"):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(FIXTURE / "adoption_parent_evidence.py"),
                        mode,
                        temporary,
                        temporary,
                        "receipt-only",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    env={**os.environ, "PYTHONPATH": str(FIXTURE.parents[2])},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                if mode == "quota-patch":
                    self.assertEqual(
                        json.loads(result.stdout), MODULE.quota_patch(original, current)
                    )
