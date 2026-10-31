"""Qualification denial preserves a completed phase and gates every later action."""

import json
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import test_database_preparation_acceptance as preparation
from test_release_workflows_integration import BASH

MODULE, SCOPE = preparation.MODULE, preparation.SCOPE


def run(mode, root, results):
    with patch("sys.argv", ["preparation", mode, str(root), str(results)]):
        MODULE.main()


class PreparationQualificationDenialTests(unittest.TestCase):
    def test_barrier_is_reversible_and_only_completed_migration_can_reach_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            preparation.populate(root, results)
            run("ci-template", root, results)
            wanted = preparation.yaml.safe_load(
                (MODULE.parent.DIRECTORY / MODULE.SOURCES[0]).read_text()
            )["spec"]
            spec = MODULE.load(root, "preparation-qualify-denied.template.json")["spec"]
            del spec["templates"][0]["steps"][1]
            spec["templates"].pop()
            self.assertEqual(spec, wanted)
            original = MODULE.load(root, "preparation-qualify-barrier.workflow.json")
            parameters = MODULE.load(root, "upgrade-request-parameters.json")
            view = SCOPE.barrier_view(original, wanted, running=True)
            self.assertEqual(set(SCOPE.scopes(view, wanted, parameters, barrier=True)), {"migrate"})
            name = original["metadata"]["name"]
            mutations = (
                lambda w: w["status"]["storedWorkflowTemplateSpec"].update(
                    serviceAccountName="default"
                ),
                lambda w: w["status"]["nodes"][name + "-barrier"].update(type="Pod"),
                lambda w: w["status"]["nodes"][name + "-barrier"].update(boundaryID="other"),
                lambda w: w["status"]["nodes"][name + "-barrier"].update(
                    startedAt="2000-01-01T00:00:00Z"
                ),
                lambda w: w["status"]["nodes"][name + "-migrate"].update(phase="Failed"),
                lambda w: w["spec"].update(onExit="repair"),
                lambda w: w["status"]["nodes"][name + "-barrier-group"]["children"].append("extra"),
            )
            for mutate in mutations:
                value = deepcopy(original)
                mutate(value)
                with self.subTest(mutate=mutate), self.assertRaises(AssertionError):
                    SCOPE.barrier_view(value, wanted, running=True)
            later = deepcopy(original)
            later["status"]["nodes"]["unexpected"] = {
                "id": "unexpected",
                "displayName": "grant",
                "type": "Pod",
                "boundaryID": name,
                "phase": "Succeeded",
            }
            with self.assertRaises(AssertionError):
                SCOPE.scopes(
                    SCOPE.barrier_view(later, wanted, running=True),
                    wanted,
                    parameters,
                    barrier=True,
                )

    def test_exact_qualification_refusal_and_retention_gate_guarded_restoration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            preparation.populate(root, results)
            earlier = (root / "preparation-denied.role-original.json").read_bytes()
            run("qualify-role-prepare", root, results)
            narrow = MODULE.load(root, "preparation-qualify-denied.narrow-patch.json")
            self.assertEqual(
                [p["path"] for p in narrow],
                ["/metadata/uid", "/metadata/resourceVersion", "/rules", "/rules"],
            )
            self.assertEqual(narrow[-1]["value"], preparation.DENIAL.narrowed(MODULE.role_source()))
            log = root / "preparation-qualify-denied.worker.private"
            correct = log.read_bytes()
            log.write_text(correct.decode().replace("perf-platform", "foreign"))
            with self.assertRaises(AssertionError):
                run("qualify-role-restore", root, results)
            restore = root / "preparation-qualify-denied.restore-patch.json"
            self.assertFalse(restore.exists())
            log.write_bytes(correct)
            path = root / "preparation-qualify-denied.migrate.job.json"
            raw = path.read_bytes()
            value = json.loads(raw)
            value["metadata"]["uid"] = "replaced"
            path.write_text(json.dumps(value))
            with self.assertRaises(AssertionError):
                run("qualify-role-restore", root, results)
            self.assertFalse(restore.exists())
            path.write_bytes(raw)
            run("qualify-role-restore", root, results)
            restored = json.loads(restore.read_text())
            current = MODULE.load(root, "preparation-qualify-denied.role-current.json")
            self.assertEqual(restored[0]["value"], current["metadata"]["uid"])
            self.assertEqual(restored[1]["value"], current["metadata"]["resourceVersion"])
            self.assertEqual(restored[-1]["value"], MODULE.role_source()["rules"])
            self.assertEqual((root / "preparation-denied.role-original.json").read_bytes(), earlier)

    def test_final_gate_requires_completed_migration_and_exact_failed_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            preparation.populate(root, results)
            MODULE.verify(root, results, "complete")
            path = root / "preparation-qualify-denied.migrate.current-job.json"
            original = path.read_bytes()
            value = MODULE.load(root, path.name)
            value["metadata"]["uid"] = "replacement"
            path.write_text(json.dumps(value))
            with self.assertRaises(AssertionError):
                MODULE.verify(root, results, "final")
            self.assertFalse((results / "database-preparation-final.outcome.json").exists())
            path.write_bytes(original)
            reports = MODULE.verify(root, results, "final")
            self.assertEqual(len(reports), 5)
            self.assertEqual(sum(len(r["jobUIDs"]) for r in reports), 7)

    @unittest.skipUnless(BASH, "Bash required for real coordinator stop paths")
    def test_coordinator_never_resumes_or_restores_after_an_unproved_gate(self):
        assert BASH
        source = (preparation.FIXTURE / "database-preparation.sh").read_text()
        library = source[
            source.index("preparation_qualification_denial() {") : source.index(
                "\npreparation_retention() {"
            )
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for fail in (
                "create",
                "terminal",
                "workers",
                "qualify-barrier",
                "qualify-role-prepare",
                "qualify-role-narrowed",
                "deny-rights",
                "refusal",
                "qualify-role-restore",
                "restore-patch",
                "restore-rights",
                "qualify-role-restored",
                "pass",
            ):
                trace = root / (fail + ".trace")
                script = root / (fail + ".sh")
                script.write_text(
                    """set -euo pipefail
fixture=$PWD; results=$PWD; source_dir=$PWD; current_stage=outer
log() { printf '%s\\n' "$1" >> "$TRACE"; }
step() { log "$1"; [[ $FAIL != "$1" ]]; }
start_stage() { current_stage=$1; log "$1"; }
complete_stage() { log complete; }
k() { if [[ $* == 'create '* ]]; then step create >&2; elif [[ $* == *restore-patch* ]]; then step restore-patch >&2; fi; printf '{}'; }
a() { printf '{}'; }
jq() { if [[ $* == -er* ]]; then cat >/dev/null; printf workflow; elif [[ $* == *'any('* ]]; then [[ $FAIL != terminal ]]; else return 1; fi; }
qualification_workers() { step workers; printf '[]' > "$2"; }
preparation_jobs() { log jobs; }
preparation_snapshot() { log snapshot; }
uv() { step "${6}"; }
runtime_grant_access() {
  log "rights-$1"
  if [[ $current_stage == *denied-authority && $1 == true ]]; then step deny-rights; fi
  if [[ $current_stage == *denied-restoration && $1 == false ]]; then step restore-rights; fi
}
preparation_case() { step refusal; }
"""
                    + library
                    + "\npreparation_qualification_denial\nstep first\nstep recovery\n",
                    newline="\n",
                )
                result = subprocess.run(
                    [BASH, str(script)],
                    cwd=root,
                    env={**os.environ, "TRACE": trace.as_posix(), "FAIL": fail, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                lines = trace.read_text().splitlines()
                with self.subTest(fail=fail):
                    self.assertEqual(
                        result.returncode, 0 if fail == "pass" else 1, (result.stderr, lines)
                    )
                    self.assertEqual("first" in lines, fail == "pass")
                    self.assertEqual("recovery" in lines, fail == "pass")
                    if fail in {
                        "create",
                        "terminal",
                        "workers",
                        "qualify-barrier",
                        "qualify-role-prepare",
                        "qualify-role-narrowed",
                        "deny-rights",
                    }:
                        self.assertNotIn("refusal", lines)
                    if fail in {"refusal", "qualify-role-restore"}:
                        self.assertNotIn("restore-patch", lines)


if __name__ == "__main__":
    unittest.main()
