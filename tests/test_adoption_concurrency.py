"""Native mutex queue evidence and controller execution intervals fail closed."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from test_adoption_parent_integration import FIXTURE, MODULE, ROOT, evidence


def snapshots():
    holder = yaml.safe_load((FIXTURE / "adoption-lock-workflow.yaml").read_text())
    holder["metadata"].update(name="lock-holder", uid="holder-uid")
    holding = {
        "mutex": "argo-workflows/mutex/perfeng-local-external-state",
        "holder": "argo-workflows/lock-holder",
    }
    holder["status"] = {
        "phase": "Running",
        "synchronization": {"mutex": {"holding": [holding]}},
        "nodes": {
            "lock-holder": {
                "type": "Suspend",
                "phase": "Running",
                "startedAt": "2026-10-23T10:00:00Z",
            }
        },
    }
    workflow, parameters, *_ = evidence(False)
    queued = []
    for suffix in ("a", "b"):
        item = copy.deepcopy(workflow)
        item["metadata"].update(
            name=f"retry-{suffix}", uid=f"uid-{suffix}", namespace="argo-workflows"
        )
        item["status"] = {
            "phase": "Pending",
            "synchronization": {"mutex": {"waiting": [holding]}},
            "storedWorkflowTemplateSpec": {
                "synchronization": copy.deepcopy(holder["spec"]["synchronization"])
            },
        }
        queued.append(item)
    return holder, queued, parameters


def finished():
    held, queued, _ = snapshots()
    holder = copy.deepcopy(held)
    holder["status"]["phase"] = "Succeeded"
    holder["status"]["nodes"]["lock-holder"].update(
        phase="Succeeded", finishedAt="2026-10-23T10:00:10Z"
    )
    completed = []
    for index, item in enumerate(queued):
        workflow = copy.deepcopy(item)
        workflow["status"] = {
            "phase": "Succeeded",
            "nodes": {
                item["metadata"]["name"]: {
                    "type": "Steps",
                    "phase": "Succeeded",
                    "startedAt": f"2026-10-23T10:0{index + 1}:00Z",
                    "finishedAt": f"2026-10-23T10:0{index + 1}:30Z",
                }
            },
        }
        completed.append(workflow)
    return held, holder, queued, completed


class AdoptionConcurrencyTests(unittest.TestCase):
    def test_holder_has_only_native_suspend_and_matches_published_mutex(self):
        holder, queued, parameters = snapshots()
        production = yaml.safe_load(
            (ROOT / "gitops/release-workflows/release-adopt-existing-v3.yaml").read_text()
        )
        self.assertEqual(holder["spec"]["synchronization"], production["spec"]["synchronization"])
        self.assertEqual(holder["spec"]["templates"], [{"name": "hold", "suspend": {}}])
        self.assertEqual(
            holder["spec"]["serviceAccountName"], production["spec"]["serviceAccountName"]
        )
        self.assertLessEqual(holder["spec"]["activeDeadlineSeconds"], 300)
        MODULE.waiting(holder, queued, [], parameters)

    def test_wrong_holder_mutex_worker_or_identity_cannot_count_as_queued(self):
        for change in (
            "mutex",
            "holder",
            "deadline",
            "uid",
            "spec",
            "phase",
            "stored",
            "worker",
            "node",
            "result",
            "global-result",
            "suspended",
            "parameters",
        ):
            with self.subTest(change=change):
                holder, queued, parameters = snapshots()
                workers = []
                if change == "mutex":
                    holder["status"]["synchronization"]["mutex"]["holding"][0]["mutex"] = "other"
                elif change == "holder":
                    queued[0]["status"]["synchronization"]["mutex"]["waiting"] = [
                        {
                            "mutex": "argo-workflows/mutex/perfeng-local-external-state",
                            "holder": "other",
                        }
                    ]
                elif change == "deadline":
                    holder["spec"]["activeDeadlineSeconds"] = 0
                elif change == "uid":
                    queued[0]["metadata"]["uid"] = holder["metadata"]["uid"]
                elif change == "spec":
                    queued[0]["spec"]["workflowTemplateRef"]["name"] = "other"
                elif change == "phase":
                    queued[0]["status"]["phase"] = "Succeeded"
                elif change == "stored":
                    queued[0]["status"]["storedWorkflowTemplateSpec"]["synchronization"] = {}
                elif change == "worker":
                    workers = [{"uid": "unexpected-worker"}]
                elif change == "node":
                    queued[0]["status"]["nodes"] = {"worker": {"type": "Pod"}}
                elif change == "global-result":
                    queued[0]["status"]["outputs"] = {
                        "parameters": [{"name": "result", "value": "{}"}]
                    }
                elif change == "result":
                    queued[0]["status"]["nodes"] = {
                        "result": {
                            "type": "Steps",
                            "outputs": {"parameters": [{"name": "result", "value": "{}"}]},
                        }
                    }
                elif change == "suspended":
                    queued[0]["spec"]["suspend"] = True
                else:
                    parameters["request-revision"] = "c" * 40
                with self.assertRaises(AssertionError):
                    MODULE.waiting(holder, queued, workers, parameters)

    def test_only_same_queued_uids_with_disjoint_intervals_pass(self):
        result = MODULE.serialized(*finished())
        self.assertEqual(result["workflowUIDs"], ["uid-a", "uid-b"])
        for change in (
            "overlap",
            "before-release",
            "uid",
            "holder-uid",
            "phase",
            "node-phase",
            "time",
            "naive",
            "backwards",
        ):
            with self.subTest(change=change):
                held, holder, queued, completed = finished()
                first = completed[0]["status"]["nodes"][completed[0]["metadata"]["name"]]
                if change == "overlap":
                    completed[1]["status"]["nodes"][completed[1]["metadata"]["name"]][
                        "startedAt"
                    ] = first["startedAt"]
                elif change == "before-release":
                    first["startedAt"] = "2026-10-23T10:00:01Z"
                elif change == "uid":
                    completed[0]["metadata"]["uid"] = "replaced"
                elif change == "holder-uid":
                    holder["metadata"]["uid"] = "replaced"
                elif change == "phase":
                    completed[0]["status"]["phase"] = "Failed"
                elif change == "node-phase":
                    first["phase"] = "Failed"
                elif change == "time":
                    first["startedAt"] = "invalid"
                elif change == "naive":
                    first["startedAt"] = "2026-10-23T10:01:00"
                else:
                    first["finishedAt"] = "2026-10-23T09:00:00Z"
                with self.assertRaises((AssertionError, ValueError)):
                    MODULE.serialized(held, holder, queued, completed)

    def test_evidence_cli_handles_queue_and_completion_files(self):
        held, holder, queued, completed = finished()
        parameters = snapshots()[2]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, value in (
                ("adoption-lock-held.workflow.json", held),
                ("adoption-lock-holder.workflow.json", holder),
                ("adoption-lock-queued.workflows.json", queued),
                ("adoption-lock-queued.workers.json", []),
                ("parent-parameters.json", parameters),
                ("adoption-parent-concurrent-a.workflow.json", completed[0]),
                ("adoption-parent-concurrent-b.workflow.json", completed[1]),
            ):
                (directory / name).write_text(json.dumps(value), encoding="utf-8")
            for mode in ("waiting", "serialized"):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(FIXTURE / "adoption_parent_evidence.py"),
                        mode,
                        temporary,
                        temporary,
                        "concurrent",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    env={**os.environ, "PYTHONPATH": str(ROOT)},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads((directory / "adoption-lock.result.json").read_text())["decision"],
                "serialized-exact-retries",
            )
