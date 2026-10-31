"""CI-only complete-parent evidence; no SQL policy or deployment orchestration."""

import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import preparation_scope as scope
import schema_migration_denial as denial
import schema_qualification_parent as qualification
import yaml

adopted = qualification.adopted
grants = adopted.grants
parent = grants.parent
SOURCES = (
    "release-database-prepare-v1.yaml",
    "release-schema-migration-v2.yaml",
    "release-schema-qualification-v1.yaml",
    "release-runtime-grants-v2.yaml",
    "release-schema-migration-v1.yaml",
    "schema-migration-job-v1.yaml",
    "schema-qualification-job-v1.yaml",
    "runtime-grants-job-v1.yaml",
    "release-preflight-v3.yaml",
    "release-task-selection-v3.yaml",
    "verify-release-quiescence-v3.yaml",
)
AUTHORITY = (
    *(
        f"rbac/runtime-grants/{name}.yaml"
        for name in (
            "serviceaccount",
            "workflow-access",
            "cluster-identity",
            "platform-observation",
        )
    ),
    "../release-workflows-bootstrap/runtime-grants-application-access.yaml",
    "../release-workflows-bootstrap/runtime-grants-ledger-access.yaml",
)
PREFIXES = {
    "migrate": "perfeng-schema-",
    "qualify": "perfeng-schema-qualify-",
    "grant": "perfeng-runtime-grants-",
}


def load(root: Path, name: str) -> Any:
    return json.loads((root / name).read_text())


def installed(root: Path, label: str) -> Any:
    objects = load(root, f"preparation-{label}.sources.json")["items"]
    actual = {(o["kind"], o["metadata"]["namespace"], o["metadata"]["name"]): o for o in objects}
    expected = [
        yaml.safe_load((parent.DIRECTORY / name).read_text())
        for name in (
            *SOURCES,
            "schema-migration-intent-v1.yaml",
            "schema-qualification-sql-v1.yaml",
            "runtime-grants-sql-v1.yaml",
        )
    ]
    expected.append(
        {
            "kind": "ConfigMap",
            "metadata": {"name": "perfeng-release-verifiers-v3", "namespace": "argo-workflows"},
            "immutable": True,
            "data": {
                name: (parent.DIRECTORY / "verifiers-v3" / name).read_text()
                for name in ("release_policy_v3.py", "release_runtime_v3.py")
            },
        }
    )
    assert len(actual) == len(objects) == len(expected)
    for wanted in expected:
        obj = actual[(wanted["kind"], wanted["metadata"]["namespace"], wanted["metadata"]["name"])]
        parent.subset(wanted, obj)
        if wanted["kind"] == "WorkflowTemplate":
            assert obj["spec"] == wanted["spec"]
        else:
            assert obj["data"] == wanted["data"] and not obj.get("binaryData")
        assert not obj["metadata"].get("ownerReferences")
    authority = load(root, f"preparation-{label}.authority.json")
    expected_authority = [
        obj
        for path in AUTHORITY
        for obj in yaml.safe_load_all((parent.DIRECTORY / path).read_text())
    ]
    actual_authority = {
        (o["kind"], o["metadata"].get("namespace"), o["metadata"]["name"]): o
        for o in authority["items"]
    }
    assert len(actual_authority) == len(authority["items"]) == len(expected_authority)
    for wanted in expected_authority:
        obj = actual_authority[
            (wanted["kind"], wanted["metadata"].get("namespace"), wanted["metadata"]["name"])
        ]
        if label in {"denied", "qualify-denied"} and (
            wanted["kind"],
            wanted["metadata"].get("namespace"),
        ) == (
            "Role",
            "perf-platform",
        ):
            denial.validate(
                load(root, f"preparation-{label}.role-original.json"), obj, True, wanted
            )
            # Normalize only the exactly validated create-only fault for identity comparison.
            obj["rules"] = deepcopy(wanted["rules"])
        parent.subset(wanted, obj)
        for key in ("rules", "roleRef", "subjects"):
            assert obj.get(key) == wanted.get(key)
    all_objects = [*objects, *authority["items"]]
    return {
        namespace: grants.install.inventory(
            {"items": [o for o in all_objects if o["metadata"].get("namespace") == namespace]}
        )
        for namespace in {o["metadata"].get("namespace") for o in all_objects}
    }


