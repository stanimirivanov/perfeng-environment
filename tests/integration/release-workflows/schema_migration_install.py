"""Prove the complete parent's first install on explicit paused bootstrap objects."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import schema_migration_parent as parent
import server_inputs as server
import yaml
from jsonschema import Draft202012Validator


def inventory(objects: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result = {}
    for obj in objects["items"]:
        key = (obj["kind"], obj["metadata"]["name"])
        assert key not in result and obj["metadata"]["uid"]
        assert not obj["metadata"].get("deletionTimestamp")
        result[key] = {
            **{k: v for k, v in obj.items() if k not in {"metadata", "status"}},
            "metadata": {
                k: v
                for k, v in obj["metadata"].items()
                if k not in {"resourceVersion", "managedFields"}
            },
        }
    return result


def baseline(
    objects: dict[str, Any], applications: dict[str, Any], pods: dict[str, Any]
) -> list[dict[str, Any]]:
    deployment = next(
        o
        for o in objects["items"]
        if o["kind"] == "Deployment" and o["metadata"]["name"] == "control-plane"
    )
    assert deployment["spec"]["replicas"] == 0
    assert (
        deployment["spec"]["template"]["spec"]["containers"][0]["name"] == "never-started-fixture"
    )
    assert deployment["status"]["observedGeneration"] == deployment["metadata"]["generation"]
    assert all(
        deployment["status"].get(k, 0) == 0
        for k in ("replicas", "readyReplicas", "updatedReplicas", "availableReplicas")
    )
    assert len(applications["items"]) == 2
    assert {o["metadata"]["name"] for o in applications["items"]} == {
        "perfeng-local-root",
        "perfeng-local-control-plane",
    }
    for obj in applications["items"]:
        assert obj["spec"]["syncPolicy"]["automated"]["enabled"] is False
        assert not obj.get("operation") and obj["metadata"]["namespace"] == "argocd"
    assert pods["items"]
    for pod in pods["items"]:
        assert pod["status"]["phase"] == "Running"
        assert any(
            c["type"] == "Ready" and c["status"] == "True" for c in pod["status"]["conditions"]
        )
        assert (
            pod["metadata"].get("labels", {}).get("app.kubernetes.io/name")
            != "perfeng-control-plane"
        )
    return [*applications["items"], deployment]


def request_input(root: Path) -> tuple[dict[str, str], dict[str, Any]]:
    """Revalidate the existing raw first-install request and its delivery identity."""
    parameters = json.loads((root / "parent-parameters.json").read_text())
    assert (
        parameters["request-revision"]
        == json.loads((root / "request-ref.json").read_text())["commit"]
    )
    raw = (root / "parent-request.json").read_bytes()
    assert parameters["request-sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    request = json.loads(raw)
    Draft202012Validator(
        json.loads(Path("deployments/release-request-v1.schema.json").read_text())
    ).validate(request)
    policy = json.loads(
        yaml.safe_load((parent.DIRECTORY / "schema-migration-intent-v1.yaml").read_text())["data"][
            "schema-migration-intent-v1.schema.json"
        ]
    )
    Draft202012Validator(policy).validate(request)
    assert request["promotion"]["expectedCurrent"] is None
    return parameters, request


def main() -> None:
    mode, directory, output = sys.argv[1:]
    root, results = Path(directory), Path(output)

    def load(name: str) -> Any:
        return json.loads((root / name).read_text())

    if mode == "baseline-retained":
        jobs = load("schema-parent-baseline.jobs.json")["items"]
        originals = [
            load("schema-install.job.json"),
            load("schema-contention.job.json"),
            load("schema-rollback.job.json"),
        ]
        assert len(jobs) == 3 and {j["metadata"]["uid"] for j in jobs} == {
            j["metadata"]["uid"] for j in originals
        }
        for original in originals:
            current = next(j for j in jobs if j["metadata"]["uid"] == original["metadata"]["uid"])
            parent.retained_job(
                original,
                current,
                failed=original["metadata"]["uid"] in {j["metadata"]["uid"] for j in originals[1:]},
            )
        return
    if mode in {"diagnose", "diagnose-install", "diagnose-contention", "diagnose-rollback"}:
        prefix = (
            "schema-" + mode.removeprefix("diagnose-") if mode != "diagnose" else "schema-install"
        )
        job = load(f"{prefix}.job.json")
        stream = ""
        if job:
            with (root / f"{prefix}.watch.json").open(errors="replace", encoding="utf-8") as source:
                stream = source.read(4194305)
        report = (
            parent.migration.startup(
                stream[:4194304],
                job,
                json.loads(
                    Path("tests/integration/release-workflows/migration-image.json").read_text()
                ),
                int((root / f"{prefix}.watch-code.txt").read_text()),
                int((root / f"{prefix}.wait-code.txt").read_text()),
            )
            if job
            else {"purpose": "diagnostic-only", "job": "absent"}
        )
        if len(stream) > 4194304:
            report["limitations"].append("watch-size-limit")
        workflow = load(f"{prefix}.workflow.json")
        report["stages"] = [
            {"name": n["displayName"], "phase": n["phase"]}
            for n in workflow.get("status", {}).get("nodes", {}).values()
            if n.get("displayName")
            in {"preflight", "task-selection", "migration-intent", "quiescence", "migrate"}
            and n.get("phase")
            in {"Pending", "Running", "Succeeded", "Failed", "Error", "Skipped", "Omitted"}
        ]
        (results / f"{prefix}.startup.json").write_text(json.dumps(report) + "\n")
        return
    if mode == "pull":
        value = server.pull_secret(load("schema-install.pull-source.json"))
        (root / "schema-install.pull-create.json").write_text(json.dumps(value) + "\n")
        return
    if mode == "retained":
        parent.retained_job(
            load("schema-install.job.json"), load("schema-install.retained-job.json")
        )
        original, current = (
            load("schema-install.workflow.json"),
            load("schema-install.retained-workflow.json"),
        )
        assert original["metadata"]["uid"] == current["metadata"]["uid"]
        assert current["spec"] == original["spec"] and current["status"]["phase"] == "Succeeded"
        assert not current["metadata"].get("deletionTimestamp")
        later = load("schema-parent.retained-workflows.json")["items"]
        failed_workflow = load("schema-contention.workflow.json")
        rollback_workflow = load("schema-rollback.workflow.json")
        assert (
            len(later) == 4
            and len(
                {
                    o["metadata"]["uid"]
                    for o in [*later, current, failed_workflow, rollback_workflow]
                }
            )
            == 7
        )
        name = original["metadata"]["name"]
        assert (
            current["status"]["nodes"][name]["outputs"]
            == original["status"]["nodes"][name]["outputs"]
        )
        server.retained_pull(
            load("schema-install.pull-before.json"), load("schema-install.retained-pull.json")
        )
        (results / "schema-install-retention.outcome.json").write_text(
            json.dumps({"decision": "schema-install-retained", "jobUID": current_job_uid(root)})
            + "\n"
        )
        return
    assert mode in {"before", "verify"}
    pull = load("schema-install.pull-before.json")
    server.retained_pull(pull, pull)
    expected = server.pull_secret(load("schema-install.pull-source.json"))
    parent.subset(expected, pull)
    assert pull["data"] == expected["data"]
    assert load("schema-install.empty.json") == {
        "schemaAbsent": True,
        "applicationRelations": 0,
        "runtimeCreate": False,
        "migrationCreate": True,
    }
    resources = baseline(
        load("schema-install.before.objects.json"),
        load("schema-install.before.applications.json"),
        load("schema-install.before.pods.json"),
    )
    identities = parent.installed(
        load("schema-install.templates-before.json"), load("schema-install.intent-before.json")
    )
    assert load("schema-install.jobs-before.json")["items"] == []
    if mode == "before":
        return
    assert identities == parent.installed(
        load("schema-install.templates-after.json"), load("schema-install.intent-after.json")
    )
    for suffix in ("objects", "applications", "pods", "pvs"):
        assert inventory(load(f"schema-install.before.{suffix}.json")) == inventory(
            load(f"schema-install.after.{suffix}.json")
        )
    baseline(
        load("schema-install.after.objects.json"),
        load("schema-install.after.applications.json"),
        load("schema-install.after.pods.json"),
    )
    assert load("schema-install.before.roles.json") == load("schema-install.after.roles.json")
    assert (
        load("schema-install.before.namespace.json")["metadata"]["uid"]
        == load("schema-install.after.namespace.json")["metadata"]["uid"]
    )
    parameters, request = request_input(root)
    report = parent.execution(
        "install",
        load("schema-install.workflow.json"),
        load("schema-install.workers.json"),
        parameters,
        request,
        load("schema-install.before.namespace.json")["metadata"]["uid"],
        resources,
        load("schema-install.job.json"),
        load("schema-install.job-pods.json"),
    )
    parent.migration.validate_schema(
        json.loads((results / "migration-image-first.schema.json").read_text()),
        json.loads(Path("tests/integration/release-workflows/migration-image.json").read_text()),
    )
    jobs = load("schema-install.jobs-after.json")["items"]
    failures = [load("schema-contention.job.json"), load("schema-rollback.job.json")]
    assert len(jobs) == 3 and {o["metadata"]["uid"] for o in jobs} == {
        load("schema-install.job.json")["metadata"]["uid"],
        *[j["metadata"]["uid"] for j in failures],
    }
    for failed in failures:
        parent.retained_job(
            failed,
            next(j for j in jobs if j["metadata"]["uid"] == failed["metadata"]["uid"]),
            failed=True,
        )
    (results / "schema-install.outcome.json").write_text(json.dumps(report) + "\n")


def current_job_uid(root: Path) -> str:
    return json.loads((root / "schema-install.retained-job.json").read_text())["metadata"]["uid"]


if __name__ == "__main__":
    main()
