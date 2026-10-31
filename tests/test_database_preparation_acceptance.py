"""Raw nested execution, exact inventory and fail-stop tests; no live SQL or API."""

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

import test_schema_qualification_parent as qualification
import yaml
from test_release_workflows_integration import BASH

FIXTURE = qualification.FIXTURE


def module(name, dependencies):
    spec = importlib.util.spec_from_file_location(name, FIXTURE / (name + ".py"))
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", dependencies):
        spec.loader.exec_module(result)
    return result


DENIAL = module("schema_migration_denial", {})
SCOPE = module("preparation_scope", {})
MODULE = module(
    "database_preparation",
    {
        "preparation_scope": SCOPE,
        "schema_qualification_parent": qualification.MODULE,
        "schema_migration_denial": DENIAL,
    },
)
DIRECTORY = MODULE.parent.DIRECTORY


def populate(root, results):
    qualification.populate(root, results)

    def load(name):
        return json.loads((root / name).read_text())

    def save(name, value):
        (root / name).write_text(json.dumps(value))

    declarations = [
        yaml.safe_load((DIRECTORY / name).read_text())
        for name in (
            *MODULE.SOURCES,
            "schema-migration-intent-v1.yaml",
            "schema-qualification-sql-v1.yaml",
            "runtime-grants-sql-v1.yaml",
        )
    ]
    declarations.append(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "perfeng-release-verifiers-v3", "namespace": "argo-workflows"},
            "immutable": True,
            "data": {
                name: (DIRECTORY / "verifiers-v3" / name).read_text()
                for name in ("release_policy_v3.py", "release_runtime_v3.py")
            },
        }
    )
    authority = [
        obj
        for path in MODULE.AUTHORITY
        for obj in yaml.safe_load_all((DIRECTORY / path).read_text())
    ]
    for obj in [*declarations, *authority]:
        obj["metadata"]["uid"] = (
            "source-"
            + obj["kind"]
            + "-"
            + obj["metadata"].get("namespace", "cluster")
            + "-"
            + obj["metadata"]["name"]
        )
    original_role = deepcopy(
        next(
            o
            for o in authority
            if o["kind"] == "Role" and o["metadata"]["namespace"] == "perf-platform"
        )
    )
    original_role["metadata"]["resourceVersion"] = "1"
    save("preparation-denied.role-original.json", original_role)
    for role_state, version, narrowed in (
        ("narrowed", "2", True),
        ("current", "2", True),
        ("restored", "3", False),
        ("final", "4", False),
    ):
        value = deepcopy(original_role)
        value["metadata"]["resourceVersion"] = version
        if narrowed:
            value["rules"] = DENIAL.narrowed(MODULE.role_source())
        save(f"preparation-denied.role-{role_state}.json", value)
    for label in ("before", "null", "denied", "first", "repeat", "retained", "final"):
        for path in root.glob("qualification-before.*"):
            (
                root
                / ("preparation-" + label + "." + path.name.removeprefix("qualification-before."))
            ).write_bytes(path.read_bytes())
        save(f"preparation-{label}.sources.json", {"items": declarations})
        current_authority = deepcopy(authority)
        if label == "denied":
            for obj in current_authority:
                if obj["kind"] == "Role" and obj["metadata"]["namespace"] == "perf-platform":
                    obj.update(load("preparation-denied.role-narrowed.json"))
        save(f"preparation-{label}.authority.json", {"items": current_authority})
        save(f"preparation-{label}.jobs.json", {"items": []})
    all_jobs = []
    for index, label in enumerate(("null", "denied", "first", "repeat"), 1):
        refused = label == "null"
        denied = label == "denied"
        failed = refused or denied
        name, uid = "prepare-" + label, f"00000000-0000-0000-0000-{index:012d}"
        parameters = load(
            "parent-parameters.json" if refused else "upgrade-request-parameters.json"
        )
        phase_sources = {
            "migrate": deepcopy(
                qualification.adopted.schema.evidence(
                    "current-null" if refused else "first", retained_admission=True
                )[1]
            )
        }
        if denied:
            phase_sources["migrate"]["status"]["nodes"]["migrate"]["phase"] = "Failed"
        if not failed:
            phase_sources.update(
                qualify=load("qualification-first.workflow.json"),
                grant=load("grant-adopted.workflow.json"),
            )
        roots, nodes, workers, case_jobs = [], {}, [], []
        for offset, (phase, source) in enumerate(phase_sources.items()):
            old_nodes = source["status"]["nodes"]
            old_nodes.pop(source["metadata"]["name"])
            phase_id = name + "-" + phase
            mapping = {old: phase_id + "-" + old for old in old_nodes}
            earliest = min(datetime.fromisoformat(n["startedAt"]) for n in old_nodes.values())
            shift = timedelta(minutes=offset * 10)
            for old, node in old_nodes.items():
                node["id"] = mapping[old]
                node["boundaryID"] = (
                    mapping["preflight"]
                    if node["displayName"]
                    in {"validate-schema", "cluster-uid", "verify-identities"}
                    else phase_id
                )
                for field in ("startedAt", "finishedAt"):
                    node[field] = (datetime.fromisoformat(node[field]) + shift).isoformat()
                if node["displayName"] in {"preflight", "quiescence"}:
                    record = json.loads(node["outputs"]["parameters"][0]["value"])
                    record.update(
                        requestDigest=parameters["request-sha256"],
                        requestRevision=parameters["request-revision"],
                        selectionRevision=parameters["selection-revision"],
                    )
                    if node["displayName"] == "preflight":
                        record["requestPath"] = parameters["request-path"]
                    node["outputs"]["parameters"][0]["value"] = json.dumps(record)
                if node["displayName"] == "candidate" and not refused:
                    next(p for p in node["outputs"]["parameters"] if p["name"] == "request")[
                        "value"
                    ] = (root / "upgrade-request-request.json").read_text()
                if node["type"] == "Pod":
                    workers.append(
                        {
                            "uid": "worker-" + node["id"],
                            "nodeID": node["id"],
                            "account": "release-runtime-grants",
                            "exitCode": 1
                            if denied and node["displayName"] == "migrate"
                            else 2
                            if refused and node["displayName"] == "candidate"
                            else 0,
                        }
                    )
            phase_root = {
                "id": phase_id,
                "displayName": phase,
                "type": "Steps",
                "phase": "Failed" if failed else "Succeeded",
                "boundaryID": name,
                "templateRef": {"name": SCOPE.PHASES[phase], "template": phase},
                "startedAt": (earliest + shift).isoformat(),
                "finishedAt": max(n["finishedAt"] for n in old_nodes.values()),
            }
            if not failed:
                leaf_name = {
                    "migrate": "schema-migration",
                    "qualify": "schema-qualification",
                    "grant": "runtime-grants",
                }[phase]
                leaf = yaml.safe_load((DIRECTORY / (leaf_name + "-job-v1.yaml")).read_text())
                manifest = leaf["spec"]["templates"][0]["resource"]["manifest"]
                for key, value in {
                    "uid": uid,
                    **{"parameters." + k: v for k, v in parameters.items()},
                }.items():
                    manifest = manifest.replace("{{workflow." + key + "}}", value)
                job = yaml.safe_load(manifest)
                job["metadata"].update(
                    uid=label + "-" + phase + "-job",
                    creationTimestamp=(
                        datetime.fromisoformat(old_nodes[phase]["startedAt"]) + timedelta(seconds=1)
                    ).isoformat(),
                )
                job["status"] = {
                    "succeeded": 1,
                    "conditions": [{"type": "Complete", "status": "True"}],
                }
                pod = {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": job["metadata"]["name"] + "-pod",
                        "uid": "pod-" + job["metadata"]["uid"],
                        "namespace": "perf-platform",
                        "ownerReferences": [
                            {
                                "kind": "Job",
                                "uid": job["metadata"]["uid"],
                                "name": job["metadata"]["name"],
                                "controller": True,
                            }
                        ],
                    },
                    "spec": deepcopy(job["spec"]["template"]["spec"]),
                    "status": {
                        "phase": "Succeeded",
                        "containerStatuses": [
                            {
                                "name": phase,
                                "restartCount": 0,
                                "state": {"terminated": {"exitCode": 0}},
                            }
                        ],
                    },
                }
                for suffix in ("", "current-"):
                    save(f"preparation-{label}.{phase}.{suffix}job.json", job)
                    save(f"preparation-{label}.{phase}.{suffix}pods.json", {"items": [pod]})
                phase_root["outputs"] = {
                    "parameters": [
                        {"name": "job-name", "value": job["metadata"]["name"]},
                        {
                            "name": "checkpoint",
                            "value": old_nodes["quiescence"]["outputs"]["parameters"][0]["value"],
                        },
                    ]
                }
                case_jobs.append(job)
            else:
                for operation in SCOPE.PHASES:
                    for suffix in ("", "current-"):
                        save(f"preparation-{label}.{operation}.{suffix}job.json", None)
                        save(f"preparation-{label}.{operation}.{suffix}pods.json", {"items": []})
            roots.append(phase_root)
            nodes.update({node["id"]: node for node in old_nodes.values()})
            nodes[phase_id] = phase_root
            group_id = name + f"[{offset}]"
            nodes[group_id] = {
                "id": group_id,
                "displayName": f"[{offset}]",
                "type": "StepGroup",
                "boundaryID": name,
                "phase": phase_root["phase"],
            }
        outer = {
            "id": name,
            "displayName": name,
            "type": "Steps",
            "templateName": "prepare",
            "phase": "Failed" if failed else "Succeeded",
        }
        if not failed:
            outer["outputs"] = {
                "parameters": [
                    {"name": key, "value": job["metadata"]["name"]}
                    for key, job in zip(
                        ("migration-job-name", "qualification-job-name", "grants-job-name"),
                        case_jobs,
                        strict=True,
                    )
                ]
                + [{"name": "checkpoint", "value": roots[-1]["outputs"]["parameters"][1]["value"]}]
            }
            (root / f"preparation-{label}.qualify.worker.private").write_text(
                "Selected migration ledger and ownership qualified.\n"
            )
        nodes[name] = outer
        workflow = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Workflow",
            "metadata": {"name": name, "uid": uid, "namespace": "argo-workflows"},
            "spec": {
                "workflowTemplateRef": {"name": "perfeng-release-database-prepare-v1"},
                "arguments": {
                    "parameters": [{"name": k, "value": v} for k, v in parameters.items()]
                },
            },
            "status": {
                "phase": outer["phase"],
                "storedWorkflowTemplateSpec": yaml.safe_load(
                    (DIRECTORY / "release-database-prepare-v1.yaml").read_text()
                )["spec"],
                "nodes": nodes,
            },
        }
        for suffix in ("", "current-"):
            save(f"preparation-{label}.{suffix}workflow.json", workflow)
            save(f"preparation-{label}.{suffix}workers.json", workers)
        if denied:
            native_id = name + "-migrate-migrate"
            worker = next(w for w in workers if w["nodeID"] == native_id)
            save(
                "preparation-denied.diagnostic-pod.json",
                {
                    "metadata": {
                        "name": "denied-native-worker",
                        "namespace": "argo-workflows",
                        "uid": worker["uid"],
                        "annotations": {"workflows.argoproj.io/node-id": native_id},
                    },
                    "spec": {"serviceAccountName": worker["account"]},
                    "status": {
                        "containerStatuses": [
                            {"name": "main", "state": {"terminated": {"exitCode": 1}}}
                        ]
                    },
                },
            )
            (root / "preparation-denied.worker.private").write_text(
                'Error from server (Forbidden): jobs.batch "perfeng-schema-'
                + uid
                + '" is forbidden: User "system:serviceaccount:argo-workflows:release-runtime-grants" cannot create resource "jobs" in API group "batch" in the namespace "perf-platform"\n'
            )
        all_jobs.extend(case_jobs)
        save(f"preparation-{label}.jobs.json", {"items": deepcopy(all_jobs)})
    for label in ("retained", "final"):
        save(f"preparation-{label}.jobs.json", {"items": all_jobs})
    for suffix in ("workflow", "job", "worker"):
        save(
            "preparation.probe-" + suffix + ".json",
            json.loads((results / ("runtime-grants-adopted." + suffix + ".json")).read_text()),
        )