def stable(root: Path, label: str, *, final: bool = False) -> Any:
    return qualification.stable(root, label, final=final, prefix="preparation-")


def execution(root: Path, label: str, *, retained: bool = False) -> dict[str, Any]:
    before, after = stable(root, "before"), stable(root, label)
    parameters, candidate = adopted.request(root, before)
    refused = label == "null"
    denied = label == "denied"
    qualification_denied = label == "qualify-denied"
    barrier = label == "qualify-barrier"
    failed = refused or denied or qualification_denied or barrier
    raw = (root / "upgrade-request-request.json").read_bytes()
    accepted = (root / "parent-request.json").read_bytes()
    if refused:
        upgrade = parameters
        parameters = load(root, "parent-parameters.json")
        raw, candidate = accepted, json.loads(accepted)
        assert set(parameters) == set(upgrade)
        assert parameters["request-sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
        assert all(parameters[k] == upgrade[k] for k in ("request-revision", "selection-revision"))
        assert re.fullmatch(
            r"deployments/release-requests/[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.json",
            parameters["request-path"],
        )
        assert parameters["request-path"] != upgrade["request-path"]
        assert candidate["promotion"]["expectedCurrent"] is None
    assert installed(root, label) == installed(root, "before")
    suffix = "current-" if retained else ""
    workflow = load(root, f"preparation-{label}.{suffix}workflow.json")
    workers = load(root, f"preparation-{label}.{suffix}workers.json")
    wanted = yaml.safe_load((parent.DIRECTORY / SOURCES[0]).read_text())["spec"]
    projected = (
        scope.barrier_view(workflow, wanted, running=barrier)
        if qualification_denied or barrier
        else workflow
    )
    views = scope.scopes(
        projected,
        wanted,
        parameters,
        migration_failed=refused or denied,
        qualification_denied=qualification_denied,
        barrier=barrier,
    )
    pods = [n for n in workflow["status"]["nodes"].values() if n["type"] == "Pod"]
    assert len(workers) == len(pods) == len({w["uid"] for w in workers})
    assert {w["nodeID"] for w in workers} == {n["id"] for n in pods}
    phase_nodes = []
    jobs = []
    for phase, view in views.items():
        node_ids = {n["id"] for n in view["status"]["nodes"].values()}
        phase_workers = [w for w in workers if w["nodeID"] in node_ids]
        phase_failed = (refused or denied) or (qualification_denied and phase == "qualify")
        job = None if phase_failed else load(root, f"preparation-{label}.{phase}.{suffix}job.json")
        job_pods = (
            {"items": []}
            if phase_failed
            else load(root, f"preparation-{label}.{phase}.{suffix}pods.json")
        )
        if phase == "migrate":
            parent.execution(
                "current-null"
                if refused
                else "first"
                if qualification_denied or barrier
                else label,
                view,
                phase_workers,
                parameters,
                candidate,
                after["namespace"]["metadata"]["uid"],
                [*after["applications"]["items"], after["deployment"]],
                job,
                job_pods,
                denied_log(root, view, phase_workers) if denied else "",
                retained_admission=True,
                account="release-runtime-grants",
            )
        else:
            grants.execution(
                view,
                phase_workers,
                parameters,
                candidate,
                [*after["applications"]["items"], after["deployment"]],
                job,
                job_pods,
                retained_admission=True,
                qualification=phase == "qualify",
                denied=qualification_denied,
                diagnostic=denied_log(root, view, phase_workers, label=label, operation="qualify")
                if qualification_denied
                else "",
            )
        if not refused:
            parent.admission_nodes(view, raw, after)
        if not phase_failed:
            assert job is not None
            jobs.append(job)
            if retained:
                parent.retained_job(load(root, f"preparation-{label}.{phase}.job.json"), job)
                assert grants.install.inventory(
                    load(root, f"preparation-{label}.{phase}.pods.json")
                ) == grants.install.inventory(job_pods)
        phase_nodes.append(view["status"]["nodes"][view["metadata"]["name"]])
    parent.ordered(phase_nodes)
    top = workflow["status"]["nodes"][workflow["metadata"]["name"]]
    outputs = top.get("outputs", {}).get("parameters", [])
    values = {p["name"]: p.get("value") for p in outputs}
    if failed:
        for phase in scope.PHASES:
            if (qualification_denied or barrier) and phase == "migrate":
                continue
            assert load(root, f"preparation-{label}.{phase}.{suffix}job.json") is None
            assert load(root, f"preparation-{label}.{phase}.{suffix}pods.json")["items"] == []
        assert not any(values.values())
        assert not any(
            p.get("value") for p in workflow["status"].get("outputs", {}).get("parameters", [])
        )
    else:
        assert len(outputs) == len(values) == 4
        for phase, key in (
            ("migrate", "migration-job-name"),
            ("qualify", "qualification-job-name"),
            ("grant", "grants-job-name"),
        ):
            assert values[key] == next(
                j["metadata"]["name"]
                for j in jobs
                if j["metadata"]["name"].startswith(PREFIXES[phase])
            )
        assert json.loads(values["checkpoint"]) == json.loads(
            next(
                p["value"]
                for p in phase_nodes[-1]["outputs"]["parameters"]
                if p["name"] == "checkpoint"
            )
        )
        assert (
            root / f"preparation-{label}.qualify.worker.private"
        ).read_text().strip() == "Selected migration ledger and ownership qualified."
    if retained:
        if qualification_denied:
            assert sorted(workers, key=lambda w: w["nodeID"]) == sorted(
                load(root, f"preparation-{label}.workers.json"), key=lambda w: w["nodeID"]
            )
        assert grants.install.inventory(
            {"items": [load(root, f"preparation-{label}.workflow.json")]}
        ) == grants.install.inventory({"items": [workflow]})
    return {
        "decision": "database-preparation-null-refused"
        if refused
        else "database-preparation-job-create-refused"
        if denied
        else "database-preparation-qualification-create-refused"
        if qualification_denied
        else "database-preparation-barrier-verified"
        if barrier
        else "database-preparation-verified",
        "workflowUID": workflow["metadata"]["uid"],
        "requestDigest": parameters["request-sha256"],
        "jobUIDs": [j["metadata"]["uid"] for j in jobs],
    }


def barrier_proof(root: Path) -> list[dict[str, Any]]:
    original = load(root, "preparation-qualify-denied.installed.json")
    assert original["kind"] == "WorkflowTemplate" and original["metadata"]["name"] == scope.CI_NAME
    assert original["metadata"]["namespace"] == "argo-workflows" and original["metadata"]["uid"]
    assert not original["metadata"].get("ownerReferences") and not original["metadata"].get(
        "deletionTimestamp"
    )
    wanted = yaml.safe_load((parent.DIRECTORY / SOURCES[0]).read_text())["spec"]
    assert original["spec"] == scope.ci_spec(wanted)
    report = execution(root, "qualify-barrier")
    before = load(root, "preparation-before.jobs.json")["items"]
    actual = load(root, "preparation-qualify-barrier.jobs.json")["items"]
    job = load(root, "preparation-qualify-barrier.migrate.job.json")
    assert grants.install.inventory({"items": actual}) == grants.install.inventory(
        {"items": [*before, job]}
    )
    # The previous denied case creates no Job. Preserve every earlier Job exactly.
    for original in before:
        parent.retained_job(
            original,
            next(j for j in actual if j["metadata"]["uid"] == original["metadata"]["uid"]),
            failed=original["status"].get("failed", 0) > 0,
        )
    return [report]


def role_source() -> Any:
    return next(
        obj
        for obj in yaml.safe_load_all(
            (parent.DIRECTORY / "rbac/runtime-grants/platform-observation.yaml").read_text()
        )
        if obj["kind"] == "Role"
    )


def denied_log(
    root: Path, view: Any, workers: Any, *, label: str = "denied", operation: str = "migrate"
) -> str:
    """Bind the supplied Forbidden diagnostic to the exact failed native worker."""
    node = parent.maintenance.readiness.named(view, operation)
    worker = next(w for w in workers if w["nodeID"] == node["id"])
    pod = load(root, f"preparation-{label}.diagnostic-pod.json")
    assert pod["metadata"]["namespace"] == "argo-workflows"
    assert pod["metadata"]["uid"] == worker["uid"]
    assert pod["metadata"]["annotations"]["workflows.argoproj.io/node-id"] == node["id"]
    assert pod["spec"]["serviceAccountName"] == worker["account"] == "release-runtime-grants"
    main = [s for s in pod["status"]["containerStatuses"] if s["name"] == "main"]
    assert len(main) == 1 and main[0]["state"]["terminated"]["exitCode"] == worker["exitCode"] == 1
    return (root / f"preparation-{label}.worker.private").read_text()


def probe(root: Path, results: Path) -> None:
    original = [
        load(results, "runtime-grants-adopted." + key + ".json")
        for key in ("workflow", "job", "worker")
    ]
    current = [
        load(root, "preparation.probe-" + key + ".json") for key in ("workflow", "job", "worker")
    ]
    diagnostic = (root / "grant-adopted.probe.private").read_text()
    adopted.probe_evidence(original[0], original[1], original[2], diagnostic)
    adopted.probe_evidence(current[0], current[1], current[2], diagnostic)
    assert grants.install.inventory({"items": [original[0]]}) == grants.install.inventory(
        {"items": [current[0]]}
    )
    parent.retained_job(original[1], current[1])
    assert current[2] == original[2]


def verify(root: Path, results: Path, mode: str) -> list[dict[str, Any]]:
    assert mode in {
        "before",
        "null",
        "denied",
        "qualify-barrier",
        "qualify-denied",
        "first",
        "repeat",
        "complete",
        "retained",
        "final",
    }
    adopted.request(root, stable(root, "before"))
    installed(root, "before")
    if mode == "before":
        return []
    if mode == "qualify-barrier":
        return barrier_proof(root)
    labels = (
        ("null",)
        if mode == "null"
        else ("null", "denied")
        if mode == "denied"
        else ("null", "denied", "qualify-denied")
        if mode == "qualify-denied"
        else ("null", "denied", "qualify-denied", "first")
        if mode == "first"
        else ("null", "denied", "qualify-denied", "first", "repeat")
    )
    retained = mode in {"retained", "final"}
    reports = [execution(root, label, retained=retained) for label in labels]
    assert len({r["workflowUID"] for r in reports}) == len(reports)
    identities = [uid for r in reports for uid in r["jobUIDs"]]
    assert (
        len(identities)
        == len(set(identities))
        == (
            0
            if mode in {"null", "denied"}
            else 1
            if mode == "qualify-denied"
            else 4
            if mode == "first"
            else 7
        )
    )
    if mode not in {"null", "denied"}:
        denial.execute(
            "final" if retained else "restored", root, "preparation-denied", role_source()
        )
    if mode not in {"null", "denied", "qualify-denied"}:
        denial.execute(
            "final" if retained else "restored", root, "preparation-qualify-denied", role_source()
        )
    if "qualify-denied" in labels:
        barrier_proof(root)
        original = load(root, "preparation-qualify-barrier.workflow.json")
        current = load(root, "preparation-qualify-denied.workflow.json")
        for key in ("uid", "name", "namespace"):
            assert original["metadata"][key] == current["metadata"][key]
        assert original["spec"] == current["spec"]
        before_view = scope.barrier_view(
            original,
            yaml.safe_load((parent.DIRECTORY / SOURCES[0]).read_text())["spec"],
            running=True,
        )
        after_view = scope.barrier_view(
            current,
            yaml.safe_load((parent.DIRECTORY / SOURCES[0]).read_text())["spec"],
            running=False,
        )
        for identity, node in before_view["status"]["nodes"].items():
            if node["type"] == "Pod" or (
                node["type"] == "Steps" and identity != original["metadata"]["name"]
            ):
                # Argo may append cross-phase graph edges after the suspended step.
                ignored = {"children", "outboundNodes"}
                assert {k: v for k, v in node.items() if k not in ignored} == {
                    k: v
                    for k, v in after_view["status"]["nodes"][identity].items()
                    if k not in ignored
                }
        migration_workers = load(root, "preparation-qualify-barrier.workers.json")
        later_workers = load(root, "preparation-qualify-denied.workers.json")
        assert all(w in later_workers for w in migration_workers)
        parent.retained_job(
            load(root, "preparation-qualify-barrier.migrate.job.json"),
            load(root, "preparation-qualify-denied.migrate.job.json"),
        )
        assert grants.install.inventory(
            load(root, "preparation-qualify-barrier.migrate.pods.json")
        ) == grants.install.inventory(load(root, "preparation-qualify-denied.migrate.pods.json"))
    sample = mode if retained else labels[-1]
    if retained:
        stable(root, mode, final=mode == "final")
        assert installed(root, mode) == installed(root, "before")
        ci_original = load(root, "preparation-qualify-denied.installed.json")
        ci_current = load(root, "preparation-qualify-denied.current-template.json")
        assert grants.install.inventory({"items": [ci_original]}) == grants.install.inventory(
            {"items": [ci_current]}
        )
        probe(root, results)
        assert reports == load(root, "preparation-complete.verified.json")
    before_jobs = load(root, "preparation-before.jobs.json")["items"]
    actual = load(root, f"preparation-{sample}.jobs.json")["items"]
    new_jobs = [
        load(root, f"preparation-{label}.{phase}.job.json")
        for label in labels
        if label not in {"null", "denied"}
        for phase in scope.PHASES
        if label != "qualify-denied" or phase == "migrate"
    ]
    assert grants.install.inventory({"items": actual}) == grants.install.inventory(
        {"items": [*before_jobs, *new_jobs]}
    )
    for original in before_jobs:
        parent.retained_job(
            original,
            next(j for j in actual if j["metadata"]["uid"] == original["metadata"]["uid"]),
            failed=original["status"].get("failed", 0) > 0,
        )
    (root / f"preparation-{mode}.verified.json").write_text(json.dumps(reports) + "\n")
    if retained:
        (results / f"database-preparation-{mode}.outcome.json").write_text(
            json.dumps(reports) + "\n"
        )
    return reports


def partition(root: Path, results: Path, phase: str, kind: str, path: Path) -> None:
    """Remove only the fully revalidated cohort Jobs for a strict legacy suite."""
    assert phase in {"retained", "final"} and kind in scope.PHASES
    verify(root, results, phase)
    value = json.loads(path.read_text())
    originals = [
        load(root, f"preparation-{label}.{kind}.job.json")
        for label in (
            ("qualify-denied", "first", "repeat") if kind == "migrate" else ("first", "repeat")
        )
    ]
    uids = {job["metadata"]["uid"] for job in originals}
    assert len(uids) == (3 if kind == "migrate" else 2)
    assert len({j["metadata"]["uid"] for j in value["items"]}) == len(value["items"])
    for original in originals:
        matches = [
            job for job in value["items"] if job["metadata"]["uid"] == original["metadata"]["uid"]
        ]
        assert len(matches) == 1
        parent.retained_job(original, matches[0])
    path.with_suffix(".all.json").write_bytes(path.read_bytes())
    path.write_text(
        json.dumps(
            {
                **value,
                "items": [job for job in value["items"] if job["metadata"]["uid"] not in uids],
            }
        )
        + "\n"
    )


def main() -> None:
    mode, directory, output, *extra = sys.argv[1:]
    root, results = Path(directory), Path(output)
    if mode == "ci-template":
        wanted = yaml.safe_load((parent.DIRECTORY / SOURCES[0]).read_text())
        wanted["metadata"] = {"name": scope.CI_NAME, "namespace": "argo-workflows"}
        wanted["spec"] = scope.ci_spec(wanted["spec"])
        (root / "preparation-qualify-denied.template.json").write_text(json.dumps(wanted) + "\n")
    elif mode.startswith("qualify-role-"):
        action = mode.removeprefix("qualify-role-")
        assert not extra
        if action == "prepare":
            verify(root, results, "qualify-barrier")
        if action == "restore":
            verify(root, results, "qualify-denied")
        denial.execute(action, root, "preparation-qualify-denied", role_source())
    elif mode.startswith("role-"):
        action = mode.removeprefix("role-")
        assert not extra
        if action == "restore":
            verify(root, results, "denied")
        denial.execute(action, root, "preparation-denied", role_source())
    elif mode == "partition":
        phase, kind, path = extra
        partition(root, results, phase, kind, Path(path))
    else:
        assert not extra
        verify(root, results, mode)


if __name__ == "__main__":
    main()
