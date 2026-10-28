"""An unadopted maintenance refusal must precede first adoption without writes."""

import json
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import test_maintenance_pause_integration as pause
import test_maintenance_write_denial as denial
from test_release_workflows_integration import BASH

with patch.dict(
    "sys.modules",
    {"maintenance_pause_evidence": pause.MODULE, "maintenance_write_denial": denial.MODULE},
):
    spec = pause.importlib.util.spec_from_file_location(
        "maintenance_unadopted", pause.FIXTURE / "maintenance_unadopted.py"
    )
    assert spec and spec.loader
    MODULE = pause.importlib.util.module_from_spec(spec)
    spec.loader.exec_module(MODULE)


def evidence():
    workflow, workers, parameters, preflight, raw, lock, before, _ = pause.evidence()
    before["server-pods"] = before.pop("serverPod")
    before["records"] = []
    nodes = workflow["status"]["nodes"]
    for name in ("receipt", "pointer"):
        nodes[name]["outputs"] = {"parameters": [{"name": "observation", "value": "null"}]}
    for name in (
        "guard-root",
        "pause-root",
        "guard-child",
        "pause-child",
        "guard-deployment",
        "pause-deployment",
        "observe",
    ):
        nodes.pop(name)
    nodes["baseline"].update(
        phase="Failed",
        outputs={},
        templateRef={"name": "perfeng-plan-release-adoption-v3", "template": "run"},
    )
    health = pause.READINESS.outputs(nodes["readiness"])["result"]
    values = {
        "phase": "finish",
        "readiness": health,
        "root": pause.READINESS.outputs(nodes["root"])["observation"],
        "receipt": None,
        "pointer": None,
        "root-after": pause.READINESS.outputs(nodes["root-after"])["observation"],
    }
    nodes["baseline"]["inputs"] = {
        "parameters": [
            {"name": k, "value": v if k == "phase" else json.dumps(v)} for k, v in values.items()
        ]
    }
    nodes["pause"].update(phase="Failed", outputs={})
    workflow["status"]["phase"] = "Failed"
    workers = [w for w in workers if w["nodeID"] in nodes]
    for worker in workers:
        worker["exitCode"] = 2 if worker["nodeID"] == "baseline" else 0
    log = "Adoption refused: inspect reviewed inputs, ownership and observations; no success evidence\n"
    return workflow, workers, parameters, preflight, raw, lock, before, deepcopy(before), log


