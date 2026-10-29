"""Offline owner-parent evidence, isolation and fail-stop orchestration checks."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import test_schema_migration_install as install
import yaml
from test_release_workflows_integration import BASH

FIXTURE = install.repeats.pause.FIXTURE
SPEC = importlib.util.spec_from_file_location(
    "runtime_grants_parent", FIXTURE / "runtime_grants_parent.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict(
    "sys.modules",
    {"schema_migration_install": install.MODULE, "schema_migration_parent": install.repeats.MODULE},
):
    SPEC.loader.exec_module(MODULE)


def populate(root, results):
    install.populate(root, results)

    def load(name):
        return json.loads((root / name).read_text())

    def save(name, value):
        (root / name).write_text(json.dumps(value))

    parameters = load("parent-parameters.json")
    templates = [
        yaml.safe_load((MODULE.parent.DIRECTORY / name).read_text()) for name in MODULE.SOURCES
    ]
    for obj in templates:
        obj["metadata"]["uid"] = obj["metadata"]["name"]
    config = yaml.safe_load((MODULE.parent.DIRECTORY / "runtime-grants-sql-v1.yaml").read_text())
    config["metadata"]["uid"] = "sql-uid"
    for label in ("first", "repeat"):
        for when in ("before", "after"):
            for suffix in ("objects", "applications", "pods", "pvs", "namespace", "roles"):
                save(
                    f"schema-install.grant-{label}-{when}.{suffix}.json",
                    load(f"schema-install.before.{suffix}.json"),
                )
            save(
                f"grant-{label}-{when}.schema.json",
                json.loads((results / "migration-image-first.schema.json").read_text()),
            )
            for suffix, value in (
                ("data.txt", "row-digests"),
                ("sentinel.txt", "123|migration-image-parity-v1"),
                ("acl.json", "reviewed-acls"),
            ):
                (root / f"grant-{label}-{when}.{suffix}").write_text(value)
        workflow = deepcopy(load("schema-install.workflow.json"))
        workflow["kind"] = "Workflow"
        workflow["metadata"]["uid"] = "grant-" + label
        workflow["spec"]["workflowTemplateRef"]["name"] = "perfeng-release-runtime-grants-v1"
        nodes = workflow["status"]["nodes"]
        nodes["grant"] = nodes.pop("migrate")
        nodes["grant"].update(
            id="grant",
            displayName="grant",
            templateRef={"name": "perfeng-runtime-grants-job-v1", "template": "run"},
        )
        nodes["migration-intent"].pop("templateName")
        nodes["migration-intent"]["templateRef"] = {
            "name": "perfeng-release-schema-migration-v1",
            "template": "migration-intent",
        }
        job_name = "perfeng-runtime-grants-grant-" + label
        next(
            p
            for p in nodes[workflow["metadata"]["name"]]["outputs"]["parameters"]
            if p["name"] == "job-name"
        )["value"] = job_name
        workers = load("schema-install.workers.json")
        for worker in workers:
            worker["account"] = "release-runtime-grants"
            if worker["nodeID"] == "migrate":
                worker["nodeID"] = "grant"
        manifest = templates[1]["spec"]["templates"][0]["resource"]["manifest"]
        for key, value in {
            "uid": workflow["metadata"]["uid"],
            **{"parameters." + k: v for k, v in parameters.items()},
        }.items():
            manifest = manifest.replace("{{workflow." + key + "}}", value)
        job = yaml.safe_load(manifest)
        job["metadata"].update(
            uid="job-grant-" + label,
            creationTimestamp=load("schema-install.job.json")["metadata"]["creationTimestamp"],
        )
        job["status"] = {"succeeded": 1, "conditions": [{"type": "Complete", "status": "True"}]}
        pods = load("schema-install.job-pods.json")
        pod = pods["items"][0]
        pod["spec"] = deepcopy(job["spec"]["template"]["spec"])
        pod["metadata"]["ownerReferences"] = [
            {"kind": "Job", "name": job_name, "uid": job["metadata"]["uid"], "controller": True}
        ]
        pod["status"]["containerStatuses"][0]["name"] = "grant"
        for suffix, value in (
            ("workflow", workflow),
            ("retained-workflow", workflow),
            ("workers", workers),
            ("job", job),
            ("pods", pods),
        ):
            save(f"grant-{label}.{suffix}.json", value)
    for label in ("first-before", "first-after", "repeat-before", "repeat-after", "retained"):
        save(f"grant-{label}.templates.json", {"items": templates})
        save(f"grant-{label}.sql.json", config)
        save(f"grant-{label}.intent.json", load("schema-install.intent-before.json"))
    save(
        "grant-retained.jobs.json",
        {"items": [load(f"grant-{label}.job.json") for label in ("first", "repeat")]},
    )


def run(mode, root, results):
    with patch("sys.argv", ["grant", mode, str(root), str(results)]):
        MODULE.main()


class RuntimeGrantsParentTests(unittest.TestCase):
    def test_complete_parents_and_retention_publish_only_bounded_verified_outcomes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for label in ("first", "repeat"):
                run("before-" + label, root, results)
                run("verify-" + label, root, results)
            self.assertFalse(list(results.glob("grant-parent-*")))
            run("retained", root, results)
            self.assertEqual(len(list(results.glob("grant-parent-*.outcome.json"))), 2)
            for path in results.glob("grant-parent-*.outcome.json"):
                self.assertEqual(
                    set(json.loads(path.read_text())),
                    {"decision", "workflowUID", "jobUID", "requestDigest"},
                )

    def test_refuse_wrong_authority_order_job_sql_and_retained_state_before_publication(self):
        cases = (
            ("grant-first.workers.json", lambda x: x[0].update(account="release-external-state")),
            (
                "grant-first.workflow.json",
                lambda x: x["status"]["nodes"]["grant"].update(
                    startedAt="2026-10-28T00:00:00+00:00"
                ),
            ),
            (
                "grant-first.workflow.json",
                lambda x: x["status"]["nodes"]["quiescence"].update(phase="Skipped"),
            ),
            ("grant-first.job.json", lambda x: x["spec"].update(backoffLimit=1)),
            (
                "grant-first.job.json",
                lambda x: x["spec"]["template"]["spec"]["containers"][0]["env"][0]["valueFrom"][
                    "secretKeyRef"
                ].update(key="runtime-url"),
            ),
            (
                "grant-first.pods.json",
                lambda x: x["items"][0]["spec"]["volumes"].append(
                    {"name": "admin", "secret": {"secretName": "postgres-admin"}}
                ),
            ),
            ("grant-first.pods.json", lambda x: x["items"][0]["status"].update(phase="Failed")),
            (
                "grant-first-after.templates.json",
                lambda x: x["items"][0]["metadata"].update(uid="replacement"),
            ),
            (
                "grant-first-after.sql.json",
                lambda x: x["data"].update({"runtime-grants.sql": "SELECT 1"}),
            ),
            (
                "schema-install.grant-first-after.objects.json",
                lambda x: x["items"][1]["data"].clear(),
            ),
            (
                "schema-install.grant-first-after.pvs.json",
                lambda x: x["items"][0]["metadata"].update(uid="replacement"),
            ),
            (
                "schema-install.grant-first-after.roles.json",
                lambda x: x.update(roles="changed-password"),
            ),
            (
                "schema-install.grant-first-after.applications.json",
                lambda x: x["items"][0]["spec"]["syncPolicy"]["automated"].update(enabled=True),
            ),
            ("grant-first-after.schema.json", lambda x: x["ledger"].pop()),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for name, mutate in cases:
                with self.subTest(name=name):
                    path = root / name
                    original = path.read_bytes()
                    value = json.loads(original)
                    mutate(value)
                    path.write_text(json.dumps(value))
                    with self.assertRaises((AssertionError, KeyError)):
                        run("verify-first", root, results)
                    self.assertFalse(list(results.glob("grant-parent-*")))
                    path.write_bytes(original)
            for suffix in ("data.txt", "sentinel.txt"):
                path = root / f"grant-first-after.{suffix}"
                original = path.read_bytes()
                path.write_text("changed")
                with self.assertRaises(AssertionError):
                    run("verify-first", root, results)
                path.write_bytes(original)

    def test_retention_refuses_lost_jobs_workflows_sources_and_changed_repeat_acls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for label in ("first", "repeat"):
                run("verify-" + label, root, results)
            for name, mutate in (
                ("grant-retained.jobs.json", lambda x: x["items"].pop()),
                (
                    "grant-retained.jobs.json",
                    lambda x: x["items"][0]["spec"].update(backoffLimit=1),
                ),
                (
                    "grant-first.retained-workflow.json",
                    lambda x: x["metadata"].update(uid="replacement"),
                ),
                ("grant-retained.sql.json", lambda x: x["metadata"].update(uid="replacement")),
            ):
                path = root / name
                original = path.read_bytes()
                value = json.loads(original)
                mutate(value)
                path.write_text(json.dumps(value))
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    run("retained", root, results)
                self.assertFalse(list(results.glob("grant-parent-*")))
                path.write_bytes(original)
            (root / "grant-repeat-after.acl.json").write_text("drift")
            with self.assertRaises(AssertionError):
                run("retained", root, results)

    @unittest.skipUnless(BASH, "Bash required for coordinator sequencing")
    def test_actual_coordinator_stops_at_each_unverified_boundary(self):
        assert BASH
        shell = (FIXTURE / "runtime-grants-parent.sh").read_text()
        function = shell[
            shell.index("runtime_grant_parent() {") : shell.index("runtime_grant_retention() {")
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for fail in ("pass", "before-first", "submit", "wait", "verify-first"):
                directory = root / fail
                directory.mkdir()
                script = directory / "case.sh"
                script.write_text(
                    """set -euo pipefail
