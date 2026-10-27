"""Prove bounded Role narrowing, native denial attribution and restoration ordering."""

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

import test_maintenance_authority as authority_tests
import test_maintenance_guard_conflict as guard_tests
from test_release_workflows_integration import BASH, ROOT

FIXTURE = guard_tests.FIXTURE
SPEC = importlib.util.spec_from_file_location(
    "maintenance_write_denial", FIXTURE / "maintenance_write_denial.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict(
    sys.modules,
    {
        "maintenance_pause_evidence": guard_tests.MAINTENANCE,
        "maintenance_authority_evidence": authority_tests.MODULE,
    },
):
    SPEC.loader.exec_module(MODULE)


def role(resource):
    value = deepcopy(MODULE.source_role(resource))
    value["metadata"].update(uid="role-uid", resourceVersion="10")
    return value


def case(resource):
    workflow, workers, parameters, raw, lock, before, *_ = guard_tests.case(resource)
    workflow["metadata"]["namespace"] = "argo-workflows"
    workflow["spec"]["workflowTemplateRef"] = {"name": "perfeng-release-maintenance-pause-v1"}
    del workflow["status"]["nodes"][guard_tests.MODULE.BARRIER]
    _, _, plural, namespace, name = MODULE.authority.TARGETS[resource]
    group = "apps" if resource == "deployment" else "argoproj.io"
    log = f'Error from server (Forbidden): {plural}.{group} "{name}" is forbidden: User "{MODULE.authority.SUBJECT}" cannot patch resource "{plural}" in API group "{group}" in the namespace "{namespace}"\n'
    return workflow, workers, parameters, raw, lock, before, log


def grants(rules):
    return {
        (group, resource, name, verb)
        for rule in rules
        for group in rule["apiGroups"]
        for resource in rule["resources"]
        for name in rule.get("resourceNames", ["*"])
        for verb in rule["verbs"]
    }