class PreparationAcceptanceTests(unittest.TestCase):
    def test_only_initial_reuses_existing_probe_and_gates_recovery_and_final_inventories(self):
        adopted = (FIXTURE / "runtime-grants-adopted.sh").read_text()
        start = adopted.index('source "$source_dir/database-preparation.sh"')
        self.assertLess(adopted.index("runtime_grant_parent adopted\n"), start)
        self.assertLess(start, adopted.index("runtime_probe adopted success"))
        shell = (FIXTURE / "database-preparation.sh").read_text()
        for guard in (
            "${scenario:-} == initial",
            "${mode:-} == preflight-parent",
            "${RUNNER_ENVIRONMENT:-} == github-hosted",
            '$KUBECONFIG == "$fixture/kubeconfig"',
        ):
            self.assertIn(guard, shell)
        self.assertNotIn("runtime_probe ", shell)
        self.assertNotIn("trap ", shell)
        pause = (FIXTURE / "maintenance-pause.sh").read_text()
        self.assertLess(
            pause.index("\npreparation_retention\n"),
            pause.index("qualification_retention retained preparation"),
        )
        self.assertLess(
            pause.index("qualification_retention retained preparation"),
            pause.index("start_stage maintenance-recovery"),
        )
        final = (FIXTURE / "preflight-parent.sh").read_text()
        self.assertLess(
            final.index("preparation_retention final"), final.index("schema_parent_retention final")
        )
        for path, kind in (
            ("schema-migration-parent.sh", "migrate"),
            ("runtime-grants-parent.sh", "grant"),
            ("schema-qualification-parent.sh", "qualify"),
        ):
            self.assertIn("preparation_inventory " + kind, (FIXTURE / path).read_text())

    def test_complete_nested_execution_refusal_and_retention_preserve_raw_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            raw = (root / "preparation-first.workflow.json").read_bytes()
            for mode in (
                "before",
                "null",
                "denied",
                "first",
                "repeat",
                "complete",
                "retained",
                "final",
            ):
                reports = MODULE.verify(root, results, mode)
            self.assertEqual(len(reports), 4)
            self.assertEqual(sum(len(r["jobUIDs"]) for r in reports), 6)
            self.assertEqual((root / "preparation-first.workflow.json").read_bytes(), raw)

    def test_missing_or_tampered_nested_evidence_blocks_publication_and_partition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            MODULE.verify(root, results, "complete")
            cases = (
                (
                    "first.current-workflow.json",
                    lambda v: v["status"]["nodes"]["prepare-first-migrate-candidate"].update(
                        boundaryID="prepare-first-migrate-candidate"
                    ),
                ),
                (
                    "first.current-workflow.json",
                    lambda v: v["status"]["nodes"]["prepare-first-migrate-candidate"].update(
                        finishedAt="2099-01-01T00:00:00+00:00"
                    ),
                ),
                (
                    "null.current-workers.json",
                    lambda v: next(w for w in v if w["nodeID"].endswith("candidate")).update(
                        exitCode=0
                    ),
                ),
                (
                    "first.current-workflow.json",
                    lambda v: v["status"]["nodes"]["prepare-first-qualify"]["templateRef"].update(
                        template="grant"
                    ),
                ),
                (
                    "first.current-workflow.json",
                    lambda v: v["status"]["nodes"]["prepare-first-qualify"].update(
                        boundaryID="prepare-first-grant"
                    ),
                ),
                (
                    "first.current-workflow.json",
                    lambda v: v["status"]["storedWorkflowTemplateSpec"].update(
                        serviceAccountName="default"
                    ),
                ),
                (
                    "first.current-workflow.json",
                    lambda v: v["status"]["nodes"]["prepare-first-grant"]["outputs"]["parameters"][
                        0
                    ].update(value="different-job"),
                ),
                (
                    "null.current-workflow.json",
                    lambda v: v["status"]["nodes"]["prepare-null"].update(
                        outputs={"parameters": [{"name": "checkpoint", "value": "success"}]}
                    ),
                ),
                (
                    "first.current-workers.json",
                    lambda v: v[0].update(account="release-schema-migration"),
                ),
                (
                    "first.migrate.current-job.json",
                    lambda v: v["metadata"].update(uid="replacement"),
                ),
                (
                    "retained.sources.json",
                    lambda v: v["items"][0]["spec"].update(activeDeadlineSeconds=3600),
                ),
                (
                    "retained.authority.json",
                    lambda v: v["items"][1]["rules"][0]["verbs"].append("delete"),
                ),
                ("retained.jobs.json", lambda v: v["items"].pop()),
                ("retained.roles.json", lambda v: v.update(password="changed")),
                ("probe-worker.json", lambda v: v[0]["containers"][0].update(exitCode=1)),
            )
            for suffix, mutate in cases:
                path = root / (
                    "preparation." + suffix
                    if suffix.startswith("probe-")
                    else "preparation-" + suffix
                )
                raw = path.read_bytes()
                value = json.loads(raw)
                mutate(value)
                path.write_text(json.dumps(value))
                with self.subTest(suffix=suffix), self.assertRaises((AssertionError, KeyError)):
                    MODULE.verify(root, results, "retained")
                self.assertFalse((results / "database-preparation-retained.outcome.json").exists())
                path.write_bytes(raw)

    def test_partition_admits_only_two_fully_verified_jobs_and_preserves_unknowns(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            MODULE.verify(root, results, "complete")
            path = root / "legacy.jobs.json"
            for kind in SCOPE.PHASES:
                originals = [
                    MODULE.load(root, f"preparation-{label}.{kind}.job.json")
                    for label in ("first", "repeat")
                ]
                extra = deepcopy(originals[0])
                extra["metadata"].update(uid="unknown", name="unexpected-job")
                value = {"items": [*originals, extra]}
                path.write_text(json.dumps(value))
                MODULE.partition(root, results, "retained", kind, path)
                self.assertEqual(json.loads(path.read_text()), {"items": [extra]})
                self.assertEqual(json.loads(path.with_suffix(".all.json").read_text()), value)
                path.write_text(json.dumps({"items": [originals[0], extra]}))
                with self.assertRaises(AssertionError):
                    MODULE.partition(root, results, "retained", kind, path)

    @unittest.skipUnless(BASH, "Bash required for actual coordinator fail-stop checks")
    def test_actual_coordinator_stops_before_next_case_or_probe_on_failure(self):
        assert BASH
        source = (FIXTURE / "database-preparation.sh").read_text()
        case = source[
            source.index("preparation_case() {") : source.index("\npreparation_retention() {")
        ]
        suite = source[
            source.index("preparation_suite() {") : source.index("\npreparation_suite\n")
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for fail in (
                "before",
                "null",
                "role-prepare",
                "role-narrowed",
                "denied",
                "role-restore",
                "role-restored",
                "wait",
                "first",
                "repeat",
                "complete",
                "pass",
            ):
                trace = root / (fail + ".trace")
                script = root / (fail + ".sh")
                script.write_text(
                    """set -euo pipefail
fixture=$PWD; results=$PWD; source_dir=$PWD; KUBECONFIG=$PWD/kubeconfig; current_stage=outer
log() { printf '%s\\n' "$1" >> "$TRACE"; }
start_stage() { current_stage=$1; log "$1"; }
complete_stage() { log complete; }
k() { printf '{}'; }
a() { printf '{}'; }
jq() { cat >/dev/null; printf workflow; }
argo() { [[ $current_stage != database-preparation-null && $current_stage != database-preparation-denied ]] && [[ $FAIL != wait || $current_stage != database-preparation-first ]]; }
timeout() { shift; "$@"; }
qualification_workers() { printf '[]' > "$2"; }
preparation_jobs() { log "jobs-$1"; }
preparation_snapshot() { log "snapshot-$1"; }
runtime_grant_access() { log "rights-$1-$2"; }
schema_parent_ledger_access() { log access; }
uv() { local mode=${6}; log "verify-$mode"; [[ $FAIL != "$mode" ]]; }
"""
                    + case
                    + suite
                    + "\npreparation_suite\nlog probe\nlog recovery\n",
                    newline="\n",
                )
                result = subprocess.run(
                    [BASH, str(script)],
                    cwd=root,
                    env={**os.environ, "TRACE": trace.as_posix(), "FAIL": fail, "BASH_ENV": ""},
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                lines = trace.read_text().splitlines()
                with self.subTest(fail=fail):
                    self.assertEqual(result.returncode, 0 if fail == "pass" else 1, result.stderr)
                    self.assertEqual("probe" in lines, fail == "pass")
                    self.assertEqual("recovery" in lines, fail == "pass")

    @unittest.skipUnless(BASH, "Bash required for retained proof and partition sequencing")
    def test_retention_cannot_enable_partition_or_recovery_after_unproved_state(self):
        assert BASH
        source = (FIXTURE / "database-preparation.sh").read_text()
        library = source[
            source.index("preparation_retention() {") : source.index("\npreparation_suite() {")
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for failure in ("snapshot", "access", "rights", "retained", "pass"):
                trace = root / (failure + ".trace")
                script = root / (failure + ".sh")
                script.write_text(
                    """set -euo pipefail
fixture=$PWD; results=$PWD; source_dir=$PWD
step() { printf '%s\\n' "$1" >> "$TRACE"; [[ $FAIL != "$1" ]]; }
start_stage() { :; }; complete_stage() { :; }
jq() { if [[ $* == -er* ]]; then printf workflow; else printf '[]'; fi; }
k() { printf '{}'; }; a() { printf '{}'; }
qualification_workers() { printf '[]' > "$2"; }
preparation_jobs() { :; }
preparation_snapshot() { step snapshot; }
runtime_grant_access() { step rights; }
schema_parent_ledger_access() { step access; }
uv() { step "${6}"; }
"""
                    + library
                    + '\npreparation_retention\npreparation_inventory migrate "$PWD/inventory.json"\nstep recovery\n',
                    newline="\n",
                )
                result = subprocess.run(
                    [BASH, str(script)],
                    cwd=root,
                    env={**os.environ, "FAIL": failure, "TRACE": trace.as_posix(), "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                lines = trace.read_text().splitlines()
                with self.subTest(failure=failure):
                    self.assertEqual(
                        result.returncode, 0 if failure == "pass" else 1, result.stderr
                    )
                    self.assertEqual("partition" in lines, failure == "pass")
                    self.assertEqual("recovery" in lines, failure == "pass")


if __name__ == "__main__":
    unittest.main()
