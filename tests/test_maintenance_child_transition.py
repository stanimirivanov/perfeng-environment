"""Transition evidence must prove the native write and preserve the running baseline."""

import json
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import test_maintenance_application_response as application
from test_release_workflows_integration import BASH

with patch.dict("sys.modules", {"maintenance_write_denial": application.MODULE.denial}):
    MODULE = application.deployment.load("maintenance_child_transition")
ROOT = application.ROOT


def evidence():
    *_, before, _, _, _ = application.evidence("child")
    original = MODULE.child(before)
    original["metadata"]["labels"] = {"app.kubernetes.io/managed-by": "perfeng-environment"}
    original["spec"]["syncPolicy"]["automated"] = {
        "enabled": False,
        "prune": False,
        "selfHeal": False,
    }
    original["status"] = {
        "sync": {"status": "Synced", "revision": "selected"},
        "health": {"status": "Healthy"},
    }
    target = deepcopy(original)
    target["spec"]["syncPolicy"]["automated"]["enabled"] = True
    target["metadata"].update(
        generation=original["metadata"]["generation"] + 1, resourceVersion="enabled-version"
    )
    after = deepcopy(before)
    MODULE.child(after)["metadata"].update(
        generation=original["metadata"]["generation"] + 2, resourceVersion="restored-version"
    )
    observation = {
        "uid": target["metadata"]["uid"],
        "resourceVersion": "fresh-guard",
        "spec": target["spec"],
        "syncPolicy": original["spec"]["syncPolicy"],
    }
    nodes = {}
    for name, leaf in (("guard-child", "guard-child"), ("pause-child", "patch-child")):
        nodes[name] = {
            "id": name,
            "displayName": name,
            "type": "Pod",
            "phase": "Succeeded",
            "templateRef": {"name": "perfeng-maintenance-resources-v1", "template": leaf},
            "startedAt": "2026-10-28T10:00:00Z"
            if name == "guard-child"
            else "2026-10-28T10:00:02Z",
            "finishedAt": "2026-10-28T10:00:01Z"
            if name == "guard-child"
            else "2026-10-28T10:00:03Z",
            "inputs": {
                "parameters": [
                    {
                        "name": "snapshot" if name == "guard-child" else "observation",
                        "value": json.dumps(target if name == "guard-child" else observation),
                    }
                ]
            },
        }
    nodes["guard-child"]["outputs"] = {
        "parameters": [{"name": "observation", "value": json.dumps(observation)}]
    }
    workflow = {
        "metadata": {"namespace": "argo-workflows", "uid": "transition-workflow"},
        "spec": {
            "workflowTemplateRef": {"name": MODULE.NAME},
            "arguments": {"parameters": [{"name": "snapshot", "value": json.dumps(target)}]},
        },
        "status": {"phase": "Succeeded", "nodes": nodes},
    }
    workers = [
        {
            "uid": name + "-worker",
            "nodeID": name,
            "account": "release-maintenance-pause",
            "exitCode": 0,
        }
        for name in nodes
    ]
    return before, target, after, workflow, workers


