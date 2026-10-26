"""Require precise maintenance receipt refusal, fresh readiness and no pause worker."""

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from test_adoption_identity_refusal import MODULE as IDENTITY
from test_adoption_identity_refusal import fault as identity_fault
from test_adoption_request_refusal import request_fault
from test_maintenance_pause_integration import FIXTURE, MODULE, ROOT, evidence
from test_release_workflows_integration import BASH

from tests.release_verifier_support import runtime

REFUSAL = (
    "Adoption refused: inspect reviewed inputs, ownership and observations; no success evidence"
)


def fault(case):
    workflow, workers, parameters, preflight, raw, raw_lock, before, _ = evidence()
    baseline = deepcopy(before["records"])
    pointer, receipt = before["records"]
    if case == "missing-receipt":
        receipt = None
        before["records"] = [pointer]
    else:
        receipt["metadata"].update(uid="replacement-uid", resourceVersion="replacement-version")
    nodes = workflow["status"]["nodes"]
    nodes["receipt"]["outputs"]["parameters"][0]["value"] = json.dumps(receipt)
    for name in (
        "guard-root",
        "pause-root",
        "guard-child",
        "pause-child",
        "guard-deployment",
        "pause-deployment",
        "observe",
    ):
        del nodes[name]
    nodes["baseline"].update(
        phase="Failed",
        outputs={"parameters": [{"name": "result", "valueFrom": {"path": "/tmp/result"}}]},
        templateRef={"name": "perfeng-plan-release-adoption-v3", "template": "run"},
    )
    nodes["pause"].update(phase="Failed", outputs={})
    workflow["status"]["phase"] = "Failed"
    workers = [w for w in workers if w["nodeID"] in nodes]
    next(w for w in workers if w["nodeID"] == "baseline")["exitCode"] = 2
    return [
        case,
        workflow,
        workers,
        parameters,
        preflight,
        raw,
        raw_lock,
        before,
        baseline,
        REFUSAL + "\n",
    ]


