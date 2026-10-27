"""Check exact CI parent copies, stop provenance, retained state and failure ordering."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import test_release_maintenance_checkpoint as checkpoint_tests
import yaml
from test_maintenance_pause_integration import FIXTURE, ROOT, evidence, recovered
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
            MODULE.template("service")

    def test_deployment_copy_and_stop_require_all_three_completed_native_writes(self):
        value = MODULE.template("deployment")
        steps = value["spec"]["templates"][0]["steps"]
        index = next(i for i, group in enumerate(steps) if group[0]["name"] == MODULE.BARRIER)
        self.assertEqual(steps[index - 1][0]["name"], "pause-deployment")
        self.assertEqual(steps[index + 1][0]["name"], "checkpoint")
        del steps[index]
        value["spec"]["templates"].pop()
        original = yaml.safe_load(
            (MODULE.DIRECTORY / "release-maintenance-pause-v1.yaml").read_text()
        )
        value["metadata"] = original["metadata"]
        self.assertEqual(value, original)
        args = case("deployment")
        MODULE.validate(args[0], args[1], "deployment", *args[2:], stopped=True)
        for name in ("pause-root", "pause-child", "pause-deployment"):
            bad = deepcopy(args[0])
            bad["status"]["nodes"][name]["phase"] = "Failed"
            with self.assertRaises(AssertionError):
                MODULE.validate(bad, args[1], "deployment", *args[2:], stopped=True)
        suspended = case("deployment", stopped=False)[0]
        MODULE.stopped_identity(args[0], suspended, args[1], args[1])
        bad = deepcopy(args[0])
        bad["metadata"]["uid"] = "replacement"
        with self.assertRaises(AssertionError):
            MODULE.stopped_identity(bad, suspended, args[1], args[1])

    def test_independent_checkpoint_binds_fresh_parent_identity_workers_and_real_policy(self):
        fixture = checkpoint_tests.MaintenanceCheckpointTests()
        fixture.setUp()
        result = fixture.verify()
        preflight = {
            "id": "preflight",
            "displayName": "preflight",
            "type": "Steps",
            "phase": "Succeeded",
            "startedAt": "2026-10-27T00:00:02+00:00",
            "finishedAt": "2026-10-27T00:00:03+00:00",
            "outputs": {"parameters": [{"name": "result", "value": json.dumps(fixture.preflight)}]},
        }
        observe = {
            "id": "observe",
            "displayName": "observe",
            "type": "Pod",
            "phase": "Succeeded",
            "startedAt": "2026-10-27T00:00:04+00:00",
            "finishedAt": "2026-10-27T00:00:05+00:00",
            "templateRef": {"name": "perfeng-verify-release-quiescence-v3", "template": "run"},
            "outputs": {"parameters": [{"name": "result", "value": json.dumps(result)}]},
        }
        parameters = {"selection-revision": checkpoint_tests.REVISION}
        workflow = {
            "metadata": {
                "name": "checkpoint",
                "namespace": "argo-workflows",
                "uid": "checkpoint-uid",
            },
            "spec": {
                "workflowTemplateRef": {"name": "perfeng-release-maintenance-checkpoint-v3"},
                "arguments": {
                    "parameters": [{"name": k, "value": v} for k, v in parameters.items()]
                },
            },
            "status": {
                "phase": "Succeeded",
                "nodes": {
                    "preflight": preflight,
                    "observe": observe,
                    "checkpoint": {
                        "id": "checkpoint",
                        "displayName": "checkpoint",
                        "type": "Steps",
                        "phase": "Succeeded",
                        "outputs": deepcopy(observe["outputs"]),
                    },
                },
            },
        }
        interrupted = {
            "metadata": {"uid": "interrupted-uid"},
            "status": {
                "finishedAt": "2026-10-27T00:00:01+00:00",
                "nodes": {"preflight": deepcopy(preflight)},
            },
        }
        workers = [
            {
                "nodeID": "observe",
                "uid": "worker",
                "account": "release-maintenance-checkpoint",
                "exitCode": 0,
            }
        ]
        state: dict[str, Any] = {
            "applications": {"items": fixture.before[:2]},
            "deployment": fixture.before[2],
            "podList": fixture.pods,
            "namespace": fixture.namespace,
        }
        self.assertEqual(
            MODULE.checkpoint(workflow, workers, interrupted, parameters, fixture.raw, state),
            result,
        )
        # The verifier's and fixture's separately sampled versions may differ.
        state["podList"] = {**state["podList"], "metadata": {"resourceVersion": "fresh-version"}}
        self.assertEqual(
            MODULE.checkpoint(workflow, workers, interrupted, parameters, fixture.raw, state),
            result,
        )
        for mutate in (
            lambda w: w["metadata"].update(uid="interrupted-uid"),
            lambda w: w["spec"].update(shutdown="Stop"),
            lambda w: w["spec"]["workflowTemplateRef"].update(name="wrong-parent"),
            lambda w: w["spec"]["arguments"]["parameters"].append(
                {"name": "request-sha256", "value": "wrong"}
            ),
            lambda w: w["status"].update(phase="Failed"),
            lambda w: w["status"]["nodes"]["preflight"].update(
                startedAt="2026-10-26T00:00:00+00:00"
            ),
            lambda w: w["status"]["nodes"]["observe"]["templateRef"].update(template="wrong"),
        ):
            bad = deepcopy(workflow)
            mutate(bad)
            with self.assertRaises(AssertionError):
                MODULE.checkpoint(bad, workers, interrupted, parameters, fixture.raw, state)
        for field, value in (
            ("account", "release-maintenance-pause"),
            ("exitCode", 1),
            ("uid", ""),
        ):
            bad_workers = deepcopy(workers)
            bad_workers[0][field] = value
            with self.assertRaises(AssertionError):
                MODULE.checkpoint(
                    workflow, bad_workers, interrupted, parameters, fixture.raw, state
                )
        for mutate in (
            lambda s: s["podList"].update(items=[{}]),
            lambda s: s["podList"]["metadata"].update({"continue": "next"}),
            lambda s: s["deployment"]["spec"].update(replicas=1),
        ):
            bad_state = deepcopy(state)
            mutate(bad_state)
            with self.assertRaises((ValueError, AssertionError)):
                MODULE.checkpoint(
                    workflow, workers, interrupted, parameters, fixture.raw, bad_state
                )

    def test_deployment_paused_and_recovered_retention_rejects_other_changes(self):
        _, _, _, _, _, _, before, paused = evidence()
        MAINTENANCE.retained(before, paused, paused=True)
        restored = recovered(before)
        MAINTENANCE.retained(before, restored, paused=False)
        for label in ("records", "secrets", "config", "git", "workloads", "pods"):
            changed = deepcopy(restored)
            changed[label] = {}
            with self.assertRaises(AssertionError):
                MAINTENANCE.retained(before, changed, paused=False)
        changed = deepcopy(restored)
        changed["serverPod"]["items"][0]["metadata"]["ownerReferences"][0]["uid"] = "new-replicaset"
        with self.assertRaises(AssertionError):
            MAINTENANCE.retained(before, changed, paused=False)

    @unittest.skipUnless(BASH, "Bash required for actual recovery failure ordering")
    def test_deployment_script_never_syncs_before_checkpoint_or_advances_failed_recovery(self):
        assert BASH
        shell = r"""
