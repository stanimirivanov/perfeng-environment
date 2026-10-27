"""Prove bounded pause/recovery evidence and reject changes outside the approved Pod."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from test_adoption_parent_integration import evidence as adoption_evidence
from test_readiness_parent_integration import MODULE as READINESS
from test_release_workflows_integration import BASH

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
SPEC = importlib.util.spec_from_file_location(
    "maintenance_pause_evidence", FIXTURE / "maintenance_pause_evidence.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"readiness_parent_evidence": READINESS}):
    SPEC.loader.exec_module(MODULE)


def evidence():
    workflow, parameters, preflight, raw, raw_lock, api, _ = adoption_evidence(False)
    nodes = workflow["status"]["nodes"]
    health = READINESS.outputs(nodes["readiness"])["result"]
    root = READINESS.outputs(nodes["root"])["observation"]
    child: dict[str, Any] = {
        "kind": "Application",
        "metadata": {"name": "perfeng-local-control-plane", "uid": health["applicationUID"]},
        "spec": {"syncPolicy": {"automated": {"enabled": False}}},
    }
    deployment: dict[str, Any] = {
        "kind": "Deployment",
        "metadata": {
            "name": "control-plane",
            "uid": health["resources"][2]["uid"],
            "generation": 3,
        },
        "spec": {"replicas": 1},
        "status": {
            "observedGeneration": 3,
            "replicas": 1,
            "readyReplicas": 1,
            "updatedReplicas": 1,
            "availableReplicas": 1,
        },
    }
    pod: dict[str, Any] = {
        "metadata": {
            "name": "control-plane-old",
            "namespace": "perf-platform",
            "uid": "server-old",
            "labels": {"pod-template-hash": "same"},
            "ownerReferences": [
                {"kind": "ReplicaSet", "uid": "replicaset-owned", "controller": True}
            ],
        },
        "spec": {
            "serviceAccountName": "control-plane",
            "volumes": [
                {
                    "name": "kube-api-access-old",
                    "projected": {
                        "sources": [
                            {"serviceAccountToken": {"expirationSeconds": 3607, "path": "token"}}
                        ]
                    },
                }
            ],
            "containers": [
                {
                    "name": "server",
                    "image": health["image"],
                    "volumeMounts": [
                        {
                            "name": "kube-api-access-old",
                            "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
                        }
                    ],
                }
            ],
        },
        "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
    }
    before: dict[str, Any] = {
        "applications": {"items": [root, child]},
        "deployment": deployment,
        "serverPod": {"items": [pod]},
        "server": [
            {
                "kind": "Deployment",
                "uid": deployment["metadata"]["uid"],
                "spec": deepcopy(deployment["spec"]),
            }
        ],
        "workloads": [
            {
                "kind": "Deployment",
                "namespace": "perf-platform",
                "name": "control-plane",
                "uid": deployment["metadata"]["uid"],
                "spec": deepcopy(deployment["spec"]),
            }
        ],
        "pods": [
            {"uid": "foundation-a", "spec": {"unchanged": "postgres"}},
            {"uid": "foundation-b", "spec": {"unchanged": "seaweedfs"}},
            {"uid": pod["metadata"]["uid"], "spec": deepcopy(pod["spec"])},
        ],
        "records": sorted([api["receipt"], api["pointer"]], key=lambda o: o["metadata"]["name"]),
        "secrets": {"private": "unchanged"},
        "config": {"runtime": "unchanged"},
        "git": {"credential": "unchanged"},
        "namespace": {"metadata": {"uid": preflight["clusterUID"]}},
    }
    stopped = deepcopy(before)
    stopped["serverPod"]["items"] = []
    stopped["pods"] = stopped["pods"][:2]
    stopped["deployment"]["spec"]["replicas"] = stopped["server"][0]["spec"]["replicas"] = stopped[
        "workloads"
    ][0]["spec"]["replicas"] = 0
    stopped["deployment"]["status"].update(
        replicas=0, readyReplicas=0, updatedReplicas=0, availableReplicas=0
    )
    checkpoint = {
        "schemaVersion": 1,
        "kind": "PerfengMaintenanceCheckpoint",
        "environment": "perfeng-local",
        "status": "quiescence-observed",
        "podListResourceVersion": "700",
        **{
            k: preflight[k]
            for k in ("requestRevision", "requestDigest", "selectionRevision", "clusterUID")
        },
        "resources": [
            {
                "kind": o["kind"],
                "namespace": "argocd" if o["kind"] == "Application" else "perf-platform",
                "name": o["metadata"]["name"],
                "uid": o["metadata"]["uid"],
                "resourceVersion": "50",
            }
            for o in [root, child, deployment]
        ],
    }
    wanted = (
        "preflight",
        "postgres",
        "seaweedfs",
        "application",
        "deployment",
        "service",
        "root",
        "pointer",
        "receipt",
        "root-after",
    )
    kept = {name: nodes[name] for name in wanted if name in nodes}
    kept["pointer"] = deepcopy(nodes["current"])
    kept["pointer"].update(id="pointer", displayName="pointer")
    kept["receipt"] = deepcopy(nodes["stored-receipt"])
    kept["receipt"].update(id="receipt", displayName="receipt")
    kept["baseline"] = deepcopy(nodes["finish"])
    kept["baseline"].update(id="baseline", displayName="baseline")
    kept["readiness"] = deepcopy(nodes["verify"])
    kept["readiness"].update(id="readiness", displayName="readiness")
    for name, snapshot in (("application", child), ("deployment", deployment)):
        observation = READINESS.outputs(kept[name])["observation"]
        observation["snapshot"] = snapshot
        kept[name]["outputs"]["parameters"][0]["value"] = json.dumps(observation)
    for resource, snapshot in (("root", root), ("child", child), ("deployment", deployment)):
        for operation in ("guard", "pause"):
            name = operation + "-" + resource
            kept[name] = {
                "id": name,
                "displayName": name,
                "type": "Pod",
                "phase": "Succeeded",
                "templateRef": {
                    "name": "perfeng-maintenance-resources-v1",
                    "template": ("patch" if operation == "pause" else operation) + "-" + resource,
                },
                "outputs": {
                    "parameters": [
                        {
                            "name": "observation",
                            "value": json.dumps(
                                {
                                    "uid": snapshot["metadata"]["uid"],
                                    "resourceVersion": "40",
                                    "spec": snapshot["spec"],
                                }
                            ),
                        }
                    ]
                },
            }
    kept["observe"] = {
        "id": "observe",
        "displayName": "observe",
        "type": "Pod",
        "phase": "Succeeded",
        "outputs": {"parameters": [{"name": "result", "value": json.dumps(checkpoint)}]},
    }
    order = [
        "preflight",
        "postgres",
        "seaweedfs",
        "application",
        "deployment",
        "service",
        "readiness",
        "root",
        "pointer",
        "receipt",
        "root-after",
        "baseline",
        "guard-root",
        "pause-root",
        "guard-child",
        "pause-child",
        "guard-deployment",
        "pause-deployment",
        "observe",
    ]
    for index, name in enumerate(order):
        when = datetime(2026, 10, 26, tzinfo=UTC) + timedelta(seconds=index * 2)
        kept[name].update(
            startedAt=when.isoformat(), finishedAt=(when + timedelta(seconds=1)).isoformat()
        )
    kept["pause"] = {
        "id": "pause",
        "displayName": "pause",
        "type": "Steps",
        "phase": "Succeeded",
        "outputs": deepcopy(kept["observe"]["outputs"]),
    }
    workflow["metadata"]["name"] = "pause"
    workflow["spec"]["workflowTemplateRef"]["name"] = "perfeng-release-maintenance-pause-v1"
    workflow["status"]["nodes"] = kept
    workers = [
        {
            "nodeID": n["id"],
            "uid": "worker-" + n["id"],
            "account": "release-maintenance-pause",
            "exitCode": 0,
        }
        for n in kept.values()
        if n["type"] == "Pod"
    ]
    return workflow, workers, parameters, preflight, raw, raw_lock, before, stopped


def recovered(before):
    value = deepcopy(before)
    pod = value["serverPod"]["items"][0]
    pod["metadata"].update(uid="server-new", name="control-plane-new")
    pod["spec"]["volumes"][0]["name"] = "kube-api-access-new"
    pod["spec"]["containers"][0]["volumeMounts"][0]["name"] = "kube-api-access-new"
    value["pods"][-1] = {"uid": pod["metadata"]["uid"], "spec": deepcopy(pod["spec"])}
    return value


class MaintenancePauseIntegrationTests(unittest.TestCase):
    def test_cli_snapshot_paths_preserve_originals_and_publish_only_verified_checkpoint(self):
        workflow, workers, parameters, preflight, raw, _, before, stopped = evidence()
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
            for label, value in (
                ("before", before),
                ("paused", stopped),
                ("recovered", recovered(before)),
            ):
                for key, suffix in mapping.items():
                    (fixture / f"maintenance-{label}.{suffix}.json").write_text(
                        json.dumps(value[key]), encoding="utf-8"
                    )
            for path, value in (
                (fixture / "parent-parameters.json", parameters),
                (results / "parent-first.result.json", preflight),
                (results / "maintenance-pause.workflow.json", workflow),
                (results / "maintenance-pause.workers.json", workers),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
            (fixture / "parent-request.json").write_bytes(raw)
            originals = {p: p.read_bytes() for p in fixture.glob("*.json")}
            for mode in ("paused", "recovered"):
                with patch.object(sys, "argv", ["evidence", mode, str(fixture), str(results)]):
                    MODULE.main()
            self.assertEqual({p: p.read_bytes() for p in originals}, originals)
            output = (results / "maintenance-pause.result.json").read_text()
            self.assertEqual(json.loads(output)["kind"], "PerfengMaintenanceCheckpoint")
            self.assertNotIn("private", output)
            self.assertNotIn("credential", output)

    @unittest.skipUnless(BASH, "Bash is required for fixture orchestration")
    def test_recovery_and_baseline_advance_require_both_successful_evidence_gates(self):
        assert BASH
        shell = r"""
