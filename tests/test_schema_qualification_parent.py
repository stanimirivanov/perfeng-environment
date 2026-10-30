"""Offline evidence and fail-stop coordinator tests for read-only qualification."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import test_runtime_grants_adopted as adopted
import yaml
from test_release_workflows_integration import BASH

FIXTURE = adopted.grants.FIXTURE
SPEC = importlib.util.spec_from_file_location(
    "schema_qualification_parent", FIXTURE / "schema_qualification_parent.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict("sys.modules", {"runtime_grants_adopted": adopted.MODULE}):
    SPEC.loader.exec_module(MODULE)


def populate(root, results):
    adopted.populate(root, results)

    def load(name):
        return json.loads((root / name).read_text())

    def save(name, value):
        (root / name).write_text(json.dumps(value))

    parameters = load("upgrade-request-parameters.json")
    templates = [
        yaml.safe_load((adopted.grants.MODULE.parent.DIRECTORY / name).read_text())
        for name in MODULE.SOURCES
    ]
    for obj in templates:
        obj["metadata"]["uid"] = obj["metadata"]["name"]
    sql = yaml.safe_load(
        (adopted.grants.MODULE.parent.DIRECTORY / "schema-qualification-sql-v1.yaml").read_text()
    )
    sql["metadata"]["uid"] = "qualification-sql"
    for label in ("before", "first", "repeat", "retained", "final"):
        source_prefix = "grant-adopted-before."
        for path in root.glob(source_prefix + "*"):
            suffix = path.name.removeprefix(source_prefix)
            (root / ("qualification-" + label + "." + suffix)).write_bytes(path.read_bytes())
        save(f"qualification-{label}.templates.json", {"items": templates})
        save(f"qualification-{label}.sql.json", sql)
        save(f"qualification-{label}.jobs.json", {"items": []})
    jobs = []
    for index, label in enumerate(("first", "repeat"), 1):
        workflow = load("grant-adopted.workflow.json")
        old_name = workflow["metadata"]["name"]
        workflow["metadata"].update(
            name="qualify-" + label, uid=f"00000000-0000-0000-0000-{index:012d}"
        )
        workflow["spec"]["workflowTemplateRef"]["name"] = "perfeng-release-schema-qualification-v1"
        nodes = workflow["status"]["nodes"]
        nodes[workflow["metadata"]["name"]] = nodes.pop(old_name)
        nodes["qualify"] = nodes.pop("grant")
        nodes["qualify"].update(
            id="qualify",
            displayName="qualify",
            templateRef={"name": "perfeng-schema-qualification-job-v1", "template": "run"},
        )
        name = "perfeng-schema-qualify-" + workflow["metadata"]["uid"]
        for item in nodes[workflow["metadata"]["name"]]["outputs"]["parameters"]:
            if item["name"] == "job-name":
                item["value"] = name
        manifest = yaml.safe_load(
            (
                adopted.grants.MODULE.parent.DIRECTORY / "schema-qualification-job-v1.yaml"
            ).read_text()
        )["spec"]["templates"][0]["resource"]["manifest"]
        for key, value in {
            "uid": workflow["metadata"]["uid"],
            **{"parameters." + k: v for k, v in parameters.items()},
        }.items():
            manifest = manifest.replace("{{workflow." + key + "}}", value)
        job = yaml.safe_load(manifest)
        job["metadata"].update(
            uid="qualification-job-" + label,
            creationTimestamp=(
                datetime.fromisoformat(nodes["qualify"]["startedAt"]) + timedelta(seconds=1)
            ).isoformat(),
        )
        job["status"] = {"succeeded": 1, "conditions": [{"type": "Complete", "status": "True"}]}
        pods = load("grant-adopted.pods.json")
        pod = pods["items"][0]
        pod["kind"] = "Pod"
        pod["metadata"]["name"] = "qualification-pod-" + label
        pod["metadata"].update(
            uid="qualification-pod-" + label,
            ownerReferences=[
                {"kind": "Job", "name": name, "uid": job["metadata"]["uid"], "controller": True}
            ],
        )
        pod["spec"] = deepcopy(job["spec"]["template"]["spec"])
        pod["status"]["containerStatuses"][0]["name"] = "qualify"
        workers = [
            {
                "uid": label + "-" + n["id"],
                "nodeID": n["id"],
                "account": "release-runtime-grants",
                "exitCode": 0,
            }
            for n in nodes.values()
            if n["type"] == "Pod"
        ]
        for key, value in (
            ("workflow", workflow),
            ("workers", workers),
            ("job", job),
            ("job-pods", pods),
        ):
            save(f"qualification-{label}.{key}.json", value)
            save(f"qualification-{label}.retained-{key}.json", value)
        (root / f"qualification-{label}.worker.private").write_text(
            "Selected migration ledger and ownership qualified.\n"
        )
        jobs.append(job)
        save(f"qualification-{label}.jobs.json", {"items": deepcopy(jobs)})
    for label in ("retained", "final"):
        save(f"qualification-{label}.jobs.json", {"items": jobs})


class QualificationParentTests(unittest.TestCase):
    def test_first_repeat_and_fresh_retention(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for mode in ("before", "first", "repeat", "retained", "final"):
                MODULE.verify(root, results, mode)
            # Final retention permits the already approved recovered Deployment.
            path = root / "qualification-final.deployment.json"
            recovered = json.loads(path.read_text())
            recovered["spec"]["replicas"] = 1
            path.write_text(json.dumps(recovered))
            MODULE.verify(root, results, "final")
            reports = json.loads((results / "schema-qualification-final.outcome.json").read_text())
            self.assertEqual(len(reports), 2)
            self.assertEqual(len({r["jobUID"] for r in reports}), 2)

    def test_tampered_state_execution_and_retention_block_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            MODULE.verify(root, results, "repeat")
            cases = (
                (
                    "first.workflow.json",
                    lambda v: v["status"]["nodes"]["admission"].update(
                        finishedAt="2099-01-01T00:00:00+00:00"
                    ),
                    "first",
                ),
                ("first.workers.json", lambda v: v[0].update(account="default"), "first"),
                (
                    "first.job.json",
                    lambda v: v["spec"]["template"]["spec"]["containers"][0].update(
                        command=["sh", "-c", "write SQL"]
                    ),
                    "first",
                ),
                (
                    "first.job-pods.json",
                    lambda v: v["items"][0]["status"]["containerStatuses"][0]["state"][
                        "terminated"
                    ].update(exitCode=1),
                    "first",
                ),
                (
                    "first.sql.json",
                    lambda v: v["data"].update({"schema-qualification.sql": "SELECT 1;"}),
                    "first",
                ),
                (
                    "first.templates.json",
                    lambda v: v["items"][0]["spec"].update(serviceAccountName="default"),
                    "first",
                ),
                (
                    "first.ledger.json",
                    lambda v: v["items"][0]["metadata"].update(uid="replaced"),
                    "first",
                ),
                (
                    "first.records.json",
                    lambda v: v[0]["data"].update(requestDigest="sha256:" + "0" * 64),
                    "first",
                ),
                (
                    "repeat.retained-job.json",
                    lambda v: v["metadata"].update(uid="replacement"),
                    "retained",
                ),
                (
                    "repeat.retained-workflow.json",
                    lambda v: v["status"].update(phase="Failed"),
                    "retained",
                ),
                ("repeat.retained-workers.json", lambda v: v[0].update(exitCode=1), "retained"),
                ("retained.jobs.json", lambda v: v["items"].pop(), "retained"),
                ("final.roles.json", lambda v: v.update(unexpected="changed"), "final"),
                ("final.secrets.json", lambda v: v.update(unexpected="changed"), "final"),
                ("final.sql.json", lambda v: v["metadata"].update(uid="replacement"), "final"),
            )
            for suffix, mutate, mode in cases:
                path = root / ("qualification-" + suffix)
                raw = path.read_bytes()
                value = json.loads(raw)
                mutate(value)
                path.write_text(json.dumps(value))
                with self.subTest(suffix=suffix), self.assertRaises(AssertionError):
                    MODULE.verify(root, results, mode)
                path.write_bytes(raw)
            for suffix in (
                "acl.json",
                "data.txt",
                "postgres.sentinel.txt",
                "seaweedfs.bytes",
                "worker.private",
            ):
                path = root / ("qualification-first." + suffix)
                raw = path.read_bytes()
                path.write_bytes(b"changed")
                with self.subTest(suffix=suffix), self.assertRaises(AssertionError):
                    MODULE.verify(root, results, "first")
                path.write_bytes(raw)
            self.assertFalse((results / "schema-qualification-final.outcome.json").exists())

    @unittest.skipUnless(BASH, "Git Bash/bash required")
    def test_actual_coordinator_stops_before_next_case_on_failure(self):
        assert BASH
        source = (FIXTURE / "schema-qualification-parent.sh").read_text()
        suite = source[
            source.index("qualification_suite() {") : source.index("\nqualification_suite\n")
        ]
        case = source[
            source.index("qualification_case() {") : source.index("\nqualification_retention() {")
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for fail in ("setup", "wait", "evidence", "retained", "pass"):
                trace = root / (fail + ".trace")
                script = root / (fail + ".sh")
                script.write_text(
                    """set -euo pipefail
