"""Maintenance must reject changed request identity/intent while retaining its baseline."""

import hashlib
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

from test_maintenance_pause_integration import FIXTURE, MODULE, ROOT
from test_maintenance_receipt_refusal import REFUSAL
from test_maintenance_receipt_refusal import fault as receipt_fault
from test_release_workflows_integration import BASH

from tests.release_verifier_support import runtime


def replace_request(args, raw):
    """Keep independent digest/preflight/readiness fields consistent for semantic mutations."""
    _, workflow, _, parameters, _, _, _, _, _, _, _ = args
    args[4] = raw
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    parameters["request-sha256"] = digest
    workflow["spec"]["arguments"]["parameters"] = [
        {"name": k, "value": v} for k, v in parameters.items()
    ]
    nodes = workflow["status"]["nodes"]
    for name in ("preflight", "readiness"):
        value = json.loads(nodes[name]["outputs"]["parameters"][0]["value"])
        value["requestDigest"] = digest
        if name == "preflight":
            value["requestPath"] = parameters["request-path"]
        nodes[name]["outputs"]["parameters"][0]["value"] = json.dumps(value)


def fault(case):
    _, workflow, workers, parameters, _, original, raw_lock, before, baseline, log = receipt_fault(
        "missing-receipt"
    )
    original_parameters = deepcopy(parameters)
    before["records"] = deepcopy(baseline)
    before["requestedReceipt"] = None
    candidate = json.loads(original)
    if case == "changed-request":
        raw = original + b"\n"
    else:
        candidate["promotion"]["expectedCurrent"] = original_parameters["request-sha256"]
        raw = (json.dumps(candidate, indent=2) + "\n").encode()
    parameters["request-path"] = f"deployments/release-requests/ci-{case}-v1.json"
    args = [
        case,
        workflow,
        workers,
        parameters,
        raw,
        original,
        original_parameters,
        raw_lock,
        before,
        baseline,
        log,
    ]
    replace_request(args, raw)
    return args