fixture=$PWD; results=$PWD; source_dir=$PWD; KUBECONFIG=$PWD/kubeconfig; current_stage=outer
record() { echo "$*" >> trace; }
start_stage() { record start; }; complete_stage() { record complete; }
grant_snapshot() { record "snapshot $1"; }
a() { record "a $1"; if [[ $1 == submit && $FAIL == submit ]]; then return 1; fi; echo '{}'; }
k() { record k; echo '{}'; }
jq() { if [[ $* == *metadata.uid* ]]; then echo uid; else echo name; fi; }
uv() { record "$*"; for arg in "$@"; do [[ $arg != "$FAIL" ]] || return 1; done; }
timeout() { record wait; [[ $FAIL != wait ]]; }
"""
                    + function
                    + "\nruntime_grant_parent first\nrecord returned\n"
                )
                process = subprocess.run(
                    [str(BASH), str(script)],
                    cwd=directory,
                    env={**os.environ, "FAIL": fail},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                trace = (directory / "trace").read_text()
                self.assertEqual(process.returncode, 0 if fail == "pass" else 1, process.stderr)
                self.assertEqual("complete" in trace, fail == "pass")
                self.assertEqual("returned" in trace, fail == "pass")
                if fail == "before-first":
                    self.assertNotIn("a submit", trace)
                if fail == "wait":
                    self.assertNotIn("snapshot first-after", trace)

    @unittest.skipUnless(BASH, "Bash required for scenario routing")
    def test_only_initial_replaces_first_two_calls_and_preserves_admin_correction(self):
        assert BASH
        shell = (FIXTURE / "runtime-grants.sh").read_text()
        section = shell[
            shell.index("runtime_probe ungranted failure") : shell.index(
                'migration_snapshot "$results/runtime-grants-schema-after.json"'
            )
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "runtime-grants.sql").write_text("legacy grants")
            (root / "runtime-grants-parent.sh").write_text(
                'runtime_grant_parent() { echo "parent $1" >> trace; [[ $1 != ${FAIL:-pass} ]]; }\n'
            )
            for scenario in (
                "initial",
                "receipt-only",
                "response-loss",
                "pointer-response-loss",
                "receipt-create-conflict",
                "pointer-create-conflict",
            ):
                script = root / "case.sh"
                script.write_text(
                    """set -euo pipefail