set -euo pipefail
fixture=$1 results=$2 source_dir=$3 failure=$4
mode=preflight-parent scenario=initial current_stage=preflight-parent
KUBECONFIG="$fixture/kubeconfig"
touch "$fixture/owned" "$fixture/adoption-retained.records.json"
root_uid=root cluster_uid=cluster adoption_baseline=first
trap 'printf "baseline=%s\npods=%s\n" "${adoption_retention_baseline:-original}" "${root_server_pods_file:-original}"' EXIT
k() { printf '{}\n'; }
a() { touch "$fixture/submitted"; printf '{"metadata":{"name":"pause-ci"}}\n'; }
jq() { cat > /dev/null; if [[ $* == *'.metadata.name'* ]]; then echo pause-ci; else echo '{}'; fi; }
timeout() { return 0; }
cmp() { return 0; }
start_stage() { current_stage=$1; echo "stage=$1"; }
complete_stage() { :; }
root_state() { :; }
root_retained() { :; }
root_persistent_retained() { :; }
adoption_records() { echo '[]' > "$1"; }
root_child_snapshot() { echo '{}' > "$1"; }
server_inputs_snapshot() { echo '{}' > "$1"; }
server_config_snapshot() { echo '{}' > "$1"; }
uv() {
  if [[ $* == *maintenance_pause_evidence.py* ]]; then
    if [[ $* == *' paused '* ]]; then step=paused; else step=recovered; fi
    echo "evidence=$step"
    [[ $failure != "$step" ]] || return 17
  fi
}
argocd_case() { echo recovery-sync; }
server_case() { echo recovered-http; }
adoption_case() { echo fresh-adoption; }
source() {
  if [[ $1 == */maintenance-authority.sh ]]; then
    echo authority-audit
    [[ $failure != authority ]] || return 17
  else builtin source "$@"; fi
}
source "$source_dir/maintenance-pause.sh"
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "rehearsal.sh"
            script.write_text(shell, encoding="utf-8", newline="\n")
            for failure in ("authority", "paused", "recovered", "none"):
                with self.subTest(failure=failure):
                    fixture = directory / failure
                    results = fixture / "results"
                    results.mkdir(parents=True)
                    result = subprocess.run(
                        [
                            BASH,
                            script.as_posix(),
                            fixture.as_posix(),
                            results.as_posix(),
                            FIXTURE.as_posix(),
                            failure,
                        ],
                        cwd=ROOT,
                        env={**os.environ, "BASH_ENV": ""},
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(
                        result.returncode, 0 if failure == "none" else 17, result.stderr
                    )
                    if failure in {"authority", "paused"}:
                        self.assertNotIn("recovery-sync", result.stdout)
                    else:
                        self.assertIn("recovery-sync", result.stdout)
                    if failure != "none":
                        self.assertIn("baseline=original", result.stdout)
                        self.assertIn("pods=original", result.stdout)
                        self.assertNotIn("fresh-adoption", result.stdout)
                        if failure == "authority":
                            self.assertFalse((fixture / "submitted").exists())
                    else:
                        self.assertIn(
                            "baseline=" + fixture.as_posix() + "/maintenance-recovered",
                            result.stdout,
                        )
                        self.assertIn("fresh-adoption", result.stdout)

    def test_complete_native_sequence_and_explicit_recovery(self):
        values = evidence()
        self.assertEqual(MODULE.success(*values)["kind"], "PerfengMaintenanceCheckpoint")
        MODULE.retained(values[-2], values[-1], paused=True)
        MODULE.retained(values[-2], recovered(values[-2]), paused=False)

    def test_refuses_wrong_worker_order_guard_baseline_checkpoint_and_partial_failure(self):
        for fault in (
            "account",
            "order",
            "guard",
            "ledger",
            "checkpoint",
            "failed",
            "stale",
            "pods",
        ):
            with self.subTest(fault=fault):
                values = list(evidence())
                workflow, workers, *_, before, stopped = values
                nodes = workflow["status"]["nodes"]
                if fault == "account":
                    workers[0]["account"] = "release-adoption"
                if fault == "order":
                    nodes["pause-deployment"]["startedAt"] = nodes["baseline"]["startedAt"]
                if fault == "guard":
                    nodes["guard-child"]["templateRef"]["template"] = "patch-child"
                if fault == "ledger":
                    before["records"][0]["metadata"]["uid"] = "replaced"
                if fault == "checkpoint":
                    nodes["observe"]["outputs"] = {}
                if fault == "failed":
                    workflow["status"]["phase"] = "Failed"
                if fault == "stale":
                    stopped["deployment"]["status"]["observedGeneration"] = 2
                if fault == "pods":
                    stopped["serverPod"] = deepcopy(before["serverPod"])
                with self.assertRaises((AssertionError, KeyError, ValueError)):
                    MODULE.success(*values)

    def test_retention_refuses_every_unapproved_identity_or_spec_change(self):
        for paused in (True, False):
            for fault in (
                "foundation",
                "workload",
                "server",
                "records",
                "secrets",
                "config",
                "git",
                "root",
                "cluster",
                "pod",
                "owner",
                "image",
            ):
                with self.subTest(paused=paused, fault=fault):
                    *_, before, stopped = evidence()
                    value = stopped if paused else recovered(before)
                    if fault == "foundation":
                        value["pods"][0]["uid"] = "new-database"
                    if fault == "workload":
                        value["workloads"].append({"kind": "DaemonSet", "name": "unexpected"})
                    if fault == "server":
                        value["server"][0]["uid"] = "replaced-deployment"
                    if fault in {"records", "secrets", "config", "git"}:
                        value[fault] = []
                    if fault == "root":
                        value["applications"]["items"][0]["spec"]["syncPolicy"]["automated"][
                            "enabled"
                        ] = True
                    if fault == "cluster":
                        value["namespace"]["metadata"]["uid"] = "other-cluster"
                    if fault in {"pod", "owner", "image"}:
                        if paused:
                            value["serverPod"] = deepcopy(before["serverPod"])
                        elif fault == "pod":
                            value["serverPod"] = deepcopy(before["serverPod"])
                        elif fault == "owner":
                            value["serverPod"]["items"][0]["metadata"]["ownerReferences"][0][
                                "uid"
                            ] = "other-rs"
                        else:
                            value["serverPod"]["items"][0]["spec"]["containers"][0]["image"] = (
                                "wrong"
                            )
                    with self.assertRaises((AssertionError, KeyError)):
                        MODULE.retained(before, value, paused=paused)