class UnadoptedMaintenanceTests(unittest.TestCase):
    def test_actual_policy_and_finish_runtime_refuse_without_outputs(self):
        self.assertEqual(MODULE.verify(*evidence())["decision"], "maintenance-unadopted-refused")

    def test_unrelated_failure_record_write_or_success_cannot_qualify(self):
        for mutate in (
            lambda a: a[0]["status"].update(phase="Succeeded"),
            lambda a: a[0]["status"]["nodes"]["baseline"]["inputs"]["parameters"][0].update(
                value="plan"
            ),
            lambda a: a[0]["status"]["nodes"]["readiness"].update(phase="Failed"),
            lambda a: a[1][0].update(account="default"),
            lambda a: a[6].update(records=[{}]),
            lambda a: a[7]["server-pods"]["items"][0]["metadata"].update(uid="replacement"),
            lambda a: a[7].update(secrets={"changed": True}),
            lambda a: a[0]["status"]["nodes"].update(
                later={
                    "id": "later",
                    "displayName": "pause-child",
                    "type": "Pod",
                    "phase": "Succeeded",
                }
            ),
        ):
            args = list(evidence())
            mutate(args)
            with self.assertRaises(AssertionError):
                MODULE.verify(*args)
        args = list(evidence())
        args[-1] = "transport timeout"
        with self.assertRaises(AssertionError):
            MODULE.verify(*args)

    def test_cli_keeps_raw_snapshots_private_and_withholds_unproven_outcome(self):
        workflow, workers, parameters, preflight, raw, _, before, after, log = evidence()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            results = directory / "results"
            results.mkdir()
            for label, state in (("before", before), ("after", after)):
                for key, value in state.items():
                    (directory / f"{MODULE.PREFIX}-{label}.{key}.json").write_text(
                        json.dumps(value)
                    )
            for name, value in (
                (f"{MODULE.PREFIX}.workflow.json", workflow),
                (f"{MODULE.PREFIX}.workers.json", workers),
                ("parent-parameters.json", parameters),
            ):
                (directory / name).write_text(json.dumps(value))
            (directory / "parent-request.json").write_bytes(raw)
            (directory / f"{MODULE.PREFIX}.worker.log").write_text(log)
            (results / "parent-first.result.json").write_text(json.dumps(preflight))
            with patch("sys.argv", ["unadopted", str(directory), str(results)]):
                MODULE.main()
            report = results / f"{MODULE.PREFIX}.outcome.json"
            self.assertEqual(
                set(json.loads(report.read_text())), {"decision", "workflowUID", "clusterUID"}
            )
            report.unlink()
            (directory / f"{MODULE.PREFIX}.worker.log").write_text("transport timeout")
            with (
                patch("sys.argv", ["unadopted", str(directory), str(results)]),
                self.assertRaises(AssertionError),
            ):
                MODULE.main()
            self.assertFalse(report.exists())

    @unittest.skipUnless(BASH, "Bash required")
    def test_first_adoption_requires_exact_refusal_and_both_retention_checks(self):
        assert BASH
        text = (pause.FIXTURE / "maintenance-unadopted.sh").read_text()
        library = text[text.index("maintenance_unadopted_case() {") :].rsplit(
            "maintenance_unadopted_case", 1
        )[0]
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; results=$1; source_dir=$1; fail=$3; KUBECONFIG="$fixture/kubeconfig"
current_stage=outer
start_stage() { echo stage >> "$fixture/trace"; }; complete_stage() { echo complete >> "$fixture/trace"; }
maintenance_install() { echo install >> "$fixture/trace"; [[ $fail != install ]]; }
maintenance_unadopted_snapshot() { echo "snapshot $1" >> "$fixture/trace"; [[ $fail != snapshot ]]; }
root_retained() {
 case "$2" in
  *-before) echo retain-before >> "$fixture/trace"; [[ $fail != before ]] ;;
  *-after) echo retain-after >> "$fixture/trace"; [[ $fail != after ]] ;;
 esac
}
a() { echo '{}'; }; k() { echo '{}'; }; jq() { cat >/dev/null; echo workflow; }
timeout() { echo wait >> "$fixture/trace"; case "$fail" in success) return 0 ;; timeout) return 124 ;; *) return 1 ;; esac; }
uv() { echo verify >> "$fixture/trace"; [[ $fail != verify ]]; }
source "$2"
maintenance_unadopted_case
echo adoption >> "$fixture/trace"
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "case.sh").write_text(library, newline="\n")
            (directory / "run.sh").write_text(setup, newline="\n")
            for fail in (
                "pass",
                "install",
                "snapshot",
                "before",
                "after",
                "success",
                "timeout",
                "verify",
            ):
                fixture = directory / fail
                fixture.mkdir()
                process = subprocess.run(
                    [
                        BASH,
                        (directory / "run.sh").as_posix(),
                        fixture.as_posix(),
                        (directory / "case.sh").as_posix(),
                        fail,
                    ],
                    cwd=pause.ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                with self.subTest(fail=fail):
                    trace = (fixture / "trace").read_text().splitlines()
                    self.assertEqual(process.returncode, 0 if fail == "pass" else 1, process.stderr)
                    self.assertNotIn("unbound variable", process.stderr)
                    if fail == "pass":
                        self.assertEqual(trace[-3:], ["verify", "complete", "adoption"])
                    else:
                        self.assertNotIn("adoption", trace)
                        self.assertNotIn("complete", trace)
                        boundary = {
                            "install": "install",
                            "before": "retain-before",
                            "after": "retain-after",
                            "success": "wait",
                            "timeout": "wait",
                            "verify": "verify",
                        }
                        if fail in boundary:
                            self.assertEqual(trace[-1], boundary[fail])
                        else:
                            self.assertTrue(trace[-1].startswith("snapshot "))


if __name__ == "__main__":
    unittest.main()