class ChildTransitionTests(unittest.TestCase):
    def test_enable_is_identity_and_full_spec_guarded_and_preserves_other_policy(self):
        before, target, *_ = evidence()
        original = MODULE.child(before)
        payload = MODULE.enable(original)
        self.assertEqual(
            [p["path"] for p in payload],
            [
                "/metadata/uid",
                "/metadata/resourceVersion",
                "/spec",
                "/spec/syncPolicy/automated/enabled",
            ],
        )
        self.assertEqual([p["op"] for p in payload], ["test", "test", "test", "replace"])
        MODULE.enabled(original, target)
        for mutate in (
            lambda o: o["metadata"].update(uid="foreign"),
            lambda o: o["metadata"].update(generation=99),
            lambda o: o["spec"]["syncPolicy"]["automated"].update(prune=True),
            lambda o: o.update(operation={"sync": {}}),
            lambda o: o["status"]["sync"].update(status="OutOfSync"),
        ):
            bad = deepcopy(target)
            mutate(bad)
            with self.assertRaises(AssertionError):
                MODULE.enabled(original, bad)

    def test_exact_native_workers_transition_and_retention_are_required(self):
        args = evidence()
        self.assertEqual(MODULE.verify(*args)["decision"], "maintenance-child-transition-verified")
        for mutate in (
            lambda a: MODULE.child(a[2])["metadata"].update(generation=7),
            lambda a: MODULE.child(a[2])["spec"]["syncPolicy"]["automated"].update(enabled=True),
            lambda a: a[2]["server-pods"]["items"][0]["metadata"].update(uid="replacement"),
            lambda a: a[2].update(records=[]),
            lambda a: a[3]["status"]["nodes"]["pause-child"].update(phase="Skipped"),
            lambda a: a[3]["status"]["nodes"]["pause-child"]["templateRef"].update(
                template="patch-root"
            ),
            lambda a: a[4][0].update(account="default"),
            lambda a: a[4][1].update(exitCode=64),
        ):
            bad = deepcopy(args)
            mutate(bad)
            with self.assertRaises(AssertionError):
                MODULE.verify(*bad)

    def test_ci_composition_references_only_published_child_leaves(self):
        obj = MODULE.template()
        self.assertEqual(obj["spec"]["serviceAccountName"], "release-maintenance-pause")
        self.assertEqual(
            obj["spec"]["synchronization"], {"mutexes": [{"name": "perfeng-local-external-state"}]}
        )
        leaves = [s for g in obj["spec"]["templates"][0]["steps"] for s in g]
        self.assertEqual(
            [s["templateRef"]["template"] for s in leaves], ["guard-child", "patch-child"]
        )
        self.assertEqual(len(obj["spec"]["templates"]), 1)
        self.assertNotIn("retryStrategy", json.dumps(obj))

    def test_cli_uses_private_snapshots_and_publishes_only_verified_outcome(self):
        before, target, after, workflow, workers = evidence()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            results = directory / "results"
            results.mkdir()
            for label, state in (("before", before), ("after", after)):
                for key, value in state.items():
                    (directory / f"{MODULE.PREFIX}-{label}.{key}.json").write_text(
                        json.dumps(value)
                    )
            for suffix, value in (
                ("enabled", target),
                ("enable-response", target),
                ("workflow", workflow),
                ("workers", workers),
            ):
                (directory / f"{MODULE.PREFIX}.{suffix}.json").write_text(json.dumps(value))
            for mode in ("prepare", "enabled", "verify"):
                with patch("sys.argv", ["transition", mode, str(directory), str(results)]):
                    MODULE.main()
            report = results / f"{MODULE.PREFIX}.outcome.json"
            self.assertEqual(
                set(json.loads(report.read_text())), {"decision", "workflowUID", "applicationUID"}
            )
            self.assertEqual(
                json.loads((directory / f"{MODULE.PREFIX}.parameters.json").read_text()),
                {"snapshot": json.dumps(target)},
            )
            report.unlink()
            workflow["status"]["phase"] = "Failed"
            (directory / f"{MODULE.PREFIX}.workflow.json").write_text(json.dumps(workflow))
            with (
                patch("sys.argv", ["transition", "verify", str(directory), str(results)]),
                self.assertRaises(AssertionError),
            ):
                MODULE.main()
            self.assertFalse(report.exists())

    @unittest.skipUnless(BASH, "Git Bash required")
    def test_shell_stops_before_adoption_on_every_unverified_boundary(self):
        assert BASH is not None
        text = (
            ROOT / "tests/integration/release-workflows/maintenance-child-transition.sh"
        ).read_text()
        library = text[text.index("maintenance_child_transition_case() {") :].rsplit(
            "maintenance_child_transition_case", 1
        )[0]
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; results=$1; source_dir=$1; fail=$3
KUBECONFIG="$fixture/kubeconfig"
current_stage=outer; adoption_retention_baseline=baseline; adoption_baseline=initial
start_stage() { echo stage >> "$fixture/trace"; }
complete_stage() { echo complete >> "$fixture/trace"; }
maintenance_write_snapshot() { echo snapshot >> "$fixture/trace"; }
root_retained() {
  case "$2" in
    *-before) echo retain-before >> "$fixture/trace"; [[ $fail != baseline-retention ]] ;;
    *-after) echo retain-after >> "$fixture/trace"; [[ $fail != retention ]] ;;
    *) return 2 ;;
  esac
}
maintenance_partial_workers() { echo '[]'; }
k() { echo "k $*" >> "$fixture/trace"; [[ $* != *' patch '* || $fail != enable ]]; echo '{}'; }
a() { echo '{}'; }; argo() { :; }; jq() { cat >/dev/null; echo workflow; }
timeout() { echo wait >> "$fixture/trace"; [[ $fail != wait ]]; }
uv() { echo "uv $*" >> "$fixture/trace"; [[ $* != *" $fail "* ]]; }
adoption_case() { echo adoption >> "$fixture/trace"; [[ $fail != adoption ]]; }
cmp() { echo compare >> "$fixture/trace"; [[ $fail != records ]]; }
source "$2"
maintenance_child_transition_case
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "case.sh").write_text(library, newline="\n")
            (directory / "run.sh").write_text(setup, newline="\n")
            for fail in (
                "pass",
                "baseline-retention",
                "retention",
                "prepare",
                "enable",
                "enabled",
                "wait",
                "verify",
                "adoption",
                "records",
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
                    cwd=ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                with self.subTest(fail=fail):
                    trace = (fixture / "trace").read_text().splitlines()
                    self.assertEqual(process.returncode, 0 if fail == "pass" else 1, process.stderr)
                    self.assertNotIn("unbound variable", process.stderr)
                    boundary = {
                        "pass": "complete",
                        "baseline-retention": "retain-before",
                        "retention": "retain-after",
                        "wait": "wait",
                        "adoption": "adoption",
                        "records": "compare",
                    }
                    if fail in boundary:
                        self.assertEqual(trace[-1], boundary[fail], process.stderr)
                    elif fail == "enable":
                        self.assertIn(" patch application perfeng-local-control-plane ", trace[-1])
                    else:
                        self.assertTrue(trace[-1].startswith("uv "))
                        self.assertIn(f" {fail} ", trace[-1])
                    if fail not in {"pass", "adoption", "records"}:
                        self.assertNotIn("adoption", trace)
                    if fail != "pass":
                        self.assertNotIn("complete", trace)
                    else:
                        self.assertEqual(trace[-1], "complete")
                    if fail == "retention":
                        self.assertIn("wait", trace)
                        self.assertEqual(trace[-1], "retain-after")
                    if fail == "baseline-retention":
                        self.assertEqual(trace[-1], "retain-before")


if __name__ == "__main__":
    unittest.main()
