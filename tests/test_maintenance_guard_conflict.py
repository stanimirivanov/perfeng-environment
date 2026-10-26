"""Exercise CI stale-guard provenance, metadata restoration and refusal boundaries."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import yaml
from test_maintenance_pause_integration import FIXTURE, evidence
from test_maintenance_pause_integration import MODULE as MAINTENANCE
from test_release_workflows_integration import BASH, ROOT

SPEC = importlib.util.spec_from_file_location(
    "maintenance_guard_conflict", FIXTURE / "maintenance_guard_conflict.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"maintenance_pause_evidence": MAINTENANCE}):
    SPEC.loader.exec_module(MODULE)

DIAGNOSTIC = (
    "Error from server (Invalid): the server rejected our request due to an error in our request\n"
)


def case(resource):
    workflow, workers, parameters, _, raw, lock, before, _ = evidence()
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
    index = order.index("pause-" + resource)
    for name in order[index + 1 :]:
        del nodes[name]
    nodes["pause"].update(phase="Failed", outputs={})
    failed = nodes["pause-" + resource]
    failed.update(phase="Error", outputs={})
    nodes[MODULE.BARRIER] = {
        "id": MODULE.BARRIER,
        "displayName": MODULE.BARRIER,
        "type": "Suspend",
        "phase": "Succeeded",
        "startedAt": nodes["guard-" + resource]["finishedAt"],
        "finishedAt": failed["startedAt"],
    }
    workflow["status"]["phase"] = "Error"
    workers = [w for w in workers if w["nodeID"] in nodes]
    next(w for w in workers if w["nodeID"] == failed["id"])["exitCode"] = 64
    target = (
        before["deployment"]
        if resource == "deployment"
        else before["applications"]["items"][0 if resource == "root" else 1]
    )
    target["apiVersion"] = "apps/v1" if resource == "deployment" else "argoproj.io/v1alpha1"
    target["metadata"].update(
        namespace="perf-platform" if resource == "deployment" else "argocd", resourceVersion="45"
    )
    original = deepcopy(target)
    added = deepcopy(target)
    field = "labels" if resource == "deployment" else "annotations"
    added["metadata"].update(resourceVersion="46")
    added["metadata"][field] = {
        **target["metadata"].get(field, {}),
        MODULE.MARKER: workflow["metadata"]["uid"],
    }
    restored = deepcopy(target)
    restored["metadata"]["resourceVersion"] = "47"
    return workflow, workers, parameters, raw, lock, before, original, added, restored


class MaintenanceGuardConflictTests(unittest.TestCase):
    def test_cli_preserves_private_evidence_and_rejects_retention_drift(self):
        for resource in MODULE.RESOURCES:
            workflow, workers, parameters, raw, _, before, original, added, restored = case(
                resource
            )
            prefix = "maintenance-stale-" + resource
            with tempfile.TemporaryDirectory() as temporary:
                fixture = Path(temporary)
                results = fixture / "results"
                results.mkdir()
                for suffix, value in (
                    ("workflow", workflow),
                    ("workers", workers),
                    ("target", original),
                    ("added", added),
                    ("restored", restored),
                ):
                    (fixture / f"{prefix}.{suffix}.json").write_text(json.dumps(value))
                (fixture / "parent-parameters.json").write_text(json.dumps(parameters))
                (fixture / "parent-request.json").write_bytes(raw)
                (fixture / f"{prefix}.log").write_text(DIAGNOSTIC)
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
                suspended = deepcopy(workflow)
                suspended["status"]["phase"] = "Running"
                suspended["status"]["nodes"][MODULE.BARRIER]["phase"] = "Running"
                del suspended["status"]["nodes"]["pause-" + resource]
                (fixture / f"{prefix}.barrier.json").write_text(json.dumps(suspended))
                for mode in ("prepare", "remove", "resume"):
                    with patch.object(
                        sys, "argv", ["guard", mode, resource, temporary, str(results)]
                    ):
                        MODULE.main()
                field = "labels" if resource == "deployment" else "annotations"
                self.assertEqual(
                    json.loads((fixture / f"{prefix}.add-patch.json").read_text())[-1]["path"],
                    "/metadata/" + field,
                )
                self.assertEqual(list(results.iterdir()), [])
                saved = {p: p.read_bytes() for p in fixture.iterdir() if p.is_file()}
                with patch.object(
                    sys, "argv", ["guard", "verify", resource, temporary, str(results)]
                ):
                    MODULE.main()
                outcome = results / f"{prefix}.outcome.json"
                self.assertEqual(
                    json.loads(outcome.read_text())["decision"], "stale-maintenance-patch-refused"
                )
                self.assertNotIn("private", outcome.read_text())
                self.assertEqual(saved, {p: p.read_bytes() for p in saved})
                outcome.unlink()
                (fixture / f"{prefix}-after.secrets.json").write_text("{}")
                with (
                    patch.object(
                        sys, "argv", ["guard", "verify", resource, temporary, str(results)]
                    ),
                    self.assertRaises(AssertionError),
                ):
                    MODULE.main()
                self.assertFalse(outcome.exists())

    @unittest.skipUnless(BASH, "Bash required for actual fixture failure sequencing")
    def test_shell_never_resumes_after_setup_failure_and_runs_all_three_boundaries(self):
        assert BASH
        source = (FIXTURE / "maintenance-guard-conflict.sh").as_posix()
        shell = r"""
