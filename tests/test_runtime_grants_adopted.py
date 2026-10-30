"""Adopted request, paused state, native grant/probe and recovery-gate evidence."""

import base64
import hashlib
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

import test_runtime_grants_parent as grants
import test_schema_migration_parent as schema
import yaml
from test_release_workflows_integration import BASH

SPEC = importlib.util.spec_from_file_location(
    "runtime_grants_adopted", grants.FIXTURE / "runtime_grants_adopted.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict("sys.modules", {"runtime_grants_parent": grants.MODULE}):
    SPEC.loader.exec_module(MODULE)


def populate(root, results):
    grants.populate(root, results)
    schema.populate(root, results)

    def load(name):
        return json.loads((root / name).read_text())

    def save(name, value):
        (root / name).write_text(json.dumps(value))

    parameters = load("upgrade-request-parameters.json")
    for prefix in ("maintenance-before", "schema-parent-baseline"):
        observed = MODULE.state(root, prefix)
        receipt = next(o for o in observed["records"] if o.get("immutable"))
        image = json.loads(base64.b64decode(receipt["binaryData"]["receipt.json"]))["image"]
        template = {"spec": {"containers": [{"name": "server", "image": image}]}}
        observed["deployment"]["spec"]["template"] = template
        for obj in observed["server"]:
            if obj["kind"] == "Deployment":
                obj["spec"]["template"] = deepcopy(template)
        for obj in observed["workloads"]:
            if obj["kind"] == "Deployment" and obj["name"] == "control-plane":
                obj["spec"]["template"] = deepcopy(template)
        for key, value in observed.items():
            save(prefix + "." + ("server-pods" if key == "serverPod" else key) + ".json", value)
    baseline = MODULE.state(root, "schema-parent-baseline")
    for label in ("before", "after", "observed", "null-before", "null-after"):
        for key, value in baseline.items():
            save(
                f"grant-adopted-{label}."
                + ("server-pods" if key == "serverPod" else key)
                + ".json",
                value,
            )
        for suffix in ("schema.json", "roles.json", "data.txt"):
            (root / f"grant-adopted-{label}.{suffix}").write_bytes(
                (root / f"schema-parent-baseline.{suffix}").read_bytes()
            )
        (root / f"grant-adopted-{label}.acl.json").write_bytes(
            (root / "grant-first-after.acl.json").read_bytes()
        )
        for suffix in ("templates", "sql", "intent"):
            save(f"grant-adopted-{label}.{suffix}.json", load(f"grant-first-before.{suffix}.json"))
        save(f"grant-adopted-{label}.jobs.json", load("grant-retained.jobs.json"))
    for suffix in MODULE.PERSISTENT:
        for prefix in (
            "schema-parent-baseline",
            "grant-adopted-before",
            "grant-adopted-after",
            "grant-adopted-observed",
            "grant-adopted-null-before",
            "grant-adopted-null-after",
        ):
            (root / f"{prefix}.{suffix}").write_text("stable SQL/S3 fixture " + suffix)

    workflow = load("grant-repeat.workflow.json")
    old_name = workflow["metadata"]["name"]
    workflow["metadata"].update(name="grant-adopted-ci", uid="00000000-0000-0000-0000-000000000099")
    workflow["status"]["nodes"]["grant-adopted-ci"] = workflow["status"]["nodes"].pop(old_name)
    workflow["spec"]["arguments"]["parameters"] = [
        {"name": k, "value": v} for k, v in parameters.items()
    ]
    workflow["spec"]["workflowTemplateRef"]["name"] = "perfeng-release-runtime-grants-v2"
    schema_nodes = schema.evidence("first", retained_admission=True)[1]["status"]["nodes"]
    for stage in (
        "candidate",
        "pointer",
        "receipt",
        "accepted-bytes",
        "receipt-stable",
        "admission",
    ):
        workflow["status"]["nodes"][stage] = deepcopy(schema_nodes[stage])
        workflow["status"]["nodes"][stage]["templateRef"] = {
            "name": "perfeng-release-schema-migration-v2",
            "template": stage,
        }
    workflow["status"]["nodes"]["quiescence"].update(
        startedAt=schema_nodes["quiescence"]["startedAt"],
        finishedAt=schema_nodes["quiescence"]["finishedAt"],
    )
    workflow["status"]["nodes"]["grant"].update(
        startedAt=schema_nodes["migrate"]["startedAt"],
        finishedAt=schema_nodes["migrate"]["finishedAt"],
    )
    for name in ("preflight", "quiescence"):
        value = workflow["status"]["nodes"][name]["outputs"]["parameters"][0]
        record = json.loads(value["value"])
        record.update(
            requestDigest=parameters["request-sha256"],
            requestRevision=parameters["request-revision"],
            selectionRevision=parameters["selection-revision"],
        )
        if name == "preflight":
            record["requestPath"] = parameters["request-path"]
        value["value"] = json.dumps(record)
    checkpoint = workflow["status"]["nodes"]["quiescence"]["outputs"]["parameters"][0]["value"]
    name = "perfeng-runtime-grants-" + workflow["metadata"]["uid"]
    workflow["status"]["nodes"]["grant-adopted-ci"]["outputs"]["parameters"] = [
        {"name": "checkpoint", "value": checkpoint},
        {"name": "job-name", "value": name},
    ]
    manifest = yaml.safe_load(
        (grants.MODULE.parent.DIRECTORY / "runtime-grants-job-v1.yaml").read_text()
    )["spec"]["templates"][0]["resource"]["manifest"]
    for key, value in {
        "uid": workflow["metadata"]["uid"],
        **{"parameters." + k: v for k, v in parameters.items()},
    }.items():
        manifest = manifest.replace("{{workflow." + key + "}}", value)
    job = yaml.safe_load(manifest)
    job["metadata"].update(
        uid="adopted-job",
        creationTimestamp=(
            datetime.fromisoformat(schema_nodes["migrate"]["startedAt"]) + timedelta(seconds=1)
        ).isoformat(),
    )
    job["status"] = {"succeeded": 1, "conditions": [{"type": "Complete", "status": "True"}]}
    pods = load("grant-repeat.pods.json")
    pods["items"][0]["spec"] = deepcopy(job["spec"]["template"]["spec"])
    pods["items"][0]["metadata"]["ownerReferences"] = [
        {"kind": "Job", "name": name, "uid": "adopted-job", "controller": True}
    ]
    for suffix, value in (
        ("workflow", workflow),
        ("current-workflow", workflow),
        ("retained-workflow", workflow),
        (
            "workers",
            [
                {
                    "uid": "worker-" + n["id"],
                    "nodeID": n["id"],
                    "account": "release-runtime-grants",
                    "exitCode": 0,
                }
                for n in workflow["status"]["nodes"].values()
                if n["type"] == "Pod"
            ],
        ),
        ("job", job),
        ("current-job", job),
        ("pods", pods),
    ):
        save(f"grant-adopted.{suffix}.json", value)
    probe = yaml.safe_load((grants.FIXTURE / "runtime-grants-workflow.yaml").read_text())
    probe["metadata"].update(name="runtime-grants-ci-adopted", uid="probe-workflow")
    probe["status"] = {"phase": "Succeeded"}
    probe_job = yaml.safe_load(
        probe["spec"]["templates"][0]["resource"]["manifest"].replace(
            "{{workflow.name}}", probe["metadata"]["name"]
        )
    )
    probe_job["metadata"].update(name="runtime-probe-adopted", uid="probe-job")
    probe_job["status"] = {"succeeded": 1, "conditions": [{"type": "Complete", "status": "True"}]}
    for suffix, value in (
        ("workflow", probe),
        ("job", probe_job),
        (
            "worker",
            [
                {
                    "uid": "probe-pod",
                    "containers": [{"name": "probe", "exitCode": 0, "waitingReason": None}],
                }
            ],
        ),
    ):
        (results / f"runtime-grants-adopted.{suffix}.json").write_text(json.dumps(value))
    save("grant-adopted.probe-retained-workflow.json", probe)
    save("grant-adopted.probe-retained-job.json", probe_job)
    (root / "grant-adopted.probe.private").write_text("runtime privilege policy verified\n")
    null = schema.evidence("current-null")
    null_workflow = null[1]
    null_workflow["kind"] = "Workflow"
    old_name = null_workflow["metadata"]["name"]
    null_workflow["metadata"].update(
        name="grant-adopted-null-ci", uid="00000000-0000-0000-0000-000000000098"
    )
    null_workflow["spec"]["workflowTemplateRef"]["name"] = "perfeng-release-runtime-grants-v2"
    null_nodes = null_workflow["status"]["nodes"]
    null_nodes["grant-adopted-null-ci"] = null_nodes.pop(old_name)
    null_nodes["candidate"]["templateRef"] = {
        "name": "perfeng-release-schema-migration-v2",
        "template": "candidate",
    }
    for worker in null[2]:
        worker["account"] = "release-runtime-grants"
    for suffix, value in (
        ("workflow", null_workflow),
        ("current-workflow", null_workflow),
        ("retained-workflow", null_workflow),
        ("workers", null[2]),
        ("job", None),
        ("pods", {"items": []}),
    ):
        save(f"grant-adopted-null.{suffix}.json", value)
    for label in ("before", "after", "observed", "retained", "null-before", "null-after"):
        templates = load("grant-first-before.templates.json")
        for filename in ("release-runtime-grants-v2.yaml", "release-schema-migration-v2.yaml"):
            obj = yaml.safe_load((grants.MODULE.parent.DIRECTORY / filename).read_text())
            obj["metadata"]["uid"] = obj["metadata"]["name"]
            templates["items"].append(obj)
        save(f"grant-adopted-{label}.templates.json", templates)
        for suffix in ("sql", "intent"):
            save(f"grant-adopted-{label}.{suffix}.json", load(f"grant-first-before.{suffix}.json"))
        authority = list(
            yaml.safe_load_all(
                (
                    schema.pause.ROOT
                    / "gitops/release-workflows-bootstrap/runtime-grants-ledger-access.yaml"
                ).read_text()
            )
        )
        for obj in authority:
            obj["metadata"]["uid"] = "grants-ledger-" + obj["kind"]
        save(f"grant-adopted-{label}.ledger.json", {"items": authority})
    for suffix in ("templates", "sql", "intent"):
        save(f"grant-retained-v2.{suffix}.json", load(f"grant-adopted-retained.{suffix}.json"))
    # The successful adopted fixture starts only after the real refusal checker.
    run("verify-null", root, results)


def run(mode, root, results):
    with patch("sys.argv", ["adopted", mode, str(root), str(results)]):
        MODULE.main()


class RuntimeGrantsAdoptedTests(unittest.TestCase):
    def test_null_refusal_requires_exact_native_failure_no_later_work_and_unchanged_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            run("before-null", root, results)
            for code in (None, 0, 1, 64):
                path = root / "grant-adopted-null.workers.json"
                raw = path.read_bytes()
                value = json.loads(raw)
                next(w for w in value if w["nodeID"] == "candidate")["exitCode"] = code
                path.write_text(json.dumps(value))
                with self.subTest(code=code), self.assertRaises(AssertionError):
                    run("verify-null", root, results)
                path.write_bytes(raw)
            path = root / "grant-adopted-null.workflow.json"
            raw = path.read_bytes()
            value = json.loads(raw)
            value["status"]["nodes"]["candidate"]["phase"] = "Error"
            path.write_text(json.dumps(value))
            run("verify-null", root, results)
            path.write_bytes(raw)
            for stage in ("pointer", "quiescence", "grant"):
                value = json.loads(raw)
                value["status"]["nodes"][stage] = {
                    "id": stage,
                    "displayName": stage,
                    "type": "Pod",
                    "phase": "Succeeded",
                }
                path.write_text(json.dumps(value))
                with self.assertRaises(AssertionError):
                    run("verify-null", root, results)
                path.write_bytes(raw)
            cases = (
                ("grant-adopted-null.job.json", {"metadata": {"uid": "unexpected"}}),
                ("grant-adopted-null.pods.json", {"items": [{"metadata": {"uid": "unexpected"}}]}),
                ("grant-adopted-null-after.roles.json", {"roles": "changed-password"}),
                ("grant-adopted-null-after.jobs.json", {"items": []}),
            )
            for name, value in cases:
                path = root / name
                raw = path.read_bytes()
                path.write_text(json.dumps(value))
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    run("verify-null", root, results)
                path.write_bytes(raw)

    def test_v2_execution_and_fresh_retention_cannot_reuse_missing_admission_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            proof = root / "grant-adopted-null.verified.json"
            raw_proof = proof.read_bytes()
            proof.unlink()
            with self.assertRaises(FileNotFoundError):
                run("before", root, results)
            proof.write_bytes(raw_proof)
            path = root / "grant-adopted.workflow.json"
            raw = path.read_bytes()
            for mutate in (
                lambda w: w["spec"]["workflowTemplateRef"].update(
                    name="perfeng-release-runtime-grants-v1"
                ),
                lambda w: w["status"]["nodes"].pop("accepted-bytes"),
                lambda w: w["status"]["nodes"]["admission"].update(
                    startedAt=w["status"]["nodes"]["preflight"]["startedAt"]
                ),
            ):
                value = json.loads(raw)
                mutate(value)
                path.write_text(json.dumps(value))
                with self.assertRaises(AssertionError):
                    run("verify", root, results)
            path.write_bytes(raw)
            run("verify", root, results)
            run("complete", root, results)
            cases = (
                (
                    "grant-adopted.current-workflow.json",
                    lambda w: w["status"]["nodes"].pop("admission"),
                ),
                (
                    "grant-adopted-null.current-workflow.json",
                    lambda w: w["status"]["nodes"].pop("candidate"),
                ),
                (
                    "grant-adopted-retained.ledger.json",
                    lambda w: w["items"][0]["rules"][0]["verbs"].append("patch"),
                ),
            )
            for name, mutate in cases:
                path = root / name
                raw = path.read_bytes()
                value = json.loads(raw)
                mutate(value)
                path.write_text(json.dumps(value))
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    run("retained", root, results)
                path.write_bytes(raw)

    def test_final_retention_requires_adopted_execution_and_bootstrap_stays_strict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for label in ("denied", "contention", "rollback", "first", "repeat", "corrected"):
                grants.run("verify-" + label, root, results)
            run("verify", root, results)
            run("complete", root, results)
            with self.assertRaises(AssertionError):
                grants.run("retained-adopted", root, results)
            path = root / "grant-retained.jobs.json"
            value = json.loads(path.read_text())
            value["items"].append(json.loads((root / "grant-adopted.job.json").read_text()))
            path.write_text(json.dumps(value))
            with self.assertRaises(AssertionError):
                grants.run("retained", root, results)
            grants.run("retained-adopted", root, results)
            self.assertEqual(len(list(results.glob("grant-parent-*.outcome.json"))), 8)

    def test_candidate_must_name_the_accepted_current_release_before_submission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            path = root / "upgrade-request-request.json"
            value = json.loads(path.read_text())
            value["promotion"]["expectedCurrent"] = "sha256:" + "0" * 64
            raw = json.dumps(value).encode()
            path.write_bytes(raw)
            path = root / "upgrade-request-parameters.json"
            parameters = json.loads(path.read_text())
            parameters["request-sha256"] = "sha256:" + hashlib.sha256(raw).hexdigest()
            path.write_text(json.dumps(parameters))
            with self.assertRaises(AssertionError):
                run("before", root, results)

    def test_real_adopted_request_parent_and_runtime_probe_preserve_paused_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for mode in ("before", "verify", "complete", "retained"):
                run(mode, root, results)
            self.assertFalse((results / "grant-parent-adopted.outcome.json").exists())

    def test_recovery_refuses_wrong_pointer_receipt_pause_or_persistent_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            cases = (
                (
                    "grant-adopted-before.records.json",
                    lambda x: next(o for o in x if "data" in o)["data"].update(
                        receiptUID="replacement"
                    ),
                ),
                (
                    "grant-adopted-before.records.json",
                    lambda x: next(o for o in x if "data" in o)["data"].update(
                        requestDigest="sha256:" + "0" * 64
                    ),
                ),
                ("grant-adopted-before.deployment.json", lambda x: x["spec"].update(replicas=1)),
                ("grant-adopted-after.roles.json", lambda x: x.update(roles="changed-password")),
                (
                    "grant-adopted-before.templates.json",
                    lambda x: x["items"][0]["spec"].update(serviceAccountName="foreign"),
                ),
            )
            for name, mutate in cases:
                path = root / name
                raw = path.read_bytes()
                value = json.loads(raw)
                mutate(value)
                path.write_text(json.dumps(value))
                with self.subTest(name=name), self.assertRaises((AssertionError, KeyError)):
                    run("verify", root, results)
                path.write_bytes(raw)
            for suffix in ("seaweedfs.bytes", "data.txt", "acl.json"):
                path = root / ("grant-adopted-after." + suffix)
                raw = path.read_bytes()
                path.write_text("changed")
                with self.assertRaises(AssertionError):
                    run("verify", root, results)
                path.write_bytes(raw)

    def test_probe_or_retained_identity_failure_blocks_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            run("verify", root, results)
            job = root / "grant-adopted.probe-retained-job.json"
            value = json.loads(job.read_text())
            value["metadata"]["uid"] = "replacement"
            job.write_text(json.dumps(value))
            with self.assertRaises(AssertionError):
                run("retained", root, results)
            probe = results / "runtime-grants-adopted.worker.json"
            value = json.loads(probe.read_text())
            value[0]["containers"][0]["exitCode"] = 3
            probe.write_text(json.dumps(value))
            with self.assertRaises(AssertionError):
                run("complete", root, results)

    @unittest.skipUnless(BASH, "Bash required for actual pre-recovery sequencing")
    def test_adopted_suite_cannot_finish_after_any_unverified_boundary(self):
        assert BASH
        text = (grants.FIXTURE / "runtime-grants-adopted.sh").read_text()
        library = text[text.index("adopted_grant_suite() {") : text.rindex("\nadopted_grant_suite")]
        setup = """set -euo pipefail
fixture=$PWD; results=$PWD; source_dir=$PWD; current_stage=outer
step() { echo "$1" >> trace; [[ $FAIL != "$1" ]]; }
start_stage() { :; }; complete_stage() { step complete; }
runtime_grant_parent() { if [[ $1 == adopted-null ]]; then step null-parent; else step parent; fi; }; runtime_probe() { step probe; }
k() { step setup; }; schema_parent_ledger_access() { step access; }
adopted_grant_snapshot() { step snapshot; }; uv() { step verified; }
adopted_grant_retention() { step retained; }
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for failure in (
                "pass",
                "setup",
                "access",
                "qualification",
                "preparation",
                "null-parent",
                "parent",
                "probe",
                "snapshot",
                "verified",
                "retained",
            ):
                case = root / failure
                case.mkdir()
                (case / "schema-qualification-parent.sh").write_text(
                    "step qualification\n", newline="\n"
                )
                (case / "database-preparation.sh").write_text("step preparation\n", newline="\n")
                (case / "runtime-probe.log").write_text("runtime privilege policy verified\n")
                script = case / "case.sh"
                script.write_text(
                    setup + library + "\nadopted_grant_suite\necho recovery >> trace\n"
                )
                result = subprocess.run(
                    [str(BASH), str(script)],
                    cwd=case,
                    env={**os.environ, "FAIL": failure, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                trace = (case / "trace").read_text()
                self.assertEqual(result.returncode, 0 if failure == "pass" else 1, result.stderr)
                self.assertEqual("recovery" in trace, failure == "pass")

    @unittest.skipUnless(BASH, "Bash required for native dispatch and strict final retention")
    def test_native_dispatch_uses_candidate_and_complete_retention_requires_probe(self):
        assert BASH
        text = (grants.FIXTURE / "runtime-grants-parent.sh").read_text()
        native = text[
            text.index("runtime_grant_parent() {") : text.index("runtime_grant_retention() {")
        ]
        retained = text[
            text.index("runtime_grant_retention() {") : text.index("runtime_grant_access() {")
        ]
        setup = """set -euo pipefail
fixture=$PWD; results=$PWD; source_dir=$PWD; KUBECONFIG=$PWD/kubeconfig; current_stage=outer
step() { echo "$1" >> trace; [[ $FAIL != "$1" ]]; }
start_stage() { :; }; complete_stage() { step complete; }
adopted_grant_snapshot() { step "snapshot-$1"; }
grant_snapshot() { step wrong-bootstrap; return 1; }
a() { echo "a $*" >> trace; echo '{}'; }; k() { echo '{}'; }; jq() { echo native-id; }
uv() { step "${5##*/}-$6"; }; timeout() { step wait || return 1; return "${WAIT_EXIT:-0}"; }
preparation_inventory() { step inventory; }
grant_sources() { :; }; runtime_grant_access() { :; }
runtime_grant_contention_retention() { :; }; runtime_grant_rollback_retention() { :; }
adopted_grant_retention() { step probe-retained; }
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = [
                (
                    "native-" + failure,
                    native,
                    "runtime_grant_parent adopted",
                    failure,
                    0 if failure == "pass" else 1,
                )
                for failure in (
                    "pass",
                    "runtime_grants_adopted.py-before",
                    "wait",
                    "runtime_grants_adopted.py-verify",
                )
            ]
            cases += [
                ("null-pass", native, "runtime_grant_parent adopted-null", "pass", 0),
                (
                    "null-before",
                    native,
                    "runtime_grant_parent adopted-null",
                    "runtime_grants_adopted.py-before-null",
                    1,
                ),
                (
                    "null-unverified",
                    native,
                    "runtime_grant_parent adopted-null",
                    "runtime_grants_adopted.py-verify-null",
                    1,
                ),
                ("bootstrap", retained, "runtime_grant_retention bootstrap", "pass", 0),
                ("complete", retained, "runtime_grant_retention complete", "pass", 0),
                ("inventory-failed", retained, "runtime_grant_retention complete", "inventory", 1),
                ("probe-failed", retained, "runtime_grant_retention complete", "probe-retained", 1),
                ("unknown", retained, "runtime_grant_retention unknown", "pass", 2),
            ]
            for label, library, command, failure, code in cases:
                case = root / label
                case.mkdir()
                script = case / "case.sh"
                script.write_text(setup + library + "\n" + command + "\necho returned >> trace\n")
                result = subprocess.run(
                    [str(BASH), str(script)],
                    cwd=case,
                    env={
                        **os.environ,
                        "FAIL": failure,
                        "BASH_ENV": "",
                        "WAIT_EXIT": "1" if label.startswith("null-") else "0",
                    },
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                trace = (case / "trace").read_text() if (case / "trace").exists() else ""
                with self.subTest(label=label):
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertEqual("returned" in trace, code == 0)
                    if label == "native-pass":
                        self.assertIn("upgrade-request-parameters.json", trace)
                        self.assertIn("workflowtemplate/perfeng-release-runtime-grants-v2", trace)
                        self.assertNotIn("wrong-bootstrap", trace)
                    if label == "null-pass":
                        self.assertIn("parent-parameters.json", trace)
                        self.assertIn("workflowtemplate/perfeng-release-runtime-grants-v2", trace)
                        self.assertNotIn("upgrade-request-parameters.json", trace)
                    if label == "native-runtime_grants_adopted.py-before":
                        self.assertNotIn("a submit", trace)
                    if label == "complete":
                        self.assertIn("runtime_grants_parent.py-retained-adopted", trace)
                    if label == "bootstrap":
                        self.assertNotIn("probe-retained", trace)
                    if label == "probe-failed":
                        self.assertNotIn("runtime_grants_parent.py-retained-adopted", trace)


if __name__ == "__main__":
    unittest.main()