source_dir=$PWD; results=$PWD
runtime_probe() { echo "probe $1 $2" >> trace; }
runtime_acl_snapshot() { :; }; cmp() { :; }
sql() { echo admin >> trace; }
"""
                    + section
                )
                process = subprocess.run(
                    [str(BASH), str(script)],
                    cwd=root,
                    env={**os.environ, "scenario": scenario},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                trace = (root / "trace").read_text().splitlines()
                self.assertEqual(trace.count("admin"), 2 if scenario == "initial" else 4)
                self.assertEqual(
                    [line for line in trace if line.startswith("parent")],
                    ["parent first", "parent repeat"] if scenario == "initial" else [],
                )
                self.assertLess(
                    trace.index("probe excess-delete failure"),
                    trace.index("probe corrected success"),
                )
                (root / "trace").unlink()
            for fail in ("first", "repeat"):
                process = subprocess.run(
                    [str(BASH), str(script)],
                    cwd=root,
                    env={**os.environ, "scenario": "initial", "FAIL": fail},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(process.returncode, 1, process.stderr)
                trace = (root / "trace").read_text().splitlines()
                self.assertNotIn("admin", trace)
                self.assertNotIn(f"probe {fail} success", trace)
                self.assertNotIn("probe excess-delete failure", trace)
                (root / "trace").unlink()


if __name__ == "__main__":
    unittest.main()