class MaintenanceRequestRefusalTests(unittest.TestCase):
    def test_both_requests_have_precise_runtime_refusal_and_retained_baseline(self):
        for case in ("changed-request", "upgrade-request"):
            with self.subTest(case=case):
                args = fault(case)
                nodes = args[1]["status"]["nodes"]
                values = {
                    key: json.loads(nodes[name]["outputs"]["parameters"][0]["value"])
                    for key, name in (
                        ("readiness", "readiness"),
                        ("root", "root"),
                        ("root_after", "root-after"),
                        ("receipt", "receipt"),
                        ("pointer", "pointer"),
                    )
                }
                stderr = io.StringIO()
                with (
                    patch.object(runtime, "request_bytes", return_value=args[4]),
                    patch.object(runtime, "inputs", return_value=values),
                    redirect_stderr(stderr),
                ):
                    self.assertEqual(runtime.cli("adoption"), 2)
                self.assertEqual(stderr.getvalue(), REFUSAL + "\n")
                args[-1] = stderr.getvalue()
                before = deepcopy(args[8:10])
                report = MODULE.request_refusal(*args)
                self.assertEqual(report["decision"], "maintenance-" + case + "-refused")
                self.assertNotEqual(report["requestDigest"], report["acceptedDigest"])
                self.assertEqual(args[8:10], before)
                self.assertNotIn("status", report)
                self.assertNotIn("result", report)

    def test_unexpected_request_changes_and_incorrect_identity_cannot_qualify(self):
        for case in ("changed-request", "upgrade-request"):
            for change in (
                "same-bytes",
                "intent",
                "environment",
                "extra-field",
                "digest",
                "commit",
                "path",
                "baseline",
                "candidate",
                "observed-candidate",
                "readiness",
                "log",
                "write",
                "result",
            ):
                with self.subTest(case=case, change=change):
                    args = fault(case)
                    workflow, parameters, before = args[1], args[3], args[8]
                    nodes = workflow["status"]["nodes"]
                    if change == "same-bytes":
                        replace_request(args, args[5])
                    if change in {"intent", "environment", "extra-field"}:
                        candidate = json.loads(args[4])
                        if change == "intent":
                            candidate["promotion"]["expectedCurrent"] = "sha256:" + "e" * 64
                        if change == "environment":
                            candidate["environment"]["clusterUID"] = "other-cluster"
                        if change == "extra-field":
                            candidate["unreviewed"] = True
                        replace_request(args, json.dumps(candidate).encode())
                    if change == "digest":
                        parameters["request-sha256"] = "sha256:" + "e" * 64
                    if change == "commit":
                        parameters["request-revision"] = "e" * 40
                    if change == "path":
                        parameters["request-path"] = "deployments/release-requests/other.json"
                    if change == "baseline":
                        before["records"][0]["data"]["receiptUID"] = "other-receipt"
                    if change == "candidate":
                        before["requestedReceipt"] = {"metadata": {"uid": "unexpected"}}
                    if change == "observed-candidate":
                        nodes["receipt"]["outputs"]["parameters"][0]["value"] = json.dumps(
                            args[9][1]
                        )
                    if change == "readiness":
                        nodes["readiness"]["phase"] = "Failed"
                    if change == "log":
                        args[-1] = (
                            "Adoption refused: invalid selection, observations or operational response"
                        )
                    if change == "write":
                        nodes["pause-root"] = {
                            "id": "pause-root",
                            "displayName": "pause-root",
                            "type": "Pod",
                            "phase": "Succeeded",
                            "templateRef": {
                                "name": "perfeng-maintenance-resources-v1",
                                "template": "patch-root",
                            },
                        }
                        args[2].append(
                            {
                                "nodeID": "pause-root",
                                "uid": "unexpected-worker",
                                "account": "release-maintenance-pause",
                                "exitCode": 0,
                            }
                        )
                    if change == "result":
                        nodes["pause"]["outputs"] = {
                            "parameters": [{"name": "result", "value": "{}"}]
                        }
                    with self.assertRaises((AssertionError, KeyError, ValueError)):
                        MODULE.request_refusal(*args)

    def test_cli_preserves_original_evidence_and_refuses_a_created_candidate_receipt(self):
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
            for case in ("changed-request", "upgrade-request"):
                (
                    _,
                    workflow,
                    workers,
                    parameters,
                    raw,
                    original,
                    original_parameters,
                    _,
                    before,
                    baseline,
                    log,
                ) = fault(case)
                for label in ("before", "after"):
                    for key, suffix in mapping.items():
                        (fixture / f"maintenance-{case}-{label}.{suffix}.json").write_text(
                            json.dumps(before[key]), encoding="utf-8"
                        )
                    (fixture / f"maintenance-{case}.candidate-{label}.json").write_text(
                        "null", encoding="utf-8"
                    )
                for path, value in (
                    (fixture / "parent-parameters.json", original_parameters),
                    (fixture / f"{case}-parameters.json", parameters),
                    (fixture / "adoption-identity-baseline.json", baseline),
                    (results / f"maintenance-{case}.workflow.json", workflow),
                    (results / f"maintenance-{case}.workers.json", workers),
                ):
                    path.write_text(json.dumps(value), encoding="utf-8")
                (fixture / "parent-request.json").write_bytes(original)
                (fixture / f"{case}-request.json").write_bytes(raw)
                (fixture / f"maintenance-{case}.worker.log").write_text(log, encoding="utf-8")
                originals = {p: p.read_bytes() for p in fixture.glob("*.json")}
                with patch.object(sys, "argv", ["evidence", case, str(fixture), str(results)]):
                    MODULE.main()
                self.assertEqual({p: p.read_bytes() for p in originals}, originals)
                output = results / f"maintenance-{case}.outcome.json"
                self.assertEqual(
                    json.loads(output.read_text())["decision"], "maintenance-" + case + "-refused"
                )
                self.assertNotIn("private", output.read_text())
                self.assertNotIn("credential", output.read_text())
                output.unlink()
                (fixture / f"maintenance-{case}.candidate-after.json").write_text(
                    '{"metadata":{"uid":"new"}}', encoding="utf-8"
                )
                with (
                    patch.object(sys, "argv", ["evidence", case, str(fixture), str(results)]),
                    self.assertRaises(AssertionError),
                ):
                    MODULE.main()
                self.assertFalse(output.exists())

    @unittest.skipUnless(BASH, "Bash is required for fixture orchestration")
    def test_shell_uses_reviewed_parameters_proves_absence_and_stays_before_receipt_faults(self):
        assert BASH
        source = (FIXTURE / "maintenance-pause.sh").read_text()
        function = re.search(r"^maintenance_baseline_refusal\(\) \{.*?^\}", source, re.M | re.S)
        assert function
        shell = r"""
set -euo pipefail
fixture=$1 results=$1/results source_dir=unused KUBECONFIG=$1/kubeconfig
mode=preflight-parent scenario=initial current_stage=preflight-parent adoption_namespace=perfeng-release-ledger
adoption_retention_baseline=$1/maintenance-recovered
mkdir -p "$results"
touch "$fixture/owned" "$results/maintenance-recovery.outcome.txt"
start_stage() { :; }
complete_stage() { :; }
maintenance_snapshot() { :; }
root_state() { :; }
root_retained() { :; }
adoption_retained() { :; }
cmp() { return 0; }
a() { printf '%s\n' "$*" >> "$fixture/submission.txt"; echo '{}'; }
k() { printf '%s\n' "$*" >> "$fixture/reads.txt"; echo '{}'; }
jq() {
  if [[ $* == *request-sha256* ]]; then printf 'sha256:%064d\n' 0
  elif [[ $* == *'.==null'* ]]; then return 0
  else cat > /dev/null; echo fixture-id; fi
}
timeout() { return 1; }
uv() { echo verified-refusal; }
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "request-refusal.sh"
            script.write_text(
                shell + function.group() + '\nmaintenance_baseline_refusal "$2"\n',
                encoding="utf-8",
                newline="\n",
            )
            for case in ("changed-request", "upgrade-request"):
                with self.subTest(case=case):
                    fixture = directory / case
                    fixture.mkdir()
                    result = subprocess.run(
                        [BASH, script.as_posix(), fixture.as_posix(), case],
                        cwd=ROOT,
                        env={**os.environ, "BASH_ENV": ""},
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("verified-refusal", result.stdout)
                    self.assertIn(
                        f"--parameter-file {fixture.as_posix()}/{case}-parameters.json",
                        (fixture / "submission.txt").read_text(),
                    )
                    reads = (fixture / "reads.txt").read_text()
                    self.assertEqual(reads.count("--ignore-not-found"), 2)
                    self.assertNotRegex(reads, r"\b(create|patch|delete|apply)\b")
        adoption = (FIXTURE / "adoption-parent.sh").read_text()
        for case in ("changed-request", "upgrade-request"):
            self.assertLess(
                adoption.index('source "$source_dir/maintenance-pause.sh"'),
                adoption.index("maintenance_baseline_refusal " + case),
            )
            self.assertLess(
                adoption.index("maintenance_baseline_refusal " + case),
                adoption.index(
                    'k delete --raw "/api/v1/namespaces/perfeng-release-ledger/configmaps/$adoption_receipt"'
                ),
            )
