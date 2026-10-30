"""Prove complete-parent repeat evidence and fail-stop recovery ordering offline."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import test_maintenance_pause_integration as pause
import test_migration_image_integration as image
import yaml
from test_release_workflows_integration import BASH

with patch.dict(
    "sys.modules",
    {"maintenance_pause_evidence": pause.MODULE, "migration_image_evidence": image.EVIDENCE},
):
    spec = importlib.util.spec_from_file_location(
        "schema_migration_parent", pause.FIXTURE / "schema_migration_parent.py"
    )
    assert spec and spec.loader
    MODULE = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(MODULE)


def evidence(label, *, retained_admission=False):
    if label == "current-null":
        return admitted(evidence("first"), null_current=True)
    original, _, parameters, preflight, accepted_raw, _, before, after = pause.evidence()
    image = json.loads((pause.ROOT / "deployments/local-control-plane.json").read_text())[
        "controlPlane"
    ]["image"]
    for state in (before, after):
        for spec in (
            state["deployment"]["spec"],
            state["server"][0]["spec"],
            state["workloads"][0]["spec"],
        ):
            spec["template"] = {"spec": {"containers": [{"name": "control-plane", "image": image}]}}
    request = json.loads(accepted_raw)
    request["promotion"] = {
        **request["promotion"],
        "expectedCurrent": "sha256:" + hashlib.sha256(accepted_raw).hexdigest(),
        "migration": {"mode": "none"}
        if label == "none"
        else {"mode": "forward-only", "id": "0018"},
    }
    parameters["request-path"] = "deployments/release-requests/" + (
        "ci-schema-migration-none-v1.json" if label == "none" else "ci-upgrade-request-v1.json"
    )
    raw = (json.dumps(request, indent=2) + "\n").encode()
    parameters["request-sha256"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    preflight.update(
        requestDigest=parameters["request-sha256"], requestPath=parameters["request-path"]
    )
    checkpoint = pause.READINESS.outputs(original["status"]["nodes"]["observe"])["result"]
    checkpoint["requestDigest"] = parameters["request-sha256"]
    uid = "00000000-0000-0000-0000-" + f"{MODULE.LABELS.index(label) + 1:012d}"
    base = datetime(2026, 10, 28, tzinfo=UTC)

    def when(seconds):
        return (base + timedelta(seconds=seconds)).isoformat()

    nodes: dict[str, Any] = {}
    for name, start, finish in (
        ("validate-schema", 0, 1),
        ("cluster-uid", 2, 3),
        ("verify-identities", 4, 5),
        ("task-selection", 9, 10),
        ("migration-intent", 11, 12),
        ("quiescence", 13, 14),
        ("migrate", 15, 18),
    ):
        if label == "none" and name in {"quiescence", "migrate"}:
            continue
        nodes[name] = {
            "id": name,
            "displayName": name,
            "type": "Pod",
            "phase": "Failed" if label == "none" and name == "migration-intent" else "Succeeded",
            "startedAt": when(start),
            "finishedAt": when(finish),
        }
    for name, template in (
        ("task-selection", "perfeng-release-task-selection-v3"),
        ("quiescence", "perfeng-verify-release-quiescence-v3"),
        ("migrate", "perfeng-schema-migration-job-v1"),
    ):
        if name in nodes:
            nodes[name]["templateRef"] = {"name": template, "template": "run"}
    nodes["migration-intent"]["templateName"] = "migration-intent"
    nodes["preflight"] = {
        "id": "preflight",
        "displayName": "preflight",
        "type": "Steps",
        "phase": "Succeeded",
        "startedAt": when(0),
        "finishedAt": when(8),
        "templateRef": {"name": "perfeng-release-preflight-v3", "template": "preflight"},
        "outputs": {"parameters": [{"name": "result", "value": json.dumps(preflight)}]},
    }
    name = "schema-parent-" + label
    nodes[name] = {
        "id": name,
        "displayName": name,
        "type": "Steps",
        "phase": "Failed" if label == "none" else "Succeeded",
    }
    job, pods = None, {"items": []}
    if label != "none":
        nodes["quiescence"]["outputs"] = {
            "parameters": [{"name": "result", "value": json.dumps(checkpoint)}]
        }
        job_name = "perfeng-schema-" + uid
        nodes[name]["outputs"] = {
            "parameters": [
                {"name": "checkpoint", "value": json.dumps(checkpoint)},
                {"name": "job-name", "value": job_name},
            ]
        }
        manifest = yaml.safe_load((MODULE.DIRECTORY / "schema-migration-job-v1.yaml").read_text())[
            "spec"
        ]["templates"][0]["resource"]["manifest"]
        for key, value in {
            "uid": uid,
            **{"parameters." + k: v for k, v in parameters.items()},
        }.items():
            manifest = manifest.replace("{{workflow." + key + "}}", value)
        job = yaml.safe_load(manifest)
        job["metadata"].update(uid="job-" + label, creationTimestamp=when(16))
        job["spec"].update(parallelism=1, completions=1)
        job["status"] = {"succeeded": 1, "conditions": [{"type": "Complete", "status": "True"}]}
        pod_spec = deepcopy(job["spec"]["template"]["spec"])
        pod_spec["tolerations"] += [
            {
                "key": "node.kubernetes.io/" + key,
                "operator": "Exists",
                "effect": "NoExecute",
                "tolerationSeconds": 300,
            }
            for key in ("not-ready", "unreachable")
        ]
        pod_spec["tolerations"].append(
            {
                "key": "node.kubernetes.io/memory-pressure",
                "operator": "Exists",
                "effect": "NoSchedule",
            }
        )
        pods["items"] = [
            {
                "metadata": {
                    "uid": "pod-" + label,
                    "namespace": "perf-platform",
                    "ownerReferences": [
                        {
                            "kind": "Job",
                            "uid": job["metadata"]["uid"],
                            "name": job_name,
                            "controller": True,
                        }
                    ],
                },
                "spec": pod_spec,
                "status": {
                    "phase": "Succeeded",
                    "containerStatuses": [
                        {"name": "migrate", "state": {"terminated": {"exitCode": 0}}}
                    ],
                },
            }
        ]
    workflow = {
        "metadata": {"name": name, "namespace": "argo-workflows", "uid": uid},
        "spec": {
            "workflowTemplateRef": {"name": "perfeng-release-schema-migration-v1"},
            "arguments": {"parameters": [{"name": k, "value": v} for k, v in parameters.items()]},
        },
        "status": {"phase": "Failed" if label == "none" else "Succeeded", "nodes": nodes},
    }
    workers = [
        {
            "uid": "worker-" + n["id"],
            "nodeID": n["id"],
            "account": "release-schema-migration",
            "exitCode": 2 if n["phase"] == "Failed" else 0,
        }
        for n in nodes.values()
        if n["type"] == "Pod"
    ]
    if label == "denied":
        workflow["status"]["phase"] = nodes[name]["phase"] = "Failed"
        nodes[name].pop("outputs")
        nodes["migrate"]["phase"] = "Failed"
        next(w for w in workers if w["nodeID"] == "migrate")["exitCode"] = 1
        job, pods = None, {"items": []}
    result = label, workflow, workers, parameters, raw, accepted_raw, before, after, job, pods
    return admitted(result) if retained_admission else result


def admitted(args, *, null_current=False):
    label, workflow, _, parameters, raw, accepted, before, after, job, pods = deepcopy(args)
    nodes = workflow["status"]["nodes"]
    if null_current:
        label = "current-null"
        old_name = workflow["metadata"]["name"]
        name = "schema-parent-" + label
        workflow["metadata"].update(name=name, uid="00000000-0000-0000-0000-000000000099")
        nodes[name] = nodes.pop(old_name)
        nodes[name].update(id=name, displayName=name, phase="Failed")
        nodes[name].pop("outputs")
        workflow["status"]["phase"] = "Failed"
        parameters = pause.evidence()[2]
        raw = accepted
        workflow["spec"]["arguments"]["parameters"] = [
            {"name": k, "value": v} for k, v in parameters.items()
        ]
        result = pause.READINESS.outputs(nodes["preflight"])["result"]
        result.update(
            requestDigest=parameters["request-sha256"], requestPath=parameters["request-path"]
        )
        nodes["preflight"]["outputs"]["parameters"][0]["value"] = json.dumps(result)
        nodes.pop("quiescence")
        nodes.pop("migrate")
        job, pods = None, {"items": []}
    workflow["spec"]["workflowTemplateRef"]["name"] = "perfeng-release-schema-migration-v2"
    nodes["migration-intent"]["templateRef"] = {
        "name": "perfeng-release-schema-migration-v1",
        "template": "migration-intent",
    }
    base = datetime(2026, 10, 28, tzinfo=UTC)

    def when(seconds):
        return (base + timedelta(seconds=seconds)).isoformat()

    names = (
        ("candidate",)
        if null_current
        else (
            "candidate",
            "pointer",
            "receipt",
            "accepted-bytes",
            "quiescence",
            "receipt-stable",
            "admission",
            "migrate",
        )
    )
    for index, name in enumerate(names):
        node = nodes.setdefault(
            name, {"id": name, "displayName": name, "type": "Pod", "templateName": name}
        )
        node.update(
            phase="Failed" if null_current else "Succeeded",
            startedAt=when(13 + index * 2),
            finishedAt=when(14 + index * 2),
        )
    if not null_current:
        assert job is not None
        job["metadata"]["creationTimestamp"] = when(28)
        nodes["migrate"]["finishedAt"] = when(30)
        nodes["candidate"]["outputs"] = {
            "parameters": [{"name": "request", "value": raw.decode().removesuffix("\n")}]
        }
        pointer = next(
            o for o in after["records"] if o["metadata"]["name"] == "perfeng-local-current"
        )
        receipt = next(
            o for o in after["records"] if o["metadata"]["name"] == pointer["data"]["receiptName"]
        )
        for name, value in (
            ("pointer", pointer),
            ("receipt", receipt),
            ("receipt-stable", receipt),
            ("admission", {"status": "retained-baseline-verified"}),
        ):
            nodes[name]["outputs"] = {
                "parameters": [{"name": "observation", "value": json.dumps(value)}]
            }
    workers = [
        {
            "uid": "worker-" + n["id"],
            "nodeID": n["id"],
            "account": "release-schema-migration",
            "exitCode": 2 if null_current and n["displayName"] == "candidate" else 0,
        }
        for n in nodes.values()
        if n["type"] == "Pod"
    ]
    return label, workflow, workers, parameters, raw, accepted, before, after, job, pods


def denied_log(workflow):
    name = "perfeng-schema-" + workflow["metadata"]["uid"]
    return (
        f'Error from server (Forbidden): error when creating "/tmp/manifest.yaml": jobs.batch "{name}" is forbidden: '
        'User "system:serviceaccount:argo-workflows:release-schema-migration" cannot create resource "jobs" '
        'in API group "batch" in the namespace "perf-platform"\n'
    )


def populate(directory, results):
    def save(name, value):
        (directory / name).write_text(json.dumps(value))

    jobs, workflows = [], []
    source = image.MigrationImageIntegrationTests()
    source.setUp()
    schema = source.schema()
    save("schema-parent-baseline.jobs.json", {"items": []})
    save("parent-parameters.json", pause.evidence()[2])
    save("schema-parent-baseline.schema.json", schema)
    save("schema-parent-baseline.roles.json", {"roles": "unchanged"})
    (directory / "schema-parent-baseline.data.txt").write_text("private SQL rows")
    for label in MODULE.LABELS:
        _, workflow, workers, parameters, raw, accepted, before, after, job, pods = evidence(
            label, retained_admission=label in {"current-null", "first", "repeat"}
        )
        prefix = "schema-parent-" + label
        if label == "denied":
            (directory / (prefix + ".denied.log")).write_text(denied_log(workflow))
        for stem, value in (
            ("maintenance-before", before),
            (prefix, after),
            ("schema-parent-baseline", after),
        ):
            for key, obj in value.items():
                save(stem + "." + ("server-pods" if key == "serverPod" else key) + ".json", obj)
        save(prefix + ".jobs-before.json", {"items": deepcopy(jobs)})
        if job:
            jobs.append(job)
        for suffix, obj in (
            ("workflow", workflow),
            ("workers", workers),
            ("job", job),
            ("job-pods", pods),
            ("jobs-after", {"items": deepcopy(jobs)}),
            ("schema", schema),
            ("roles", {"roles": "unchanged"}),
        ):
            save(prefix + "." + suffix + ".json", obj)
        (directory / (prefix + ".data.txt")).write_text("private SQL rows")
        for suffix in MODULE.PERSISTENT:
            for stem in (prefix, "schema-parent-baseline"):
                (directory / f"{stem}.{suffix}").write_text("private persistent state")
        key = (
            "migration-none"
            if label == "none"
            else "parent"
            if label == "current-null"
            else "upgrade-request"
        )
        save(key + "-parameters.json", parameters)
        (directory / (key + "-request.json")).write_bytes(raw)
        (directory / "parent-request.json").write_bytes(accepted)
        save("request-ref.json", {"commit": parameters["request-revision"]})
        workflows.append(workflow)
    save("schema-parent.retained-jobs.json", {"items": jobs})
    save("schema-parent.retained-workflows.json", {"items": workflows})
    for label in ("before", "after"):
        objects = [
            yaml.safe_load((MODULE.DIRECTORY / name).read_text())
            for name in (*MODULE.SOURCES, "release-schema-migration-v2.yaml")
        ]
        for obj in objects:
            obj["metadata"]["uid"] = obj["metadata"]["name"]
        save(f"schema-parent.templates-{label}.json", {"items": objects})
        intent = yaml.safe_load((MODULE.DIRECTORY / "schema-migration-intent-v1.yaml").read_text())
        intent["metadata"]["uid"] = "intent-policy"
        save(f"schema-parent.intent-{label}.json", intent)
        authority = list(
            yaml.safe_load_all(
                (
                    pause.ROOT
                    / "gitops/release-workflows-bootstrap/schema-migration-ledger-access.yaml"
                ).read_text()
            )
        )
        for obj in authority:
            obj["metadata"]["uid"] = "ledger-" + obj["kind"]
        save(f"schema-parent.ledger-{label}.json", {"items": authority})


class SchemaMigrationParentTests(unittest.TestCase):
    @unittest.skipUnless(BASH and shutil.which("jq"), "Bash/jq required for actual shell replay")
    def test_actual_case_selects_v2_and_null_current_never_starts_job_watch(self):
        assert BASH
        text = (pause.FIXTURE / "schema-migration-parent.sh").read_text()
        library = text[
            text.index("schema_parent_case() {") : text.index("schema_parent_access() {")
        ]
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; results=$1; source_dir=$1; label=$3; failure=$4
KUBECONFIG="$fixture/kubeconfig"
step() { echo "$1" >> "$fixture/trace"; [[ $failure != "$1" ]]; }
start_stage() { :; }; complete_stage() { :; }
a() { if [[ $1 == submit ]]; then step "submit $*" >&2; fi; echo '{"metadata":{"name":"ci-case","uid":"case-uid"}}'; }
k() { if [[ $* != *'get job '* ]]; then echo '{"items":[]}'; fi; }
timeout() { if [[ $1 == --kill-after=5s ]]; then step "watch-$3"; else shift; "$@"; fi; }
argo() { step wait; if [[ $label == current-null && $failure != unexpected-success ]]; then return 1; fi; }
schema_parent_snapshot() { step snapshot; }
uv() { step "verify-$6"; }
source "$2"
schema_parent_case "$label"
echo grants-and-recovery >> "$fixture/trace"
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "case.sh").write_text(library, newline="\n")
            (root / "run.sh").write_text(setup, newline="\n")
            cases = (
                ("first", "pass"),
                ("repeat", "pass"),
                ("current-null", "pass"),
                ("current-null", "verify-current-null"),
                ("current-null", "unexpected-success"),
            )
            for index, (label, failure) in enumerate(cases):
                fixture = root / str(index)
                fixture.mkdir()
                process = subprocess.run(
                    [
                        BASH,
                        (root / "run.sh").as_posix(),
                        fixture.as_posix(),
                        (root / "case.sh").as_posix(),
                        label,
                        failure,
                    ],
                    env={**os.environ, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                trace = (fixture / "trace").read_text().splitlines()
                with self.subTest(label=label, failure=failure):
                    self.assertEqual(
                        process.returncode, 0 if failure == "pass" else 1, process.stderr
                    )
                    submitted = next(line for line in trace if line.startswith("submit "))
                    self.assertIn("workflowtemplate/perfeng-release-schema-migration-v2", submitted)
                    self.assertIn(
                        "parent-parameters.json"
                        if label == "current-null"
                        else "upgrade-request-parameters.json",
                        submitted,
                    )
                    self.assertEqual(
                        any(line.startswith("watch-") for line in trace), label != "current-null"
                    )
                    self.assertEqual("grants-and-recovery" in trace, failure == "pass")

    @unittest.skipUnless(BASH and shutil.which("jq"), "Bash/jq required for actual shell replay")
    def test_ledger_access_checks_exact_identity_and_rejects_inspection_errors(self):
        assert BASH
        text = (pause.FIXTURE / "schema-migration-parent.sh").read_text()
        library = text[
            text.index("schema_parent_ledger_access() {") : text.index("schema_parent_denial() {")
        ]
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; result=$3
k() { echo "$*" >> "$fixture/trace"; if [[ $result == error ]]; then echo 'private vendor output' >&2; return 1; else echo "$result"; if [[ $result == no ]]; then return 1; fi; fi; }
sleep() { SECONDS=$((SECONDS + 31)); }
source "$2"
schema_parent_ledger_access
echo ready >> "$fixture/trace"
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "access.sh").write_text(library, newline="\n")
            (root / "run.sh").write_text(setup, newline="\n")
            for answer in ("yes", "no", "error", "unexpected"):
                fixture = root / answer
                fixture.mkdir()
                (fixture / "parent-parameters.json").write_text(
                    json.dumps({"request-sha256": "sha256:" + "a" * 64})
                )
                process = subprocess.run(
                    [
                        BASH,
                        (root / "run.sh").as_posix(),
                        fixture.as_posix(),
                        (root / "access.sh").as_posix(),
                        answer,
                    ],
                    env={**os.environ, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                trace = (fixture / "trace").read_text()
                self.assertEqual(process.returncode, 0 if answer == "yes" else 1)
                self.assertIn(
                    "--as system:serviceaccount:argo-workflows:release-schema-migration", trace
                )
                self.assertIn("--as-group system:authenticated", trace)
                self.assertIn(
                    "auth can-i get configmaps/perfeng-local-current -n perfeng-release-ledger",
                    trace,
                )
                if answer == "yes":
                    self.assertIn(
                        "auth can-i get configmaps/receipt." + "a" * 32 + "." + "a" * 32, trace
                    )
                self.assertNotIn("private vendor output", process.stdout + process.stderr)

    def test_v2_repeats_require_actual_admission_outputs_and_order(self):
        for label in ("first", "repeat"):
            args = evidence(label, retained_admission=True)
            MODULE.verify(*args, retained_admission=True)
        for change in (
            lambda a: a[1]["spec"]["workflowTemplateRef"].update(
                name="perfeng-release-schema-migration-v1"
            ),
            lambda a: a[1]["status"]["nodes"].pop("accepted-bytes"),
            lambda a: a[1]["status"]["nodes"]["pointer"].update(phase="Skipped"),
            lambda a: a[1]["status"]["nodes"]["receipt-stable"].update(
                startedAt="2026-10-28T00:00:10+00:00"
            ),
            lambda a: a[1]["status"]["nodes"]["admission"]["outputs"]["parameters"][0].update(
                value='{"status":"other"}'
            ),
            lambda a: a[1]["status"]["nodes"]["candidate"]["outputs"]["parameters"][0].update(
                value="{}"
            ),
            lambda a: a[1]["status"]["nodes"]["receipt"]["outputs"]["parameters"][0].update(
                value='{"metadata":{"name":"missing"}}'
            ),
        ):
            args = list(evidence("first", retained_admission=True))
            change(args)
            with self.assertRaises((AssertionError, KeyError)):
                MODULE.verify(*args, retained_admission=True)

    def test_null_current_refuses_at_candidate_before_any_ledger_or_job_node(self):
        args = evidence("current-null")
        MODULE.verify(*args, retained_admission=True)
        output_error = deepcopy(args)
        output_error[1]["status"]["nodes"]["candidate"]["phase"] = "Error"
        MODULE.verify(*output_error, retained_admission=True)
        for code in (None, 0, 1, 64):
            bad = deepcopy(args)
            next(w for w in bad[2] if w["nodeID"] == "candidate")["exitCode"] = code
            with self.assertRaises(AssertionError):
                MODULE.verify(*bad, retained_admission=True)
        for name in ("pointer", "receipt", "quiescence", "migrate"):
            bad = deepcopy(args)
            bad[1]["status"]["nodes"][name] = {
                "id": name,
                "displayName": name,
                "type": "Pod",
                "phase": "Succeeded",
            }
            with self.assertRaises(AssertionError):
                MODULE.verify(*bad, retained_admission=True)
        bad = deepcopy(args)
        bad[1]["status"]["nodes"][bad[1]["metadata"]["name"]]["outputs"] = {
            "parameters": [{"name": "job-name", "value": "unexpected"}]
        }
        with self.assertRaises(AssertionError):
            MODULE.verify(*bad, retained_admission=True)

    def test_v2_retention_requires_sources_authority_and_persistent_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            cases = (
                ("schema-parent.templates-after.json", lambda x: x["items"].pop()),
                (
                    "schema-parent.ledger-after.json",
                    lambda x: x["items"][0]["rules"][0]["verbs"].append("create"),
                ),
                (
                    "schema-parent.ledger-after.json",
                    lambda x: x["items"][1]["metadata"].update(uid="replacement"),
                ),
                ("schema-parent.retained-workflows.json", lambda x: x["items"].pop(2)),
                ("schema-parent.retained-jobs.json", lambda x: x["items"].pop()),
            )
            report = results / "schema-parent-complete.outcome.json"
            for name, mutate in cases:
                path = root / name
                original = path.read_bytes()
                value = json.loads(original)
                mutate(value)
                path.write_text(json.dumps(value))
                with (
                    patch("sys.argv", ["evidence", "complete", str(root), str(results)]),
                    self.assertRaises((AssertionError, KeyError)),
                ):
                    MODULE.main()
                self.assertFalse(report.exists())
                path.write_bytes(original)
            for suffix in ("postgres.acl.json", "seaweedfs.bytes", "postgres.storage.json"):
                path = root / ("schema-parent-repeat." + suffix)
                original = path.read_bytes()
                path.write_bytes(b"drift")
                with (
                    patch("sys.argv", ["evidence", "complete", str(root), str(results)]),
                    self.assertRaises(AssertionError),
                ):
                    MODULE.main()
                self.assertFalse(report.exists())
                path.write_bytes(original)
            with patch("sys.argv", ["evidence", "complete", str(root), str(results)]):
                MODULE.main()
            original_report = report.read_bytes()
            retained = root / "schema-parent.retained-workflows.json"
            value = json.loads(retained.read_text())
            first = next(
                w for w in value["items"] if w["metadata"]["name"] == "schema-parent-first"
            )
            first["status"]["nodes"].pop("admission")
            retained.write_text(json.dumps(value))
            with (
                patch("sys.argv", ["evidence", "final", str(root), str(results)]),
                self.assertRaises(AssertionError),
            ):
                MODULE.main()
            self.assertFalse((results / "schema-parent-final.outcome.json").exists())
            self.assertEqual(report.read_bytes(), original_report)

    @unittest.skipUnless(BASH, "Bash required")
    def test_suite_stops_before_grants_and_recovery_at_each_unverified_boundary(self):
        assert BASH
        text = (pause.FIXTURE / "schema-migration-parent.sh").read_text()
        library = text[
            text.index("schema_parent_suite() {") : text.index("schema_parent_retention() {")
        ]
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; results=$1; source_dir=$1; failure=$3; current_stage=paused
step() { echo "$1" >> "$fixture/trace"; [[ $failure != "$1" ]]; }
start_stage() { :; }; complete_stage() { :; }
k() { if [[ $1 == apply ]]; then step "install-${*: -1}"; else echo '{"items":[]}'; fi; }
schema_parent_snapshot() { step snapshot; }
schema_parent_sources() { step sources-and-authority; }
schema_parent_denial() { step denied; }
schema_parent_case() { step "case-$1"; }
schema_parent_retention() { step retained; }
uv() { step "verify-$6"; }
source "$2"
schema_parent_suite
echo grants-and-recovery >> "$fixture/trace"
"""
        failures = (
            "pass",
            "install-gitops/release-workflows-bootstrap/schema-migration-ledger-access.yaml",
            "snapshot",
            "sources-and-authority",
            "verify-baseline",
            "case-none",
            "denied",
            "case-current-null",
            "case-first",
            "case-repeat",
            "retained",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "suite.sh").write_text(library, newline="\n")
            (root / "run.sh").write_text(setup, newline="\n")
            for index, failure in enumerate(failures):
                fixture = root / str(index)
                fixture.mkdir()
                process = subprocess.run(
                    [
                        BASH,
                        (root / "run.sh").as_posix(),
                        fixture.as_posix(),
                        (root / "suite.sh").as_posix(),
                        failure,
                    ],
                    env={**os.environ, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                trace = (fixture / "trace").read_text().splitlines()
                with self.subTest(failure=failure):
                    self.assertEqual(
                        process.returncode, 0 if failure == "pass" else 1, process.stderr
                    )
                    if failure == "pass":
                        self.assertEqual(trace[-1], "grants-and-recovery")
                        self.assertLess(trace.index("case-current-null"), trace.index("case-first"))
                        self.assertLess(trace.index("case-repeat"), trace.index("retained"))
                    else:
                        self.assertNotIn("grants-and-recovery", trace)
                        self.assertEqual(trace[-1], failure)

    def test_refusals_and_repeats_preserve_install_and_both_failed_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            installed = deepcopy(evidence("first")[8])
            installed["metadata"].update(uid="install-job", name="perfeng-schema-install")
            failed = deepcopy(installed)
            failed["metadata"].update(uid="contention-job", name="perfeng-schema-contention")
            failed["status"] = {
                "failed": 1,
                "conditions": [
                    {"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}
                ],
            }
            (root / "schema-contention.job.json").write_text(json.dumps(failed))
            rollback = deepcopy(failed)
            rollback["metadata"].update(uid="rollback-job", name="perfeng-schema-rollback")
            (root / "schema-rollback.job.json").write_text(json.dumps(rollback))
            (root / "schema-parent-baseline.jobs.json").write_text(
                json.dumps({"items": [installed, failed, rollback]})
            )
            files = ["schema-parent.retained-jobs.json"] + [
                f"schema-parent-{label}.jobs-{when}.json"
                for label in MODULE.LABELS
                for when in ("before", "after")
            ]
            for name in files:
                path = root / name
                value = json.loads(path.read_text())
                value["items"][:0] = [deepcopy(installed), deepcopy(failed), deepcopy(rollback)]
                path.write_text(json.dumps(value))
            with patch("sys.argv", ["evidence", "complete", str(root), str(results)]):
                MODULE.main()
            report = results / "schema-parent-complete.outcome.json"
            self.assertTrue(report.exists())
            report.unlink()
            path = root / "schema-parent-denied.jobs-after.json"
            value = json.loads(path.read_text())
            value["items"][0]["spec"]["backoffLimit"] = 1
            path.write_text(json.dumps(value))
            with (
                patch("sys.argv", ["evidence", "complete", str(root), str(results)]),
                self.assertRaises(AssertionError),
            ):
                MODULE.main()
            self.assertFalse(report.exists())

    def test_denial_requires_exact_native_failure_and_successful_prefix(self):
        args = evidence("denied")
        log = denied_log(args[1])
        MODULE.verify(*args, log)
        for wrong in (
            "",
            log.replace("cannot create", "cannot get"),
            log.replace("release-schema-migration", "release-external-state"),
            log.replace('"perf-platform"', '"default"'),
            log.replace('"batch"', '"apps"'),
            log.replace("jobs.batch", "pods"),
            log.replace("perfeng-schema-", "foreign-"),
            log.replace("Error from server (Forbidden):", "connection refused:"),
            log.replace('"perf-platform"', '"default"') + '\nin the namespace "perf-platform"',
            log.replace("cannot create", "cannot get")
            + '\ncannot create resource "jobs" in API group "batch"',
        ):
            with self.subTest(log=wrong), self.assertRaises(AssertionError):
                MODULE.verify(*args, wrong)
        for mutate in (
            lambda a: a[1]["status"]["nodes"]["quiescence"].update(phase="Failed"),
            lambda a: a[2][-1].update(exitCode=0),
            lambda a: a[2][-1].update(exitCode=None),
            lambda a: a[1]["spec"].update(shutdown="Stop"),
            lambda a: a[1]["status"]["nodes"]["schema-parent-denied"].update(
                outputs={"parameters": [{"name": "job-name", "value": "unexpected"}]}
            ),
            lambda a: a[9]["items"].append({"metadata": {"uid": "unexpected"}}),
            lambda a: a[7]["secrets"].clear(),
        ):
            bad = list(evidence("denied"))
            mutate(bad)
            with self.assertRaises((AssertionError, KeyError)):
                MODULE.verify(*bad, log)
        bad = list(evidence("denied"))
        bad[8] = evidence("first")[8]
        with self.assertRaises(AssertionError):
            MODULE.verify(*bad, log)

    def test_role_changes_remove_only_create_and_guard_uid_version_and_rules(self):
        spec = importlib.util.spec_from_file_location(
            "schema_migration_denial", pause.FIXTURE / "schema_migration_denial.py"
        )
        assert spec and spec.loader
        role = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(role)
        original = role.source()
        original["metadata"].update(uid="role-uid", resourceVersion="10")
        narrowed = deepcopy(original)
        narrowed["rules"] = role.narrowed()
        narrowed["metadata"]["resourceVersion"] = "11"
        self.assertEqual(original["rules"][:-1], narrowed["rules"][:-1])
        self.assertEqual(narrowed["rules"][-1]["verbs"], ["get", "watch"])
        for current, restore in ((original, False), (narrowed, True)):
            operations = role.patch(original, current, restore)
            self.assertEqual(
                [p["path"] for p in operations],
                ["/metadata/uid", "/metadata/resourceVersion", "/rules", "/rules"],
            )
            self.assertEqual([p["op"] for p in operations], ["test", "test", "test", "replace"])
            self.assertEqual(operations[1]["value"], current["metadata"]["resourceVersion"])
        for change in (
            lambda r: r["metadata"].update(uid="replacement"),
            lambda r: r["metadata"].update(resourceVersion=""),
            lambda r: r["metadata"].update(namespace="default"),
            lambda r: r["metadata"].update(labels={"foreign": "yes"}),
            lambda r: r["metadata"].update(deletionTimestamp="now"),
            lambda r: r["rules"][-1]["verbs"].append("delete"),
            lambda r: r["rules"][0]["verbs"].clear(),
        ):
            bad = deepcopy(narrowed)
            change(bad)
            with self.assertRaises(AssertionError):
                role.patch(original, bad, True)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, obj in (
                ("original", original),
                ("narrowed", narrowed),
                ("current", narrowed),
                ("restored", original),
                ("final", original),
            ):
                (root / f"schema-parent-denied.role-{label}.json").write_text(json.dumps(obj))
            for mode in ("prepare", "narrowed", "restore", "restored", "final"):
                with patch("sys.argv", ["role", mode, str(root)]):
                    role.main()
            restoration = json.loads((root / "schema-parent-denied.restore-patch.json").read_text())
            self.assertEqual(restoration[-1]["value"], original["rules"])
            self.assertEqual(restoration[1]["value"], narrowed["metadata"]["resourceVersion"])

    def test_real_shapes_accept_refusal_and_both_repeat_runs_with_pod_defaults(self):
        for label in MODULE.LABELS:
            args = evidence(label)
            report = MODULE.verify(
                *args,
                denied_log(args[1]) if label == "denied" else "",
                retained_admission=label == "current-null",
            )
            self.assertEqual(
                report["decision"],
                "schema-parent-intent-refused"
                if label == "none"
                else "schema-parent-job-create-refused"
                if label == "denied"
                else "schema-parent-null-current-refused"
                if label == "current-null"
                else "schema-parent-repeat-verified",
            )

    def test_wrong_identity_permissions_payload_order_or_paused_state_cannot_qualify(self):
        for mutate in (
            lambda a: a[2][0].update(account="release-external-state"),
            lambda a: a[1]["status"]["nodes"]["quiescence"].update(phase="Skipped"),
            lambda a: a[8]["metadata"].update(creationTimestamp="2026-10-27T00:00:00Z"),
            lambda a: a[8]["spec"]["template"]["spec"]["containers"][0]["env"][0]["valueFrom"][
                "secretKeyRef"
            ].update(key="runtime-url"),
            lambda a: a[9]["items"][0]["spec"].update(hostNetwork=True),
            lambda a: a[9]["items"][0]["spec"]["containers"][0].update(args=["unexpected"]),
            lambda a: a[9]["items"][0]["spec"]["containers"][0]["securityContext"].update(
                privileged=True
            ),
            lambda a: a[9]["items"][0]["spec"]["containers"][0]["securityContext"][
                "capabilities"
            ].update(add=["SYS_ADMIN"]),
            lambda a: a[9]["items"][0]["spec"]["tolerations"].append(
                {"key": "foreign", "operator": "Exists"}
            ),
            lambda a: a[7]["records"][0]["metadata"].update(uid="foreign"),
            lambda a: a[7]["deployment"]["spec"].update(replicas=1),
        ):
            bad = list(evidence("first"))
            mutate(bad)
            with self.assertRaises(AssertionError):
                MODULE.verify(*bad)
        for code in (0, 1, 64, None):
            bad = list(evidence("none"))
            next(w for w in bad[2] if w["nodeID"] == "migration-intent")["exitCode"] = code
            with self.assertRaises(AssertionError):
                MODULE.verify(*bad)

    def test_cli_rechecks_ledger_data_and_job_retention_before_publishing_suite(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            results = directory / "results"
            results.mkdir()
            populate(directory, results)
            argv = ["evidence", "complete", str(directory), str(results)]
            with patch("sys.argv", argv):
                MODULE.main()
            report = results / "schema-parent-complete.outcome.json"
            self.assertEqual(
                json.loads(report.read_text())["decision"], "schema-parent-repeat-suite-verified"
            )
            self.assertNotIn("private", report.read_text())
            report.unlink()
            for suffix in ("schema.json", "data.txt", "roles.json", "retained-jobs.json"):
                path = directory / (
                    "schema-parent." + suffix
                    if suffix == "retained-jobs.json"
                    else "schema-parent-repeat." + suffix
                )
                original = path.read_bytes()
                if suffix == "data.txt":
                    path.write_text("changed SQL row")
                elif suffix == "schema.json":
                    obj = json.loads(original)
                    obj["ledger"][0]["appliedAt"] = "changed timestamp"
                    path.write_text(json.dumps(obj))
                else:
                    path.write_text('{"items":[]}')
                with patch("sys.argv", argv), self.assertRaises(AssertionError):
                    MODULE.main()
                self.assertFalse(report.exists())
                path.write_bytes(original)

    def test_installed_definitions_and_policy_must_match_published_sources(self):
        objects = [yaml.safe_load((MODULE.DIRECTORY / name).read_text()) for name in MODULE.SOURCES]
        for obj in objects:
            obj["metadata"]["uid"] = obj["metadata"]["name"]
        intent = yaml.safe_load((MODULE.DIRECTORY / "schema-migration-intent-v1.yaml").read_text())
        intent["metadata"]["uid"] = "intent-policy"
        self.assertEqual(len(MODULE.installed({"items": objects}, intent)), 6)
        intent["data"]["schema-migration-intent-v1.schema.json"] = "{}"
        with self.assertRaises(AssertionError):
            MODULE.installed({"items": objects}, intent)

    def test_pre_job_diagnostics_publish_only_fixed_signals_and_stage_phases(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            results = directory / "results"
            results.mkdir()
            populate(directory, results)
            (directory / "schema-parent-first.job.json").write_text("null")
            (directory / "schema-parent-first.failures.log").write_text(
                "permission denied https://user:private-token@host.invalid at instance location"
            )
            (directory / "schema-parent-first.workflow-pods.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "status": {
                                    "containerStatuses": [
                                        {
                                            "state": {
                                                "waiting": {
                                                    "reason": "ImagePullBackOff",
                                                    "message": "private-token",
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                )
            )
            with patch("sys.argv", ["evidence", "diagnose-first", str(directory), str(results)]):
                MODULE.main()
            text = (results / "schema-parent-first.startup.json").read_text()
            value = json.loads(text)
            self.assertEqual(value["failureSignals"], ["permission-denied", "schema-validation"])
            self.assertEqual(value["workerWaitingReasons"], ["ImagePullBackOff"])
            self.assertNotIn("private-token", text)
            self.assertNotIn("host.invalid", text)

    @unittest.skipUnless(BASH, "Bash required")
    def test_denial_shell_never_restores_or_recovers_after_unproved_boundary(self):
        assert BASH
        text = (pause.FIXTURE / "schema-migration-parent.sh").read_text()
        library = text[
            text.index("schema_parent_denial() {") : text.index("schema_parent_suite() {")
        ]
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; source_dir=$1; failure=$3; access_count=0
step() { echo "$1" >> "$fixture/trace"; [[ $failure != "$1" ]]; }
start_stage() { :; }; complete_stage() { step complete; }
schema_parent_access() { access_count=$((access_count + 1)); step "access-$access_count-$1"; }
schema_parent_case() { step refused-and-retained; }
uv() { step "verify-${6}"; }
k() {
  if [[ $* == *narrow-patch* ]]; then step narrow-write >&2
  elif [[ $* == *restore-patch* ]]; then step restore-write >&2
  else step read-role >&2; fi
  echo '{}'
}
source "$2"
schema_parent_denial
echo repeats-and-recovery >> "$fixture/trace"
"""
        failures = (
            "pass",
            "access-1-false",
            "read-role",
            "verify-prepare",
            "narrow-write",
            "verify-narrowed",
            "access-2-true",
            "refused-and-retained",
            "access-3-true",
            "verify-restore",
            "restore-write",
            "access-4-false",
            "verify-restored",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "case.sh").write_text(library, newline="\n")
            (root / "run.sh").write_text(setup, newline="\n")
            for failure in failures:
                fixture = root / failure
                fixture.mkdir()
                process = subprocess.run(
                    [
                        BASH,
                        (root / "run.sh").as_posix(),
                        fixture.as_posix(),
                        (root / "case.sh").as_posix(),
                        failure,
                    ],
                    cwd=pause.ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                with self.subTest(failure=failure):
                    trace = (fixture / "trace").read_text().splitlines()
                    self.assertEqual(
                        process.returncode, 0 if failure == "pass" else 1, process.stderr
                    )
                    if failure == "pass":
                        self.assertLess(
                            trace.index("refused-and-retained"), trace.index("verify-restore")
                        )
                        self.assertEqual(trace[-1], "repeats-and-recovery")
                    else:
                        self.assertNotIn("repeats-and-recovery", trace)
                        self.assertEqual(trace[-1], failure)
                        if failures.index(failure) <= failures.index("refused-and-retained"):
                            self.assertNotIn("restore-write", trace)

    @unittest.skipUnless(BASH, "Bash required")
    def test_effective_access_checks_reject_errors_and_preserve_get_watch(self):
        assert BASH
        text = (pause.FIXTURE / "schema-migration-parent.sh").read_text()
        library = text[
            text.index("schema_parent_access() {") : text.index("schema_parent_denial() {")
        ]
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; case=$3
k() {
  echo "$*" >> "$fixture/trace"
  if [[ $case == transport ]]; then echo no; return 2; fi
  if [[ $case == warning ]]; then echo warning >&2; echo no; return 1; fi
  if [[ $case == wrong-get && $* == *'can-i get '* ]]; then echo no; return 1; fi
  if [[ $* == *'can-i create '* ]]; then echo no; return 1; fi
  echo yes
}
sleep() { SECONDS=$((SECONDS + 31)); }
source "$2"
schema_parent_access true
echo accepted >> "$fixture/trace"
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "case.sh").write_text(library, newline="\n")
            (root / "run.sh").write_text(setup, newline="\n")
            for case in ("pass", "transport", "warning", "wrong-get"):
                fixture = root / case
                fixture.mkdir()
                process = subprocess.run(
                    [
                        BASH,
                        (root / "run.sh").as_posix(),
                        fixture.as_posix(),
                        (root / "case.sh").as_posix(),
                        case,
                    ],
                    cwd=pause.ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                with self.subTest(case=case):
                    trace = (fixture / "trace").read_text()
                    self.assertEqual(process.returncode, 0 if case == "pass" else 1, process.stderr)
                    if case == "pass":
                        for verb in ("create", "get", "watch"):
                            self.assertIn(
                                "auth can-i " + verb + " jobs.batch -n perf-platform", trace
                            )
                        self.assertIn(
                            "--as system:serviceaccount:argo-workflows:release-schema-migration",
                            trace,
                        )
                    else:
                        self.assertNotIn("accepted", trace)

    @unittest.skipUnless(BASH, "Bash required")
    def test_real_suite_stops_before_recovery_at_every_evidence_boundary(self):
        assert BASH
        text = (pause.FIXTURE / "schema-migration-parent.sh").read_text()
        library = text[
            text.index("schema_parent_suite() {") : text.index("\nschema_parent_sources() {")
        ]
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; results=$1; source_dir=$1; failure=$3; current_stage=outer
start_stage() { echo "stage $1" >> "$fixture/trace"; }; complete_stage() { echo complete >> "$fixture/trace"; }
k() { echo '{}'; }; jq() { cat >/dev/null; echo workflow; }
schema_parent_snapshot() { echo baseline >> "$fixture/trace"; [[ $failure != baseline ]]; }
schema_parent_sources() { echo "installed-$1" >> "$fixture/trace"; [[ $failure != "installed-$1" ]]; }
schema_install_retention() { echo install-retention >> "$fixture/trace"; [[ $failure != install-retention ]]; }
schema_parent_denial() { schema_parent_case denied; }
schema_parent_access() { echo authority-final >> "$fixture/trace"; [[ $failure != authority-final ]]; }
schema_parent_case() { echo "case-$1" >> "$fixture/trace"; [[ $failure != "$1" ]]; }
uv() {
  if [[ $* == *' baseline '* ]]; then step=verify-baseline
  elif [[ $* == *' baseline-retained '* ]]; then step=verify-install-baseline
  elif [[ $* == *' final '* ]]; then step=verify-authority
  else step=verify-complete; fi
  echo "$step" >> "$fixture/trace"; [[ $failure != "$step" ]];
}
source "$2"
schema_parent_suite
echo recovery >> "$fixture/trace"
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "case.sh").write_text(library, newline="\n")
            (directory / "run.sh").write_text(setup, newline="\n")
            for failure in (
                "pass",
                "baseline",
                "verify-install-baseline",
                "installed-before",
                "verify-baseline",
                "none",
                "denied",
                "first",
                "repeat",
                "installed-after",
                "authority-final",
                "verify-authority",
                "verify-complete",
                "install-retention",
            ):
                fixture = directory / failure
                fixture.mkdir()
                process = subprocess.run(
                    [
                        BASH,
                        (directory / "run.sh").as_posix(),
                        fixture.as_posix(),
                        (directory / "case.sh").as_posix(),
                        failure,
                    ],
                    cwd=pause.ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                with self.subTest(failure=failure):
                    trace = (fixture / "trace").read_text().splitlines()
                    self.assertEqual(
                        process.returncode, 0 if failure == "pass" else 1, process.stderr
                    )
                    self.assertNotIn("unbound variable", process.stderr)
                    if failure == "pass":
                        self.assertEqual(trace[-2:], ["complete", "recovery"])
                    else:
                        self.assertNotIn("recovery", trace)
                        self.assertEqual(
                            trace[-1], "case-" + failure if failure in MODULE.LABELS else failure
                        )


if __name__ == "__main__":
    unittest.main()
