"""Check exact CI parent copies, stop provenance, retained state and failure ordering."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import yaml
from test_maintenance_pause_integration import FIXTURE, ROOT, evidence
from test_maintenance_pause_integration import MODULE as MAINTENANCE
from test_release_workflows_integration import BASH

SPEC = importlib.util.spec_from_file_location(
    "maintenance_partial_pause", FIXTURE / "maintenance_partial_pause.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"maintenance_pause_evidence": MAINTENANCE}):
    SPEC.loader.exec_module(MODULE)


def case(resource, stopped=True):
    workflow, workers, parameters, _, raw, lock, before, _ = evidence()
    workflow["metadata"]["namespace"] = "argo-workflows"
    workflow["spec"]["workflowTemplateRef"] = {
        "name": MODULE.template(resource)["metadata"]["name"]
    }
    nodes = workflow["status"]["nodes"]
    nodes["baseline"]["templateRef"] = {
        "name": "perfeng-plan-release-adoption-v3",
        "template": "run",
    }
    order = [
        "guard-root",
        "pause-root",
        "guard-child",
        "pause-child",
        "guard-deployment",
        "pause-deployment",
        "observe",
    ]
    for name in order[order.index("pause-" + resource) + 1 :]:
        del nodes[name]
    nodes["pause"].update(phase="Failed" if stopped else "Running", outputs={})
    when = nodes["pause-" + resource]["finishedAt"]
    nodes[MODULE.BARRIER] = {
        "id": MODULE.BARRIER,
        "displayName": MODULE.BARRIER,
        "type": "Suspend",
        "phase": "Failed" if stopped else "Running",
        "startedAt": when,
    }
    workflow["status"]["phase"] = "Failed" if stopped else "Running"
    if stopped:
        workflow["spec"]["shutdown"] = "Stop"
        workflow["status"]["message"] = MODULE.STOPPED
        nodes[MODULE.BARRIER].update(message=MODULE.STOPPED, finishedAt=when)
    workers = sorted([w for w in workers if w["nodeID"] in nodes], key=lambda w: w["nodeID"])
    return workflow, workers, parameters, raw, lock, before


class MaintenancePartialPauseTests(unittest.TestCase):
    def test_templates_reverse_exactly_and_keep_native_authority_and_bounds(self):
        source = yaml.safe_load(
            (MODULE.DIRECTORY / "release-maintenance-pause-v1.yaml").read_text()
        )
        for resource in MODULE.RESOURCES:
            value = MODULE.template(resource)
            steps = value["spec"]["templates"][0]["steps"]
            index = next(i for i, group in enumerate(steps) if group[0]["name"] == MODULE.BARRIER)
            self.assertEqual(steps[index - 1][0]["name"], "pause-" + resource)
            self.assertEqual(
                steps[index + 1][0]["name"],
                "guard-child" if resource == "root" else "guard-deployment",
            )
            del steps[index]
            self.assertEqual(
                value["spec"]["templates"].pop(), {"name": MODULE.BARRIER, "suspend": {}}
            )
            value["metadata"] = source["metadata"]
            self.assertEqual(value, source)
        with self.assertRaises(AssertionError):
            MODULE.template("deployment")

    def test_real_baseline_policy_and_completed_prefix_are_required_before_stop(self):
        for resource in MODULE.RESOURCES:
            args = case(resource, stopped=False)
            MODULE.validate(args[0], args[1], resource, *args[2:], stopped=False)
            for field in ("baseline", "guard-" + resource, "pause-" + resource):
                bad = deepcopy(args[0])
                bad["status"]["nodes"][field]["phase"] = "Error"
                with (
                    self.subTest(resource=resource, field=field),
                    self.assertRaises(AssertionError),
                ):
                    MODULE.validate(bad, args[1], resource, *args[2:], stopped=False)
            bad_workers = deepcopy(args[1])
            bad_workers[0]["exitCode"] = 2
            with self.assertRaises(AssertionError):
                MODULE.validate(args[0], bad_workers, resource, *args[2:], stopped=False)

    def test_stop_requires_exact_strategy_barrier_cause_workers_and_no_later_execution(self):
        for resource in MODULE.RESOURCES:
            args = case(resource)
            MODULE.validate(args[0], args[1], resource, *args[2:], stopped=True)
            mutations = [
                lambda w: w["spec"].update(shutdown="Terminate"),
                lambda w: w["status"].update(phase="Error"),
                lambda w: w["status"].update(message="timeout"),
                lambda w: w["status"]["nodes"][MODULE.BARRIER].update(message="worker failure"),
                lambda w: w["status"]["nodes"][MODULE.BARRIER].update(phase="Succeeded"),
                lambda w, target=resource: w["status"]["nodes"]["pause-" + target].update(
                    finishedAt="2099-01-01T00:00:00+00:00"
                ),
                lambda w: w["status"]["nodes"].update(
                    checkpoint={
                        "id": "checkpoint",
                        "displayName": "checkpoint",
                        "type": "Steps",
                        "phase": "Running",
                    }
                ),
                lambda w: w["status"]["nodes"]["pause"].update(
                    outputs={"parameters": [{"name": "result", "value": "{}"}]}
                ),
            ]
            for index, mutate in enumerate(mutations):
                bad = deepcopy(args[0])
                mutate(bad)
                with (
                    self.subTest(resource=resource, mutation=index),
                    self.assertRaises(AssertionError),
                ):
                    MODULE.validate(bad, args[1], resource, *args[2:], stopped=True)
            for field, value in (("exitCode", 1), ("account", "default"), ("uid", "")):
                workers = deepcopy(args[1])
                workers[0][field] = value
                with self.assertRaises(AssertionError):
                    MODULE.validate(args[0], workers, resource, *args[2:], stopped=True)

    def test_cli_verifies_installed_copy_and_retention_without_rewriting_private_evidence(self):
        for resource in MODULE.RESOURCES:
            workflow, workers, parameters, raw, _, before = case(resource)
            suspended = case(resource, stopped=False)[0]
            prefix = "maintenance-partial-" + resource
            with tempfile.TemporaryDirectory() as temporary:
                fixture = Path(temporary)
                results = fixture / "results"
                results.mkdir()
                for suffix, value in (
                    ("workflow", workflow),
                    ("barrier", suspended),
                    ("workers", workers),
                    ("workers-before", workers),
                    ("installed", MODULE.template(resource)),
                ):
                    (fixture / f"{prefix}.{suffix}.json").write_text(json.dumps(value))
                (fixture / "parent-parameters.json").write_text(json.dumps(parameters))
                (fixture / "parent-request.json").write_bytes(raw)
                for label in ("before", "after"):
                    for key in (
                        "applications",
                        "deployment",
                        "namespace",
                        "server",
                        "workloads",
                        "pods",
                        "records",
                        "secrets",
                        "config",
                        "git",
                    ):
                        (fixture / f"{prefix}-{label}.{key}.json").write_text(
                            json.dumps(before[key])
                        )

                def run(mode, resource=resource, results=results):
                    with patch.object(
                        sys, "argv", ["partial", mode, resource, temporary, str(results)]
                    ):
                        MODULE.main()

                (fixture / f"{prefix}.barrier-pods.json").write_text('{"items": []}')
                (fixture / f"{prefix}.state.txt").write_text("ready")
                command = [
                    sys.executable,
                    str(FIXTURE / "maintenance_partial_pause.py"),
                    "barrier",
                    resource,
                    temporary,
                    str(results),
                ]
                barrier = subprocess.run(
                    command,
                    cwd=ROOT,
                    env={**os.environ, "PYTHONPATH": str(ROOT)},
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(barrier.returncode, 0, barrier.stderr)
                report = results / f"{prefix}.barrier.json"
                self.assertEqual(json.loads(report.read_text())["decision"], "barrier-ready")
                (fixture / f"{prefix}.state.txt").write_text("timeout")
                barrier = subprocess.run(
                    command,
                    cwd=ROOT,
                    env={**os.environ, "PYTHONPATH": str(ROOT)},
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertNotEqual(barrier.returncode, 0)
                self.assertEqual(json.loads(report.read_text())["decision"], "barrier-timeout")
                run("installed")
                saved = {p: p.read_bytes() for p in fixture.iterdir() if p.is_file()}
                run("verify")
                outcome = results / f"{prefix}.outcome.json"
                self.assertEqual(
                    json.loads(outcome.read_text())["decision"], "maintenance-partial-pause-stopped"
                )
                self.assertNotIn("private", outcome.read_text())
                self.assertEqual(saved, {p: p.read_bytes() for p in saved})
                outcome.unlink()
                applications = fixture / f"{prefix}-after.applications.json"
                original = applications.read_bytes()
                refreshed = json.loads(original)
                root = next(
                    o for o in refreshed["items"] if o["metadata"]["name"] == "perfeng-local-root"
                )
                root["metadata"]["generation"] = root["metadata"].get("generation", 0) + 1
                applications.write_text(json.dumps(refreshed))
                run("verify")
                outcome.unlink()
                root["metadata"]["labels"] = {"wrong": "owner"}
                applications.write_text(json.dumps(refreshed))
                with self.assertRaises(AssertionError):
                    run("verify")
                self.assertFalse(outcome.exists())
                applications.write_bytes(original)
                for suffix, value in (("after.secrets", {}),):
                    path = fixture / f"{prefix}-{suffix}.json"
                    old = path.read_bytes()
                    path.write_text(json.dumps(value))
                    with self.assertRaises(AssertionError):
                        run("verify")
                    self.assertFalse(outcome.exists())
                    path.write_bytes(old)
                changed = deepcopy(workflow)
                changed["metadata"]["uid"] = "replacement"
                (fixture / f"{prefix}.workflow.json").write_text(json.dumps(changed))
                with self.assertRaises(AssertionError):
                    run("verify")
                self.assertFalse(outcome.exists())
                installed = MODULE.template(resource)
                installed["spec"]["serviceAccountName"] = "default"
                (fixture / f"{prefix}.installed.json").write_text(json.dumps(installed))
                with self.assertRaises(AssertionError):
                    run("installed")

    @unittest.skipUnless(BASH, "Bash required for real fixture failure ordering")
    def test_shell_stops_only_after_verified_barrier_and_never_recovers_or_advances_baseline(self):
        assert BASH
        shell = r"""