fixture=$PWD; results=$PWD; source_dir=$PWD; KUBECONFIG=$PWD/kubeconfig; current_stage=initial
log() { printf '%s\\n' "$1" >> "$TRACE"; }
start_stage() { log "$1"; }
complete_stage() { log complete; }
k() { printf '{}'; }
a() { printf '{}'; }
jq() { cat >/dev/null; printf workflow; }
argo() { [[ $FAIL != wait ]]; }
timeout() { shift; "$@"; }
schema_parent_ledger_access() { log authority; }
qualification_snapshot() { log "snapshot-$1"; }
qualification_workers() { printf '[]' > "$2"; }
uv() { local mode=${6}; log "verify-$mode"; [[ ! ( $FAIL == setup && $mode == before ) && ! ( $FAIL == evidence && $mode == first ) ]]; }
qualification_retention() { log retained; [[ $FAIL != retained ]]; }
"""
                    + case
                    + suite
                    + "\nqualification_suite\nlog recovery\n",
                    newline="\n",
                )
                process = subprocess.run(
                    [BASH, str(script)],
                    cwd=root,
                    env={**os.environ, "TRACE": trace.as_posix(), "FAIL": fail, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                lines = trace.read_text().splitlines()
                with self.subTest(fail=fail):
                    self.assertEqual(process.returncode, 0 if fail == "pass" else 1, process.stderr)
                    self.assertEqual("recovery" in lines, fail == "pass")
                    if fail in {"setup", "wait", "evidence"}:
                        self.assertNotIn("schema-qualification-repeat", lines)

    def test_ci_wiring_preserves_boundaries_and_checks_before_recovery_and_final(self):
        grants = (FIXTURE / "runtime-grants-adopted.sh").read_text()
        self.assertLess(
            grants.index(
                "schema_parent_ledger_access release-runtime-grants",
                grants.index("adopted_grant_suite()"),
            ),
            grants.index('source "$source_dir/schema-qualification-parent.sh"'),
        )
        self.assertLess(
            grants.index('source "$source_dir/schema-qualification-parent.sh"'),
            grants.index("runtime_grant_parent adopted-null"),
        )
        pause = (FIXTURE / "maintenance-pause.sh").read_text()
        self.assertLess(
            pause.index("\nqualification_retention\n"),
            pause.index("start_stage maintenance-recovery"),
        )
        final = (FIXTURE / "preflight-parent.sh").read_text()
        self.assertIn("runtime_grant_retention complete\n  qualification_retention final", final)
        shell = (FIXTURE / "schema-qualification-parent.sh").read_text()
        self.assertIn("${scenario:-} == initial", shell)
        self.assertNotIn("trap ", shell)
        self.assertNotIn("delete ", shell)
        self.assertNotIn("patch ", shell)


if __name__ == "__main__":
    unittest.main()