set -euo pipefail
fixture=$1 results=$1/results source_dir=unused KUBECONFIG=$1/kubeconfig
mode=preflight-parent scenario=initial current_stage=preflight-parent FAIL_MODE=$3
source_dir=$1/source; mkdir -p "$source_dir"
printf 'echo lingering >> "$fixture/trace"; [[ $FAIL_MODE != lingering ]]\n' > "$source_dir/maintenance-lingering-pod.sh"
GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
adoption_retention_baseline=$1/maintenance-recovered
root_server_pods_file=original adoption_baseline=first
mkdir -p "$results"; touch "$fixture/owned" "$results/maintenance-recovery.outcome.txt"
trap 'echo "baseline=$adoption_retention_baseline" >> "$fixture/trace"' EXIT
start_stage() { echo "stage $*" >> "$fixture/trace"; }; complete_stage() { :; }
k() { echo '{}' ; }; a() { echo '{}'; }; maintenance_partial_workers() { echo '[]'; }
maintenance_partial_case() { echo pause >> "$fixture/trace"; [[ $FAIL_MODE != pause ]]; }
maintenance_snapshot() { :; }; maintenance_partial_root() { :; }; root_state() { :; }
root_persistent_retained() { [[ $FAIL_MODE != retention ]]; }; cmp() { return 0; }
argocd_case() { echo sync >> "$fixture/trace"; [[ $FAIL_MODE != sync ]]; }
server_case() { echo http >> "$fixture/trace"; [[ $FAIL_MODE != http ]]; }
adoption_case() { echo adoption >> "$fixture/trace"; }
jq() { while IFS= read -r line; do :; done; echo checkpoint; }
timeout() { [[ $FAIL_MODE != wait ]]; }
uv() { echo "uv $*" >> "$fixture/trace"; if [[ $* == *" $FAIL_MODE "* ]]; then return 1; fi; }
source "$2"
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "rehearsal.sh"
            script.write_text(shell, newline="\n")
            for mode in (
                "success",
                "pause",
                "lingering",
                "wait",
                "fresh",
                "checkpoint",
                "sync",
                "http",
                "retention",
                "recovered",
            ):
                fixture = directory / mode
                fixture.mkdir()
                process = subprocess.run(
                    [
                        BASH,
                        script.as_posix(),
                        fixture.as_posix(),
                        (FIXTURE / "maintenance-deployment-pause.sh").as_posix(),
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
                    if mode in {"pause", "lingering", "wait", "fresh", "checkpoint"}:
                        self.assertNotIn("sync\n", trace)
                    if mode != "success":
                        self.assertIn(
                            "baseline=" + fixture.as_posix() + "/maintenance-recovered", trace
                        )
                        self.assertNotIn("adoption\n", trace)
                    else:
                        self.assertIn("/maintenance-partial-deployment-recovered", trace)
                        self.assertLess(trace.index("checkpoint deployment"), trace.index("sync\n"))
                        self.assertLess(
                            trace.index("recovered deployment"), trace.index("adoption\n")
                        )
        source = (FIXTURE / "maintenance-deployment-pause.sh").read_text()
        self.assertIn("get --raw '/api/v1/namespaces/perf-platform/pods?labelSelector=", source)
        self.assertNotIn("trap ", source)
        adoption = (FIXTURE / "adoption-parent.sh").read_text()
        self.assertLess(
            adoption.index('source "$source_dir/maintenance-deployment-pause.sh"'),
            adoption.index("k delete --raw"),
        )

    @unittest.skipUnless(BASH and shutil.which("jq"), "Bash/jq required for settlement predicates")
    def test_settlement_uses_raw_pod_list_and_refuses_foreign_or_unsettled_state(self):
        assert BASH
        source = (FIXTURE / "maintenance-deployment-pause.sh").read_text()
        functions = source[
            source.index("maintenance_deployment_pods() {") : source.index(
                "\ndeployment_previous_stage="
            )
        ]
        shell = (
            r"""
set -euo pipefail
fixture=$1
k() { echo "$*" >> "$fixture/trace"; if [[ $* == *--raw* ]]; then cat "$fixture/pods.json"; else cat "$fixture/deployment.json"; fi; }
sleep() { SECONDS=$((SECONDS + 121)); }
"""
            + functions
            + "\nmaintenance_deployment_settled case\n"
        )
        deployment: dict[str, Any] = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"uid": "owned", "generation": 7},
            "spec": {"replicas": 0},
            "status": {"observedGeneration": 7},
        }
        pods: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "PodList",
            "metadata": {"resourceVersion": "10"},
            "items": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "settle.sh"
            script.write_text(shell, newline="\n")
            for mode in (
                "success",
                "foreign",
                "stale",
                "nonzero",
                "desired",
                "deleting",
                "remaining",
                "paginated",
                "generic-list",
                "unversioned",
            ):
                fixture = directory / mode
                fixture.mkdir()
                current, listed = deepcopy(deployment), deepcopy(pods)
                if mode == "foreign":
                    current["metadata"]["uid"] = "foreign"
                if mode == "stale":
                    current["status"]["observedGeneration"] = 6
                if mode == "nonzero":
                    current["status"]["readyReplicas"] = 1
                if mode == "desired":
                    current["spec"]["replicas"] = 1
                if mode == "deleting":
                    current["metadata"]["deletionTimestamp"] = "pending"
                if mode == "remaining":
                    listed["items"] = [{"metadata": {"deletionTimestamp": "pending"}}]
                if mode == "paginated":
                    listed["metadata"]["continue"] = "next"
                if mode == "generic-list":
                    listed["kind"] = "List"
                if mode == "unversioned":
                    listed["metadata"].pop("resourceVersion")
                for name, value in (
                    ("case-before.deployment", deployment),
                    ("deployment", current),
                    ("pods", listed),
                ):
                    (fixture / f"{name}.json").write_text(json.dumps(value))
                result = subprocess.run(
                    [BASH, script.as_posix(), fixture.as_posix()],
                    cwd=ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                with self.subTest(mode=mode):
                    self.assertEqual(result.returncode == 0, mode == "success", result.stderr)
                    trace = (fixture / "trace").read_text()
                    self.assertIn(
                        "--raw /api/v1/namespaces/perf-platform/pods?labelSelector=", trace
                    )
                    for verb in ("create ", "patch ", "delete "):
                        self.assertNotIn(verb, trace)

    @unittest.skipUnless(BASH, "Bash required for the actual Deployment interruption branch")
    def test_common_deployment_branch_proves_stop_before_settlement_and_retention(self):
        assert BASH
        common = (
            (FIXTURE / "maintenance-partial-pause.sh")
            .read_text()
            .split("\nfor maintenance_partial_resource in root child;")[0]
        )
        shell = r"""
set -euo pipefail
fixture=$1 results=$1/results source_dir=unused KUBECONFIG=$1/kubeconfig
mode=preflight-parent scenario=initial current_stage=preflight-parent FAIL_MODE=$3
GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
adoption_retention_baseline=$1/maintenance-recovered
root_uid=root-uid adoption_root_case=root-corrected root_revision=selected
mkdir -p "$results"; touch "$fixture/owned" "$results/maintenance-recovery.outcome.txt"
start_stage() { :; }; complete_stage() { echo complete >> "$fixture/trace"; }
maintenance_snapshot() { echo "snapshot $*" >> "$fixture/trace"; }
root_state() { echo "state $*" >> "$fixture/trace"; }
root_retained() { echo unexpected-retention >> "$fixture/trace"; }
root_persistent_retained() { [[ $FAIL_MODE != retention ]]; }
adoption_retained() { echo "adoption $*" >> "$fixture/trace"; }
cmp() { return 0; }; cp() { printf '%s\n' "$(< "$1")" > "$2"; }
argo() { :; }; timeout() { [[ $* == *' stop '* ]]; }
k() { echo '{}'; }; a() { echo '{}'; }
jq() { if [[ $* != *'.json'* ]]; then while IFS= read -r line; do :; done; fi; echo fixture-id; }
uv() { echo "uv $*" >> "$fixture/trace"; if [[ $* == *" $FAIL_MODE deployment "* ]]; then return 1; fi; echo '{}'; }
maintenance_deployment_pods() { echo '{}'; }
maintenance_deployment_settled() { echo settled >> "$fixture/trace"; [[ $FAIL_MODE != settled ]]; }
source "$2"
maintenance_partial_case deployment
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "branch.sh"
            script.write_text(shell, newline="\n")
            source = directory / "common.sh"
            source.write_text(common, newline="\n")
            for mode in ("success", "stopped", "settled", "retention", "verify"):
                fixture = directory / mode
                fixture.mkdir()
                result = subprocess.run(
                    [BASH, script.as_posix(), fixture.as_posix(), source.as_posix(), mode],
                    cwd=ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                trace = (fixture / "trace").read_text()
                with self.subTest(mode=mode):
                    self.assertEqual(result.returncode == 0, mode == "success", result.stderr)
                    if mode == "stopped":
                        self.assertNotIn("settled\n", trace)
                    if mode in {"stopped", "settled"}:
                        self.assertNotIn("-after", trace)
                    if mode != "success":
                        self.assertNotIn("complete", trace)
                    else:
                        self.assertLess(trace.index("stopped deployment"), trace.index("settled\n"))
                        self.assertIn(".no-server.json", trace)
                        self.assertIn("verify deployment", trace)
                    self.assertNotIn("unexpected-retention", trace)
                    self.assertNotIn("adoption maintenance-partial-deployment-after", trace)

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