set -euo pipefail
fixture=$1 results=$1/results source_dir=unused KUBECONFIG=$1/kubeconfig
mode=preflight-parent scenario=initial current_stage=preflight-parent FAIL_MODE=$3
GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
adoption_retention_baseline=$1/maintenance-recovered
root_uid=root-uid adoption_root_case=root-corrected root_revision=selected
mkdir -p "$results"; touch "$fixture/owned" "$results/maintenance-recovery.outcome.txt"
start_stage() { :; }; complete_stage() { echo complete >> "$fixture/trace"; }
maintenance_snapshot() { :; }; root_state() { :; }; root_retained() { [[ $FAIL_MODE != retention ]]; }; adoption_retained() { :; }
cmp() { return 0; }; cp() { printf '%s\n' "$(< "$1")" > "$2"; }
argo() { :; }
timeout() { echo "timeout $*" >> "$fixture/trace"; if [[ $* == *' stop '* ]]; then [[ $FAIL_MODE != stop ]]; elif [[ $FAIL_MODE == wait-timeout ]]; then return 124; else return 1; fi; }
k() { echo "k $*" >> "$fixture/trace"; echo '{}'; }
a() { echo "a $*" >> "$fixture/trace"; echo '{}'; }
jq() { if [[ $* != *'.json'* ]]; then while IFS= read -r line; do :; done; fi; echo fixture-id; }
uv() { echo "uv $*" >> "$fixture/trace"; if [[ $* == *" $FAIL_MODE "* && $FAIL_MODE != success ]]; then return 1; fi; echo '{}'; }
source "$2"
[[ $adoption_retention_baseline == "$fixture/maintenance-recovered" ]]
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "rehearsal.sh"
            script.write_text(shell, newline="\n")
            for mode in (
                "success",
                "installed",
                "barrier",
                "stop",
                "wait-timeout",
                "retention",
                "verify",
            ):
                fixture = directory / mode
                fixture.mkdir()
                process = subprocess.run(
                    [
                        BASH,
                        script.as_posix(),
                        fixture.as_posix(),
                        (FIXTURE / "maintenance-partial-pause.sh").as_posix(),
                        mode,
                    ],
                    cwd=ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                trace = (fixture / "trace").read_text()
                with self.subTest(mode=mode):
                    self.assertEqual(process.returncode == 0, mode == "success", process.stderr)
                    if mode in {"installed", "barrier"}:
                        self.assertNotIn(" stop ", trace)
                    if mode != "success":
                        self.assertNotIn("template child", trace)
                        self.assertNotIn("complete", trace)
                    else:
                        self.assertEqual(trace.count(" stop "), 2)
                        self.assertEqual(trace.count("complete"), 2)
                        for resource in MODULE.RESOURCES:
                            self.assertIn("verify " + resource, trace)
                        self.assertEqual(trace.count("root_inputs.py converged"), 4)
                    for forbidden in ("resume", "delete", "--type=json", "maintenance-recovery"):
                        self.assertNotIn(forbidden, trace)
        adoption = (FIXTURE / "adoption-parent.sh").read_text()
        self.assertLess(
            adoption.index('source "$source_dir/maintenance-guard-conflict.sh"'),
            adoption.index('source "$source_dir/maintenance-partial-pause.sh"'),
        )
        self.assertLess(
            adoption.index('source "$source_dir/maintenance-partial-pause.sh"'),
            adoption.index("k delete --raw"),
        )


if __name__ == "__main__":
    unittest.main()