class MaintenanceWriteDenialTests(unittest.TestCase):
    def test_narrowing_removes_exactly_one_named_patch_and_restores_exact_original(self):
        for resource in MODULE.TARGETS:
            original = role(resource)
            rules = MODULE.narrowed_rules(resource)
            _, _, plural, _, name = MODULE.authority.TARGETS[resource]
            group = "apps" if resource == "deployment" else "argoproj.io"
            self.assertEqual(
                grants(rules), grants(original["rules"]) - {(group, plural, name, "patch")}
            )
            narrow = MODULE.role_patch(resource, original, original, restore=False)
            self.assertEqual(
                narrow[:3],
                [
                    {"op": "test", "path": "/metadata/uid", "value": "role-uid"},
                    {"op": "test", "path": "/metadata/resourceVersion", "value": "10"},
                    {"op": "test", "path": "/rules", "value": original["rules"]},
                ],
            )
            current = deepcopy(original)
            current["metadata"]["resourceVersion"] = "11"
            current["rules"] = rules
            restore = MODULE.role_patch(resource, original, current, restore=True)
            self.assertEqual(restore[1]["value"], "11")
            self.assertEqual(
                restore[-1], {"op": "replace", "path": "/rules", "value": original["rules"]}
            )
            for mutate in (
                lambda r: r["metadata"].update(uid="foreign"),
                lambda r: r["metadata"].update(namespace="default"),
                lambda r: r["metadata"].update(finalizers=["hold"]),
                lambda r: r["rules"].append(
                    {"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}
                ),
            ):
                bad = deepcopy(current)
                mutate(bad)
                with self.assertRaises(AssertionError):
                    MODULE.role_patch(resource, original, bad, restore=True)

    def test_real_prefix_and_exact_forbidden_are_required_at_all_boundaries(self):
        for resource in MODULE.TARGETS:
            workflow, workers, parameters, raw, lock, before, log = case(resource)
            self.assertEqual(
                MODULE.refusal(resource, workflow, workers, parameters, raw, lock, before, log)[
                    "resource"
                ],
                resource,
            )
            for mutate in (
                lambda w: w["status"].update(phase="Succeeded"),
                lambda w: w["spec"].update(shutdown="Stop"),
                lambda w: w["spec"]["workflowTemplateRef"].update(name="copy"),
                lambda w, target=resource: w["status"]["nodes"]["guard-" + target].update(
                    phase="Failed"
                ),
                lambda w: w["status"]["nodes"]["baseline"].update(phase="Failed"),
                lambda w, target=resource: w["status"]["nodes"]["pause-" + target][
                    "templateRef"
                ].update(template="patch-other"),
                lambda w: w["status"]["nodes"].update(
                    later={
                        "id": "later",
                        "displayName": "checkpoint",
                        "type": "Pod",
                        "phase": "Succeeded",
                    }
                ),
            ):
                bad = deepcopy(workflow)
                mutate(bad)
                with self.subTest(resource=resource), self.assertRaises((AssertionError, KeyError)):
                    MODULE.refusal(resource, bad, workers, parameters, raw, lock, before, log)
            for wrong_log in (
                "timeout",
                log.replace("cannot patch", "cannot get"),
                log.replace("(Forbidden)", "(Invalid)"),
                log.replace(MODULE.authority.SUBJECT, "other"),
                log.replace('in the namespace "', 'in the namespace "wrong-'),
            ):
                with self.assertRaises(AssertionError):
                    MODULE.refusal(
                        resource, workflow, workers, parameters, raw, lock, before, wrong_log
                    )
            bad_workers = deepcopy(workers)
            next(w for w in bad_workers if w["nodeID"] == "pause-" + resource)["exitCode"] = 1
            with self.assertRaises(AssertionError):
                MODULE.refusal(resource, workflow, bad_workers, parameters, raw, lock, before, log)

    def test_cli_withholds_restore_patch_when_retention_or_native_evidence_fails(self):
        for resource in MODULE.TARGETS:
            workflow, workers, parameters, raw, _, before, log = case(resource)
            before["server-pods"] = before.pop("serverPod")
            original = role(resource)
            current = deepcopy(original)
            current["metadata"]["resourceVersion"] = "11"
            current["rules"] = MODULE.narrowed_rules(resource)
            prefix = "maintenance-write-denied-" + resource
            with tempfile.TemporaryDirectory() as temporary:
                fixture = Path(temporary)
                results = fixture / "results"
                results.mkdir()

                def save(name, value, directory=fixture):
                    (directory / (name + ".json")).write_text(json.dumps(value))

                for suffix, value in (
                    ("workflow", workflow),
                    ("workers", workers),
                    ("role-original", original),
                    ("role-narrowed", current),
                    ("role-current", current),
                ):
                    save(prefix + "." + suffix, value)
                save("parent-parameters", parameters)
                (fixture / "parent-request.json").write_bytes(raw)
                (fixture / (prefix + ".log")).write_text(log)
                for label in ("before", "after"):
                    for key, value in before.items():
                        save(f"{prefix}-{label}.{key}", value)
                with patch("sys.argv", ["fixture", "verify", resource, str(fixture), str(results)]):
                    MODULE.main()
                restore = fixture / (prefix + ".restore-patch.json")
                self.assertEqual(json.loads(restore.read_text())[-1]["value"], original["rules"])
                restore.unlink()
                save(f"{prefix}-after.secrets", {"changed": True})
                with (
                    patch("sys.argv", ["fixture", "verify", resource, str(fixture), str(results)]),
                    self.assertRaises(AssertionError),
                ):
                    MODULE.main()
                self.assertFalse(restore.exists())
                save(f"{prefix}-after.secrets", before["secrets"])
                (fixture / (prefix + ".log")).write_text("connection timed out")
                with (
                    patch("sys.argv", ["fixture", "verify", resource, str(fixture), str(results)]),
                    self.assertRaises(AssertionError),
                ):
                    MODULE.main()
                self.assertFalse(restore.exists())

    @unittest.skipUnless(BASH, "Bash required for actual restoration ordering")
    def test_effective_authority_requires_all_reads_and_only_one_denied_patch(self):
        assert BASH
        source = (FIXTURE / "maintenance-write-denial.sh").read_text()
        function = source[
            source.index("maintenance_write_access() {") : source.index(
                "\nmaintenance_write_case() {"
            )
        ]
        setup = r"""
set -euo pipefail
fixture=$1 mode=$2
maintenance_authority_k() {
  echo "$*" >> "$fixture/trace"
  if [[ $mode == api ]]; then echo 'API unavailable' >&2; return 1; fi
  if [[ $mode == read && $* == *'get applications.argoproj.io/perfeng-local-root'* ]]; then echo no; return 1; fi
  if [[ $mode == sibling && $* == *'patch applications.argoproj.io/perfeng-local-control-plane'* ]]; then echo no; return 1; fi
  if [[ $* == *'patch applications.argoproj.io/perfeng-local-root'* ]]; then
    echo no
    if [[ $mode == malformed ]]; then return 0; fi
    return 1
  fi
  echo yes
}
sleep() { SECONDS=$((SECONDS + 31)); }
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "authority.sh"
            script.write_text(
                setup + function + "\nmaintenance_write_access root auth\n", newline="\n"
            )
            for mode in ("pass", "api", "read", "sibling", "malformed"):
                fixture = directory / mode
                fixture.mkdir()
                process = subprocess.run(
                    [BASH, script.as_posix(), fixture.as_posix(), mode],
                    cwd=ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                with self.subTest(mode=mode):
                    self.assertEqual(process.returncode == 0, mode == "pass", process.stderr)
                    if mode == "pass":
                        self.assertEqual(len((fixture / "trace").read_text().splitlines()), 6)

    @unittest.skipUnless(BASH, "Bash required for actual restoration ordering")
    def test_shell_never_restores_after_failed_setup_refusal_or_retention(self):
        assert BASH
        source = (
            (FIXTURE / "maintenance-write-denial.sh")
            .read_text()
            .split("\nfor maintenance_write_resource", 1)[0]
        )
        setup = r"""
set -euo pipefail
fixture=$1 results=$1/results source_dir=unused KUBECONFIG=$1/kubeconfig
mode=preflight-parent scenario=initial GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
current_stage=parent cluster_uid=cluster root_uid=root adoption_root_case=case root_revision=revision
adoption_retention_baseline=$fixture/maintenance-recovered
mkdir -p "$results"; touch "$fixture/owned" "$results/maintenance-recovery.outcome.txt"
fail=$2
start_stage() { :; }; complete_stage() { echo complete >> "$fixture/trace"; }
maintenance_snapshot() { :; }; root_state() { :; }
root_retained() { [[ $fail != retention ]]; }
maintenance_authority_k() { :; }
k() {
  if [[ $* == *'get namespace kube-system'* ]]; then echo cluster; return; fi
  if [[ $* == *'restore-patch.json'* ]]; then echo restore >> "$fixture/trace"; [[ $fail != restore ]] || return 1; fi
  echo '{}'
}
a() { echo '{}'; }; argo() { :; }
timeout() { if [[ $fail == success ]]; then return 0; elif [[ $fail == timeout ]]; then return 124; else return 1; fi; }
jq() { cat >/dev/null; echo worker; }
uv() { echo "uv $*" >> "$fixture/trace"; [[ $* != *" $fail root "* ]]; }
"""
        tail = r"""
maintenance_write_access() { echo "access $1" >> "$fixture/trace"; [[ $1 != root || $fail != auth ]]; }
maintenance_write_case root
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "fixture.sh"
            library = directory / "cases.sh"
            library.write_text(source, newline="\n")
            script.write_text(setup + '\nsource "$3"\n' + tail, newline="\n")
            for mode in (
                "pass",
                "prepare",
                "narrowed",
                "auth",
                "success",
                "timeout",
                "retention",
                "verify",
                "restore",
                "restored",
            ):
                fixture = directory / mode
                fixture.mkdir()
                process = subprocess.run(
                    [BASH, script.as_posix(), fixture.as_posix(), mode, library.as_posix()],
                    cwd=ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                trace = (fixture / "trace").read_text()
                with self.subTest(mode=mode):
                    self.assertEqual(process.returncode == 0, mode == "pass", process.stderr)
                    if mode not in {"pass", "restore", "restored"}:
                        self.assertNotIn("restore\n", trace)
                    if mode != "pass":
                        self.assertNotIn("complete\n", trace)


if __name__ == "__main__":
    unittest.main()
