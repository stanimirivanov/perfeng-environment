"""Exercise precise lingering-Pod attribution and fail-closed fixture ordering."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import test_maintenance_partial_pause as partial_tests
import test_release_maintenance_checkpoint as checkpoint_tests
from test_release_workflows_integration import BASH

FIXTURE = partial_tests.FIXTURE
SPEC = importlib.util.spec_from_file_location(
    "maintenance_lingering_pod", FIXTURE / "maintenance_lingering_pod.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict("sys.modules", {"maintenance_partial_pause": partial_tests.MODULE}):
    SPEC.loader.exec_module(MODULE)


def evidence() -> tuple[Any, ...]:
    fixture = checkpoint_tests.MaintenanceCheckpointTests()
    fixture.setUp()
    fixture.before[2]["spec"]["template"]["spec"]["containers"] = [
        {"image": "example@sha256:" + "a" * 64}
    ]
    expected = MODULE.manifest(fixture.before[2], "interrupted-uid")
    pod = deepcopy(expected)
    pod["metadata"].update(uid="fixture-pod", resourceVersion="31")
    pod["status"] = {"phase": "Pending"}
    states = [
        {
            "applications": {"items": fixture.before[:2]},
            "deployment": fixture.before[2],
            "namespace": fixture.namespace,
            "podList": {**fixture.pods, "items": [pod]},
        }
    ]
    states.append(deepcopy(states[0]))
    states[1]["podList"]["items"][0]["metadata"]["resourceVersion"] = "32"
    preflight = {
        "id": "preflight",
        "displayName": "preflight",
        "type": "Steps",
        "phase": "Succeeded",
        "startedAt": "2026-10-27T00:00:02Z",
        "finishedAt": "2026-10-27T00:00:03Z",
        "outputs": {"parameters": [{"name": "result", "value": json.dumps(fixture.preflight)}]},
    }
    observe = {
        "id": "observe",
        "displayName": "observe",
        "type": "Pod",
        "phase": "Error",
        "startedAt": "2026-10-27T00:00:04Z",
        "finishedAt": "2026-10-27T00:00:05Z",
        "templateRef": {"name": "perfeng-verify-release-quiescence-v3", "template": "run"},
    }
    parameters = {"selection-revision": checkpoint_tests.REVISION}
    workflow = {
        "metadata": {"name": "checkpoint", "uid": "refused-uid", "namespace": "argo-workflows"},
        "spec": {
            "workflowTemplateRef": {"name": "perfeng-release-maintenance-checkpoint-v3"},
            "arguments": {"parameters": [{"name": k, "value": v} for k, v in parameters.items()]},
        },
        "status": {
            "phase": "Error",
            "nodes": {
                "preflight": preflight,
                "observe": observe,
                "checkpoint": {"id": "checkpoint", "displayName": "checkpoint", "type": "Steps"},
            },
        },
    }
    interrupted = {
        "metadata": {"uid": "interrupted-uid"},
        "status": {
            "finishedAt": "2026-10-27T00:00:01Z",
            "nodes": {"preflight": deepcopy(preflight)},
        },
    }
    workers = [
        {
            "nodeID": "observe",
            "uid": "worker-uid",
            "account": "release-maintenance-checkpoint",
            "exitCode": 2,
        }
    ]
    return (
        workflow,
        workers,
        interrupted,
        parameters,
        fixture.raw,
        states,
        expected,
        pod,
        MODULE.REFUSAL + "\n",
    )


class MaintenanceLingeringPodTests(unittest.TestCase):
    def test_precise_refusal_requires_real_worker_and_valid_counterfactual(self):
        args = evidence()
        self.assertEqual(MODULE.refusal(*args)["podUID"], "fixture-pod")
        for index, mutate in (
            (0, lambda w: w["status"].update(phase="Succeeded")),
            (0, lambda w: w["spec"].update(shutdown="Stop")),
            (0, lambda w: w["metadata"].update(uid="interrupted-uid")),
            (
                0,
                lambda w: w["status"]["nodes"]["observe"].update(
                    outputs={"parameters": [{"name": "result", "value": "{}"}]}
                ),
            ),
            (0, lambda w: w["status"]["nodes"]["preflight"].update(phase="Failed")),
            (1, lambda w: w[0].update(exitCode=1)),
            (1, lambda w: w[0].update(account="release-maintenance-pause")),
            (5, lambda s: s[1]["podList"]["items"][0]["metadata"].update(uid="replacement")),
            (5, lambda s: s[1]["deployment"]["spec"].update(replicas=1)),
            (5, lambda s: s[1]["podList"]["metadata"].update({"continue": "next"})),
            (5, lambda s: s[1]["podList"].update(items=[])),
        ):
            bad = deepcopy(args)
            mutate(bad[index])
            with (
                self.subTest(index=index, mutate=mutate),
                self.assertRaises((AssertionError, ValueError)),
            ):
                MODULE.refusal(*bad)
        bad = list(deepcopy(args))
        bad[-1] = "Quiescence refused: invalid selection, observations or operational response\n"
        with self.assertRaises(AssertionError):
            MODULE.refusal(*bad)

    def test_gate_and_ownership_forbid_execution_adoption_and_foreign_cleanup(self):
        *_, expected, created, _ = evidence()
        self.assertFalse(expected["spec"]["automountServiceAccountToken"])
        self.assertEqual(MODULE.pod_identity(created, expected)["uid"], "fixture-pod")
        for mutate in (
            lambda p: p["spec"].update(schedulingGates=[]),
            lambda p: p["spec"].update(nodeName="node"),
            lambda p: p["spec"].update(volumes=[{}]),
            lambda p: p["metadata"].update(ownerReferences=[{}]),
            lambda p: p["metadata"].update(finalizers=["hold"]),
            lambda p: p["metadata"].update(name="control-plane"),
            lambda p: p["status"].update(phase="Running"),
            lambda p: p["status"].update(containerStatuses=[{}]),
        ):
            bad = deepcopy(created)
            mutate(bad)
            with self.assertRaises(AssertionError):
                MODULE.pod_identity(bad, expected)

    def test_fresh_checkpoint_must_follow_the_validated_rejected_execution(self):
        rejected = {
            "metadata": {"uid": "rejected"},
            "status": {"finishedAt": "2026-10-27T00:00:01Z"},
        }
        fresh: dict[str, Any] = {
            "metadata": {"uid": "fresh"},
            "status": {
                "nodes": {
                    "preflight": {
                        "id": "preflight",
                        "displayName": "preflight",
                        "type": "Steps",
                        "startedAt": "2026-10-27T00:00:02Z",
                    }
                }
            },
        }
        report = {"decision": "lingering-pod-refusal-verified", "workflowUID": "rejected"}
        MODULE.fresh_checkpoint(rejected, fresh, report)
        for change in ("uid", "time", "report"):
            candidate, proof = deepcopy(fresh), deepcopy(report)
            if change == "uid":
                candidate["metadata"]["uid"] = "rejected"
            elif change == "time":
                candidate["status"]["nodes"]["preflight"]["startedAt"] = "2026-10-27T00:00:00Z"
            else:
                proof["workflowUID"] = "unrelated"
            with self.assertRaises(AssertionError):
                MODULE.fresh_checkpoint(rejected, candidate, proof)

    def test_cli_exports_only_latest_identity_guard_after_refusal_and_retention(self):
        workflow, workers, interrupted, parameters, _, states, _, created, log = evidence()
        *_, before, paused = partial_tests.evidence()
        for state in (before, paused):
            state["applications"] = deepcopy(states[0]["applications"])
            state["namespace"] = deepcopy(states[0]["namespace"])
        before["deployment"] = deepcopy(states[0]["deployment"])
        before["deployment"]["spec"]["replicas"] = 1
        paused["deployment"] = deepcopy(states[0]["deployment"])
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            results = fixture / "results"
            results.mkdir()

            def write(name, value):
                (fixture / (name + ".json")).write_text(json.dumps(value))

            for key, value in before.items():
                write(
                    "maintenance-partial-deployment-before."
                    + ("server-pods" if key == "serverPod" else key),
                    value,
                )
            write("maintenance-partial-deployment-after.deployment", paused["deployment"])
            write("maintenance-partial-deployment.workflow", interrupted)
            write("parent-parameters", parameters)
            write("maintenance-lingering.workflow", workflow)
            write("maintenance-lingering.workers", workers)
            write("maintenance-lingering.created", created)
            (fixture / "maintenance-lingering.log").write_text(log)
            for label, state in zip(("before", "after"), states, strict=True):
                for key, value in paused.items():
                    if key != "serverPod":
                        write(f"maintenance-lingering-{label}.{key}", value)
                write(f"maintenance-lingering-{label}.pod-list", state["podList"])
            with patch("sys.argv", ["fixture", "refusal", str(fixture), str(results)]):
                MODULE.main()
            delete = fixture / "maintenance-lingering.delete.json"
            self.assertEqual(
                json.loads(delete.read_text()),
                {
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "preconditions": {"uid": "fixture-pod", "resourceVersion": "32"},
                },
            )
            delete.unlink()
            changed = deepcopy(states[1]["podList"])
            changed["items"][0]["metadata"]["uid"] = "foreign"
            write("maintenance-lingering-after.pod-list", changed)
            with (
                patch("sys.argv", ["fixture", "refusal", str(fixture), str(results)]),
                self.assertRaises(AssertionError),
            ):
                MODULE.main()
            self.assertFalse(delete.exists())

    @unittest.skipUnless(BASH, "Bash required")
    def test_real_shell_refuses_cleanup_or_recovery_after_failed_evidence(self):
        assert BASH
        shell = r"""