set -euo pipefail
fixture=$1 results=$1/results source_dir=unused KUBECONFIG=$1/kubeconfig
mode=preflight-parent scenario=initial current_stage=preflight-parent FAIL_MODE=$3
GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
adoption_retention_baseline=$1/maintenance-recovered
mkdir -p "$results"
touch "$fixture/owned" "$results/maintenance-recovery.outcome.txt"
start_stage() { :; }; complete_stage() { :; }
maintenance_snapshot() { :; }; root_state() { :; }; root_retained() { :; }; adoption_retained() { :; }
cmp() { return 0; }
cp() { printf '%s\n' "$(< "$1")" > "$2"; }
argo() { :; }
timeout() { return 1; }
k() { printf 'k %s\n' "$*" >> "$fixture/trace"; echo '{}'; }
a() { printf 'a %s\n' "$*" >> "$fixture/trace"; echo '{}'; }
jq() { if [[ $* != *'.json'* ]]; then while IFS= read -r line; do :; done; fi; echo fixture-id; }
uv() {
  printf 'uv %s\n' "$*" >> "$fixture/trace"
  if [[ $* == *" $FAIL_MODE "* && $FAIL_MODE != success ]]; then return 1; fi
  echo '{}'
}
source "$2"
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "rehearsal.sh"
            script.write_text(shell, encoding="utf-8", newline="\n")
            for mode in ("success", "barrier", "prepare", "remove", "resume", "verify"):
                fixture = directory / mode
                fixture.mkdir()
                process = subprocess.run(
                    [BASH, script.as_posix(), fixture.as_posix(), source, mode],
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
                    if mode in {"barrier", "prepare", "remove", "resume"}:
                        self.assertNotIn("a resume", trace)
                    if mode in {"barrier", "prepare"}:
                        self.assertNotIn("k -n argocd patch", trace)
                    if mode == "success":
                        for resource in MODULE.RESOURCES:
                            self.assertIn("verify " + resource, trace)
                        self.assertEqual(trace.count("a resume"), 3)
                        self.assertEqual(trace.count("--type=json"), 6)
                    self.assertNotIn("delete", trace)
        adoption = (FIXTURE / "adoption-parent.sh").read_text()
        self.assertLess(
            adoption.index('source "$source_dir/maintenance-guard-conflict.sh"'),
            adoption.index("k delete --raw"),
        )

    def test_each_template_reverses_exactly_and_installed_tampering_is_refused(self):
        source = yaml.safe_load(
            (MODULE.DIRECTORY / "release-maintenance-pause-v1.yaml").read_text()
        )
        for resource in MODULE.RESOURCES:
            value = MODULE.template(resource)
            steps = value["spec"]["templates"][0]["steps"]
            index = next(i for i, group in enumerate(steps) if group[0]["name"] == MODULE.BARRIER)
            self.assertEqual(steps[index - 1][0]["name"], "guard-" + resource)
            self.assertEqual(steps[index + 1][0]["name"], "pause-" + resource)
            del steps[index]
            self.assertEqual(
                value["spec"]["templates"].pop(), {"name": MODULE.BARRIER, "suspend": {}}
            )
            value["metadata"] = source["metadata"]
            self.assertEqual(value, source)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            installed = MODULE.template("root")
            installed["spec"]["serviceAccountName"] = "default"
            (fixture / "maintenance-stale-root.installed.json").write_text(json.dumps(installed))
            with (
                patch.object(sys, "argv", ["guard", "installed", "root", temporary, temporary]),
                self.assertRaises(AssertionError),
            ):
                MODULE.main()

    def test_real_local_json_patch_restores_metadata_and_stale_native_patch_refuses(self):
        kubectl = shutil.which("kubectl")
        if not kubectl:
            self.skipTest("kubectl required for actual local JSON Patch replay")
        with tempfile.TemporaryDirectory() as temporary:
            path, payload = Path(temporary) / "object.json", Path(temporary) / "patch.json"
            for resource in MODULE.RESOURCES:
                workflow, _, _, _, _, _, original, added, restored = case(resource)
                uid = workflow["metadata"]["uid"]
                for current, patch_document in (
                    (original, MODULE.marker_patch(resource, original, original, uid, False)),
                    (added, MODULE.marker_patch(resource, original, added, uid, True)),
                ):
                    path.write_text(json.dumps(current))
                    payload.write_text(json.dumps(patch_document))
                    result = subprocess.run(
                        [
                            kubectl,
                            "patch",
                            "--local",
                            "--type=json",
                            "-f",
                            str(path),
                            "--patch-file",
                            str(payload),
                            "-o",
                            "json",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)["spec"], original["spec"])
                    self.assertEqual(
                        MODULE.MARKER
                        in json.loads(result.stdout)["metadata"].get(
                            "labels" if resource == "deployment" else "annotations", {}
                        ),
                        current is original,
                    )
                guard = {
                    "uid": original["metadata"]["uid"],
                    "spec": original["spec"],
                    "resourceVersion": "40",
                }
                guard["syncPolicy"] = original["spec"].get("syncPolicy")
                MODULE.restored(resource, original, added, restored, guard, uid)
                path.write_text(json.dumps(restored))
                native = yaml.safe_load(
                    (MODULE.DIRECTORY / "maintenance-resources-v1.yaml").read_text()
                )
                manifest = next(
                    t for t in native["spec"]["templates"] if t["name"] == "patch-" + resource
                )["resource"]["manifest"]
                payload.write_text(
                    re.sub(
                        r'\{\{=toJson\(jsonpath\(inputs.parameters.observation, "\$\.([a-zA-Z]+)"\)\)\}\}',
                        lambda match, guard=guard: json.dumps(guard[match[1]]),
                        manifest,
                    )
                )
                result = subprocess.run(
                    [
                        kubectl,
                        "patch",
                        "--local",
                        "--type=json",
                        "-f",
                        str(path),
                        "--patch-file",
                        str(payload),
                        "-o",
                        "json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertNotEqual(result.returncode, 0)

    def test_ready_native_failure_and_restoration_reject_unrelated_or_changed_evidence(self):
        for resource in MODULE.RESOURCES:
            workflow, workers, parameters, raw, lock, before, original, added, restored = case(
                resource
            )
            guard = MODULE.ready(workflow, resource, parameters, raw, lock, before)
            MODULE.restored(resource, original, added, restored, guard, workflow["metadata"]["uid"])
            MODULE.refused(workflow, workers, resource, DIAGNOSTIC)
            for change in (
                "exit",
                "account",
                "log",
                "barrier",
                "write",
                "result",
                "guard",
                "ledger",
                "restoration",
                "marker",
                "uid",
                "generation",
            ):
                with self.subTest(resource=resource, change=change):
                    values = case(resource)
                    wf, ws, ps, data, lk, state, old, marked, current = values
                    log = DIAGNOSTIC
                    if change == "exit":
                        next(w for w in ws if w["nodeID"] == "pause-" + resource)["exitCode"] = 1
                    elif change == "account":
                        ws[0]["account"] = "default"
                    elif change == "log":
                        log = "Forbidden"
                    elif change == "barrier":
                        wf["status"]["nodes"][MODULE.BARRIER]["phase"] = "Running"
                    elif change == "write":
                        wf["status"]["nodes"]["hidden"] = {
                            "id": "hidden",
                            "displayName": "hidden",
                            "type": "Pod",
                            "phase": "Succeeded",
                            "templateRef": {
                                "name": "perfeng-maintenance-resources-v1",
                                "template": "patch-deployment",
                            },
                        }
                    elif change == "result":
                        wf["status"]["nodes"]["pause"]["outputs"] = {
                            "parameters": [{"name": "result", "value": "{}"}]
                        }
                    elif change == "guard":
                        wf["status"]["nodes"]["guard-" + resource]["phase"] = "Error"
                    elif change == "ledger":
                        state["records"][0]["metadata"]["uid"] = "different"
                    elif change == "restoration":
                        current["spec"]["unexpected"] = True
                    elif change == "marker":
                        marked["metadata"]["labels" if resource == "deployment" else "annotations"][
                            MODULE.MARKER
                        ] = "wrong"
                    elif change == "uid":
                        current["metadata"]["uid"] = "replaced"
                    elif change == "generation":
                        current["metadata"]["generation"] = old["metadata"].get("generation", 0) + 1
                    with self.assertRaises(AssertionError):
                        observed = MODULE.ready(wf, resource, ps, data, lk, state)
                        MODULE.restored(
                            resource, old, marked, current, observed, wf["metadata"]["uid"]
                        )
                        MODULE.refused(wf, ws, resource, log)