class MaintenanceReceiptRefusalTests(unittest.TestCase):
    def test_actual_runtime_redaction_is_accepted_by_upstream_adoption_gates(self):
        for case in ("missing-receipt", "replaced-receipt", "changed-request", "upgrade-request"):
            with self.subTest(case=case):
                args = identity_fault(case) if case.endswith("receipt") else request_fault(case)
                workflow = args[1]
                nodes = workflow["status"]["nodes"]

                def outputs(name, nodes=nodes):
                    return json.loads(nodes[name]["outputs"]["parameters"][0]["value"])

                values = {
                    "readiness": outputs("readiness"),
                    "root": outputs("root"),
                    "receipt": outputs("existing-receipt"),
                    "pointer": outputs("current"),
                    "root_after": None,
                }
                stderr = io.StringIO()
                raw = args[4] if case.endswith("receipt") else args[3]
                with (
                    patch.object(runtime, "request_bytes", return_value=raw),
                    patch.object(runtime, "inputs", return_value=values),
                    redirect_stderr(stderr),
                ):
                    self.assertEqual(runtime.cli("adoption"), 2)
                self.assertEqual(stderr.getvalue(), REFUSAL + "\n")
                args[8] = stderr.getvalue()
                report = (
                    IDENTITY.refusal(*args)
                    if case.endswith("receipt")
                    else IDENTITY.request_refusal(*args)
                )
                self.assertEqual(report["decision"], case + "-refused")

    def test_both_receipt_faults_prove_baseline_refusal_without_checkpoint(self):
        for case in ("missing-receipt", "replaced-receipt"):
            with self.subTest(case=case):
                report = MODULE.refusal(*fault(case))
                self.assertEqual(report["decision"], "maintenance-" + case + "-refused")
                self.assertEqual(report["originalReceiptUID"], "receipt-uid")
                self.assertEqual(
                    report["observedReceiptUID"],
                    None if case == "missing-receipt" else "replacement-uid",
                )
                self.assertNotIn("status", report)
                self.assertNotIn("result", report)
                args = fault(case)
                args[1]["status"]["phase"] = "Error"
                args[1]["status"]["nodes"]["baseline"]["phase"] = "Error"
                self.assertEqual(MODULE.refusal(*args)["decision"], report["decision"])

    def test_unrelated_failures_stale_evidence_writes_and_success_outputs_never_qualify(self):
        for case in ("missing-receipt", "replaced-receipt"):
            for change in (
                "readiness",
                "parameters",
                "namespace",
                "pointer",
                "record",
                "root",
                "worker",
                "exit",
                "log",
                "planner",
                "order",
                "write",
                "hidden-write",
                "checkpoint",
                "parent-result",
                "baseline-result",
                "infrastructure",
            ):
                with self.subTest(case=case, change=change):
                    args = fault(case)
                    _, workflow, workers, parameters, _, _, _, before, _, _ = args
                    nodes = workflow["status"]["nodes"]
                    if change == "readiness":
                        nodes["readiness"]["phase"] = "Failed"
                    if change == "parameters":
                        parameters["selection-revision"] = "c" * 40
                    if change == "namespace":
                        before["namespace"]["metadata"]["uid"] = "other-cluster"
                    if change == "pointer":
                        before["records"][0]["metadata"]["uid"] = "changed-pointer"
                    if change == "record":
                        before["records"] = []
                    if change == "root":
                        before["applications"]["items"][0]["metadata"]["uid"] = "new-root"
                    if change == "worker":
                        workers[0]["account"] = "release-adoption"
                    if change == "exit":
                        next(w for w in workers if w["nodeID"] == "baseline")["exitCode"] = 64
                    if change == "log":
                        args[-1] = (
                            "Adoption refused: invalid selection, observations or operational response\n"
                        )
                    if change == "planner":
                        nodes["baseline"]["templateRef"]["name"] = "other-planner"
                    if change == "order":
                        nodes["baseline"]["startedAt"] = nodes["readiness"]["startedAt"]
                    if change in {"write", "hidden-write"}:
                        name = "pause-root" if change == "write" else "unexpected"
                        nodes[name] = {
                            "id": name,
                            "displayName": name,
                            "type": "Pod",
                            "phase": "Succeeded",
                            "templateRef": {
                                "name": "perfeng-maintenance-resources-v1",
                                "template": "patch-root",
                            },
                        }
                        workers.append(
                            {
                                "nodeID": name,
                                "uid": "extra-worker",
                                "account": "release-maintenance-pause",
                                "exitCode": 0,
                            }
                        )
                    if change in {"checkpoint", "parent-result", "baseline-result"}:
                        node = nodes[
                            "readiness"
                            if change == "checkpoint"
                            else "pause"
                            if change == "parent-result"
                            else "baseline"
                        ]
                        node["outputs"] = {
                            "parameters": [
                                {
                                    "name": "result",
                                    "value": json.dumps({"kind": "PerfengMaintenanceCheckpoint"}),
                                }
                            ]
                        }
                    if change == "infrastructure":
                        nodes["baseline"]["phase"] = "Error"
                        next(w for w in workers if w["nodeID"] == "baseline")["exitCode"] = None
                    with self.assertRaises((AssertionError, KeyError, ValueError)):
                        MODULE.refusal(*args)

    def test_replaced_uid_requires_identical_payload_and_provenance(self):
        for change in ("uid", "payload", "provenance"):
            with self.subTest(change=change):
                args = fault("replaced-receipt")
                receipt = args[7]["records"][1]
                if change == "uid":
                    receipt["metadata"]["uid"] = "receipt-uid"
                if change == "payload":
                    receipt["binaryData"]["request.json"] = "changed"
                if change == "provenance":
                    receipt["metadata"]["annotations"]["perfeng.io/first-workflow-uid"] = (
                        "other-writer"
                    )
                args[1]["status"]["nodes"]["receipt"]["outputs"]["parameters"][0]["value"] = (
                    json.dumps(receipt)
                )
                with self.assertRaises((AssertionError, ValueError)):
                    MODULE.refusal(*args)

    def test_cli_preserves_private_snapshots_and_publishes_only_validated_refusal(self):
        mapping = {
            "serverPod": "server-pods",
            "pods": "pods",
            "server": "server",
            "workloads": "workloads",
            "records": "records",
            "secrets": "secrets",
            "config": "config",
            "git": "git",
            "applications": "applications",
            "deployment": "deployment",
            "namespace": "namespace",
        }
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            results = fixture / "results"
            results.mkdir()
            for case in ("missing-receipt", "replaced-receipt"):
                _, workflow, workers, parameters, preflight, raw, _, before, baseline, log = fault(
                    case
                )
                for label in ("before", "after"):
                    for key, suffix in mapping.items():
                        (fixture / f"maintenance-{case}-{label}.{suffix}.json").write_text(
                            json.dumps(before[key]), encoding="utf-8"
                        )
                for path, value in (
                    (fixture / "parent-parameters.json", parameters),
                    (fixture / "adoption-identity-baseline.json", baseline),
                    (results / "parent-first.result.json", preflight),
                    (results / f"maintenance-{case}.workflow.json", workflow),
                    (results / f"maintenance-{case}.workers.json", workers),
                ):
                    path.write_text(json.dumps(value), encoding="utf-8")
                (fixture / "parent-request.json").write_bytes(raw)
                (fixture / f"maintenance-{case}.worker.log").write_text(log, encoding="utf-8")
                originals = {p: p.read_bytes() for p in fixture.glob("*.json")}
                with patch.object(sys, "argv", ["evidence", case, str(fixture), str(results)]):
                    MODULE.main()
                self.assertEqual({p: p.read_bytes() for p in originals}, originals)
                output = results / f"maintenance-{case}.outcome.json"
                self.assertNotIn("credential", output.read_text())
                self.assertNotIn("private", output.read_text())
                output.unlink()
                path = fixture / f"maintenance-{case}-after.records.json"
                path.write_text("[]", encoding="utf-8")
                with (
                    patch.object(sys, "argv", ["evidence", case, str(fixture), str(results)]),
                    self.assertRaises(AssertionError),
                ):
                    MODULE.main()
                self.assertFalse(output.exists())

    @unittest.skipUnless(BASH, "Bash is required for fixture orchestration")
    def test_shell_keeps_existing_fault_windows_initial_only_and_rejects_wait_failures(self):
        assert BASH
        source = (FIXTURE / "maintenance-pause.sh").read_text()
        function = re.search(r"^maintenance_baseline_refusal\(\) \{.*?^\}", source, re.M | re.S)
        assert function
        shell = r"""
set -euo pipefail
fixture=$1 results=$1/results source_dir=unused KUBECONFIG=$1/kubeconfig
mode=preflight-parent scenario=$2 current_stage=preflight-parent wait_code=$3
adoption_retention_baseline=$1/maintenance-recovered
mkdir -p "$results"
touch "$fixture/owned" "$results/maintenance-recovery.outcome.txt"
start_stage() { current_stage=$1; echo "stage=$1"; }
complete_stage() { echo completed; }
maintenance_snapshot() { echo snapshot; }
root_state() { :; }
root_retained() { echo retained; }
adoption_retained() { echo retained-adoption; }
cmp() { return 0; }
a() { echo '{"metadata":{"name":"refusal-ci"}}'; }
k() { echo '{}'; }
jq() { cat > /dev/null; echo fixture-id; }
timeout() { return "$wait_code"; }
uv() { echo verified-refusal; }
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "refusal.sh"
            script.write_text(
                shell + function.group() + "\nmaintenance_baseline_refusal missing-receipt\n",
                encoding="utf-8",
                newline="\n",
            )
            for scenario, wait_code, expected in (
                ("initial", 1, 0),
                ("initial", 0, 1),
                ("initial", 124, 1),
                ("initial", 137, 1),
                ("receipt-only", 1, 2),
            ):
                with self.subTest(scenario=scenario, wait_code=wait_code):
                    fixture = directory / f"{scenario}-{wait_code}"
                    fixture.mkdir()
                    result = subprocess.run(
                        [BASH, script.as_posix(), fixture.as_posix(), scenario, str(wait_code)],
                        cwd=ROOT,
                        env={**os.environ, "BASH_ENV": ""},
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertEqual("verified-refusal" in result.stdout, expected == 0)
        adoption = (FIXTURE / "adoption-parent.sh").read_text()
        self.assertLess(
            adoption.index("adoption_case missing-receipt missing-receipt"),
            adoption.index("maintenance_baseline_refusal missing-receipt"),
        )
        self.assertLess(
            adoption.index("maintenance_baseline_refusal missing-receipt"),
            adoption.index('k create -f "$fixture/adoption-receipt-recreate.json"'),
        )
        self.assertLess(
            adoption.index("adoption_case replaced-receipt replaced-receipt"),
            adoption.index("maintenance_baseline_refusal replaced-receipt"),
        )