set -euo pipefail
fixture=$1 results=$1/results source_dir=unused KUBECONFIG=$1/kubeconfig
mode=preflight-parent scenario=initial GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
mkdir -p "$results"; touch "$fixture/owned" "$results/maintenance-partial-deployment.outcome.json"
fail=$3
start_stage() { :; }; complete_stage() { echo complete >> "$fixture/trace"; }
maintenance_snapshot() { :; }; maintenance_partial_root() { :; }; maintenance_deployment_pods() { echo '{}'; }
root_state() { :; }; root_persistent_retained() { [[ $fail != retention ]]; }
maintenance_partial_workers() { echo '[]'; }
maintenance_deployment_settled() { echo settled >> "$fixture/trace"; [[ $fail != settled ]]; }
jq() { cat >/dev/null; echo observer; }
a() { echo '{}'; }; argo() { return 1; }
timeout() { if [[ $fail == success ]]; then return 0; elif [[ $fail == timeout ]]; then return 124; else return 1; fi; }
k() { echo "$1" >> "$fixture/trace"; if [[ $1 == delete && $fail == delete ]]; then return 1; fi; echo '{}'; }
uv() { echo "uv $*" >> "$fixture/trace"; [[ $* != *' refusal '* || $fail != refusal ]]; }
source "$2"
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "run.sh"
            script.write_text(shell, newline="\n")
            for case in ("pass", "retention", "success", "timeout", "refusal", "delete", "settled"):
                fixture = root / case
                fixture.mkdir()
                process = subprocess.run(
                    [
                        BASH,
                        script.as_posix(),
                        fixture.as_posix(),
                        (FIXTURE / "maintenance-lingering-pod.sh").as_posix(),
                        case,
                    ],
                    cwd=partial_tests.ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                trace = (fixture / "trace").read_text()
                with self.subTest(case=case):
                    self.assertEqual(process.returncode == 0, case == "pass", process.stderr)
                    if case in {"retention", "success", "timeout", "refusal"}:
                        self.assertNotIn("delete\n", trace)
                    if case != "pass":
                        self.assertNotIn("complete\n", trace)


if __name__ == "__main__":
    unittest.main()
