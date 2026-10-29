"""CI evidence for unchanged owner-only grants on paused bootstrap resources."""

import json
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import schema_migration_install as install
import schema_migration_parent as parent
import yaml
from jsonschema import Draft202012Validator

SOURCES = (
    "release-runtime-grants-v1.yaml",
    "runtime-grants-job-v1.yaml",
    *parent.SOURCES,
)


def sources(templates: dict[str, Any], sql: dict[str, Any], intent: dict[str, Any]) -> Any:
    """Validate unchanged published declarations and retain their exact identities."""
    expected = [yaml.safe_load((parent.DIRECTORY / name).read_text()) for name in SOURCES]
    actual = {obj["metadata"]["name"]: obj for obj in templates["items"]}
    assert len(actual) == len(templates["items"]) == len(expected)
    assert set(actual) == {obj["metadata"]["name"] for obj in expected}
    for obj in expected:
        current = actual[obj["metadata"]["name"]]
        assert current["spec"] == obj["spec"]
        assert current["metadata"]["namespace"] == "argo-workflows"
    for name, current in (
        ("runtime-grants-sql-v1.yaml", sql),
        ("schema-migration-intent-v1.yaml", intent),
    ):
        expected_config = yaml.safe_load((parent.DIRECTORY / name).read_text())
        parent.subset(expected_config, current)
        assert current["data"] == expected_config["data"] and not current.get("binaryData")
        assert not current["metadata"].get("ownerReferences")
    return install.inventory({"items": [*templates["items"], sql, intent]})


def execution(
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    parameters: dict[str, str],
    request: dict[str, Any],
    resources: list[dict[str, Any]],
    job: dict[str, Any] | None,
    pods: dict[str, Any],
    *,
    denied: bool = False,
    diagnostic: str = "",
) -> dict[str, Any]:
    """Require actual native stages, authority, provenance and fixed owner Job."""
    assert workflow["metadata"]["namespace"] == "argo-workflows"
    assert workflow["spec"]["workflowTemplateRef"] == {"name": "perfeng-release-runtime-grants-v1"}
    assert not workflow["spec"].get("shutdown")
    assert workflow["status"]["phase"] in ({"Failed", "Error"} if denied else {"Succeeded"})
    args = workflow["spec"]["arguments"]["parameters"]
    assert len(args) == 4 and {p["name"]: p["value"] for p in args} == parameters
    named, outputs = parent.maintenance.readiness.named, parent.maintenance.readiness.outputs
    sequence = [
        named(workflow, name, "Steps") if name == "preflight" else named(workflow, name)
        for name in ("preflight", "task-selection", "migration-intent", "quiescence", "grant")
    ]
    for node, reference, template in zip(
        sequence,
        (
            "perfeng-release-preflight-v3",
            "perfeng-release-task-selection-v3",
            "perfeng-release-schema-migration-v1",
            "perfeng-verify-release-quiescence-v3",
            "perfeng-runtime-grants-job-v1",
        ),
        ("preflight", "run", "migration-intent", "run", "run"),
        strict=True,
    ):
        assert node["phase"] in (
            {"Failed", "Error"} if denied and node is sequence[-1] else {"Succeeded"}
        )
        assert node["templateRef"] == {"name": reference, "template": template}
    parent.ordered(sequence)
    result = outputs(sequence[0])["result"]
    checkpoint = outputs(sequence[3])["result"]
    for value, schema in (
        (result, "release-preflight-result-v1"),
        (checkpoint, "maintenance-checkpoint-v1"),
    ):
        Draft202012Validator(
            json.loads(Path(f"deployments/{schema}.schema.json").read_text())
        ).validate(value)
        assert all(
            value[k] == parameters[p]
            for k, p in (
                ("requestDigest", "request-sha256"),
                ("requestRevision", "request-revision"),
                ("selectionRevision", "selection-revision"),
            )
        )
        assert value["clusterUID"] == request["environment"]["clusterUID"]
    assert result["requestPath"] == parameters["request-path"]
    assert result["lockDigests"] == request["selection"]["lockDigests"]
    assert result["environment"] == request["environment"]["name"] == "perfeng-local"
    assert {(o["kind"], o["name"], o["uid"]) for o in checkpoint["resources"]} == {
        (o["kind"], o["metadata"]["name"], o["metadata"]["uid"]) for o in resources
    }
    nodes = [n for n in workflow["status"]["nodes"].values() if n["type"] == "Pod"]
    assert len(nodes) == 7 and {n["displayName"] for n in nodes} == {
        "validate-schema",
        "cluster-uid",
        "verify-identities",
        "task-selection",
        "migration-intent",
        "quiescence",
        "grant",
    }
    assert len(workers) == len(nodes) and len({w["uid"] for w in workers}) == len(workers)
    assert {w["nodeID"] for w in workers} == {n["id"] for n in nodes}
    for node in nodes:
        failed = denied and node["displayName"] == "grant"
        worker = next(w for w in workers if w["nodeID"] == node["id"])
        assert worker["uid"] and worker["account"] == "release-runtime-grants"
        assert worker["exitCode"] == (1 if failed else 0)
        assert node["phase"] in ({"Failed", "Error"} if failed else {"Succeeded"})
    uid = workflow["metadata"]["uid"]
    assert uid and not workflow["metadata"].get("deletionTimestamp")
    expected_name = "perfeng-runtime-grants-" + uid
    if denied:
        assert job is None and pods["items"] == []
        values = {
            p["name"]: p["value"]
            for p in workflow["status"]["nodes"][workflow["metadata"]["name"]]
            .get("outputs", {})
            .get("parameters", [])
            if "value" in p
        }
        assert not values.get("checkpoint") and not values.get("job-name")
        assert re.search(
            r'Error from server \(Forbidden\): [^\r\n]*jobs\.batch(?: "'
            + re.escape(expected_name)
            + r'")? is forbidden: User "system:serviceaccount:argo-workflows:release-runtime-grants" '
            r'cannot create resource "jobs" in API group "batch" in (?:the )?namespace "perf-platform"',
            diagnostic,
        )
        return {
            "decision": "owner-grant-job-create-refused",
            "workflowUID": uid,
            "requestDigest": parameters["request-sha256"],
        }
    assert isinstance(job, dict)
    manifest = yaml.safe_load((parent.DIRECTORY / "runtime-grants-job-v1.yaml").read_text())[
        "spec"
    ]["templates"][0]["resource"]["manifest"]
    for key, value in {"uid": uid, **{"parameters." + k: v for k, v in parameters.items()}}.items():
        manifest = manifest.replace("{{workflow." + key + "}}", value)
    expected = yaml.safe_load(manifest)
    parent.subset(expected, job)
    assert job["metadata"]["uid"] and not job["metadata"].get("ownerReferences")
    assert not job["metadata"].get("deletionTimestamp")
    assert "ttlSecondsAfterFinished" not in job["spec"]
    assert job["spec"].get("parallelism", 1) == job["spec"].get("completions", 1) == 1
    assert not job["spec"].get("suspend")
    assert (
        datetime.fromisoformat(sequence[4]["startedAt"])
        <= datetime.fromisoformat(job["metadata"]["creationTimestamp"])
        <= datetime.fromisoformat(sequence[4]["finishedAt"])
    )
    assert job["status"].get("succeeded") == 1 and not job["status"].get("failed")
    assert any(
        c["type"] == "Complete" and c["status"] == "True" for c in job["status"]["conditions"]
    )
    assert len(pods["items"]) == 1
    pod = pods["items"][0]
    assert pod["metadata"]["uid"] and pod["metadata"]["namespace"] == "perf-platform"
    assert not pod["metadata"].get("deletionTimestamp")
    assert any(
        o["kind"] == "Job"
        and o["uid"] == job["metadata"]["uid"]
        and o["name"] == expected_name
        and o["controller"]
        for o in pod["metadata"]["ownerReferences"]
    )
    expected_pod = deepcopy(expected["spec"]["template"]["spec"])
    tolerations = expected_pod.pop("tolerations")
    defaults = [
        {
            "key": "node.kubernetes.io/" + key,
            "operator": "Exists",
            "effect": "NoExecute",
            "tolerationSeconds": 300,
        }
        for key in ("not-ready", "unreachable")
    ]
    defaults.append(
        {"key": "node.kubernetes.io/memory-pressure", "operator": "Exists", "effect": "NoSchedule"}
    )
    for spec in (job["spec"]["template"]["spec"], pod["spec"]):
        parent.subset(expected_pod, spec)
        assert all(t in spec["tolerations"] for t in tolerations)
        assert all(t in tolerations + defaults for t in spec["tolerations"])
        assert not any(
            spec.get(k)
            for k in (
                "initContainers",
                "ephemeralContainers",
                "hostNetwork",
                "hostPID",
                "hostIPC",
                "imagePullSecrets",
            )
        )
        container = spec["containers"][0]
        assert not any(
            container.get(k)
            for k in (
                "envFrom",
                "workingDir",
                "lifecycle",
                "livenessProbe",
                "readinessProbe",
                "startupProbe",
            )
        )
        security = container["securityContext"]
        assert not security.get("privileged") and not security["capabilities"].get("add")
        assert (
            security.get("runAsNonRoot", True)
            and security.get("runAsUser", 999) == security.get("runAsGroup", 999) == 999
        )
        assert security.get("procMount", "Default") == "Default"
    assert pod["status"]["phase"] == "Succeeded"
    statuses = pod["status"]["containerStatuses"]
    assert len(statuses) == 1 and statuses[0]["name"] == "grant"
    assert (
        statuses[0]["state"]["terminated"]["exitCode"] == 0
        and statuses[0].get("restartCount", 0) == 0
    )
    values = {
        p["name"]: p["value"]
        for p in workflow["status"]["nodes"][workflow["metadata"]["name"]]["outputs"]["parameters"]
        if "value" in p
    }
    assert json.loads(values["checkpoint"]) == checkpoint and values["job-name"] == expected_name
    return {
        "decision": "owner-grant-parent-verified",
        "workflowUID": uid,
        "jobUID": job["metadata"]["uid"],
        "requestDigest": parameters["request-sha256"],
    }


def main() -> None:
    mode, directory, output = sys.argv[1:]
    root, results = Path(directory), Path(output)

    def load(name: str) -> Any:
        return json.loads((root / name).read_text())

    def installed(label: str) -> Any:
        return sources(
            load(f"grant-{label}.templates.json"),
            load(f"grant-{label}.sql.json"),
            load(f"grant-{label}.intent.json"),
        )

    if mode == "retained":
        jobs = load("grant-retained.jobs.json")["items"]
        originals = [load(f"grant-{label}.job.json") for label in ("first", "repeat")]
        assert len(jobs) == 2 and len({j["metadata"]["uid"] for j in originals}) == 2
        assert {j["metadata"]["uid"] for j in jobs} == {j["metadata"]["uid"] for j in originals}
        for job in originals:
            parent.retained_job(
                job, next(j for j in jobs if j["metadata"]["uid"] == job["metadata"]["uid"])
            )
        labels = ("denied", "first", "repeat")
        workflows = [load(f"grant-{label}.workflow.json") for label in labels]
        assert len({w["metadata"]["uid"] for w in workflows}) == 3
        for label, workflow in zip(labels, workflows, strict=True):
            current = load(f"grant-{label}.retained-workflow.json")
            assert install.inventory({"items": [workflow]}) == install.inventory(
                {"items": [current]}
            )
            assert current["status"]["phase"] == workflow["status"]["phase"]
            assert current["status"]["phase"] in (
                {"Failed", "Error"} if label == "denied" else {"Succeeded"}
            )
            assert (
                installed(label + "-before") == installed(label + "-after") == installed("retained")
            )
        assert (
            (root / "grant-first-after.acl.json").read_bytes()
            == (root / "grant-repeat-before.acl.json").read_bytes()
            == (root / "grant-repeat-after.acl.json").read_bytes()
        )
        # Only bounded, validated identifiers reach shared artifacts, after all phase gates.
        for label in labels:
            report = load(f"grant-{label}.verified.json")
            (results / f"grant-parent-{label}.outcome.json").write_text(json.dumps(report) + "\n")
        return
    action, label = mode.split("-", 1)
    assert action in {"before", "verify"} and label in {"denied", "first", "repeat"}
    parameters, request = install.request_input(root)
    before = "grant-" + label + "-before"
    resources = install.baseline(
        load(f"schema-install.{before}.objects.json"),
        load(f"schema-install.{before}.applications.json"),
        load(f"schema-install.{before}.pods.json"),
    )
    identities = installed(label + "-before")
    assert (
        load(f"schema-install.{before}.namespace.json")["metadata"]["uid"]
        == request["environment"]["clusterUID"]
    )
    parent.migration.validate_schema(
        load(f"grant-{label}-before.schema.json"),
        json.loads(Path("tests/integration/release-workflows/migration-image.json").read_text()),
    )
    if action == "before":
        return
    assert identities == installed(label + "-after")
    after = "grant-" + label + "-after"
    install.baseline(
        load(f"schema-install.{after}.objects.json"),
        load(f"schema-install.{after}.applications.json"),
        load(f"schema-install.{after}.pods.json"),
    )
    for suffix in ("objects", "applications", "pods", "pvs"):
        assert install.inventory(
            load(f"schema-install.{before}.{suffix}.json")
        ) == install.inventory(load(f"schema-install.{after}.{suffix}.json"))
    for suffix in ("namespace", "roles"):
        a, b = (
            load(f"schema-install.{before}.{suffix}.json"),
            load(f"schema-install.{after}.{suffix}.json"),
        )
        assert (a["metadata"]["uid"] == b["metadata"]["uid"]) if suffix == "namespace" else a == b
    for suffix in ("schema.json", "data.txt", "sentinel.txt"):
        assert (root / f"grant-{label}-before.{suffix}").read_bytes() == (
            root / f"grant-{label}-after.{suffix}"
        ).read_bytes()
    if label == "denied":
        assert (root / "grant-denied-before.acl.json").read_bytes() == (
            root / "grant-denied-after.acl.json"
        ).read_bytes()
        assert install.inventory(load("grant-denied-before.jobs.json")) == install.inventory(
            load("grant-denied-after.jobs.json")
        )
    report = execution(
        load(f"grant-{label}.workflow.json"),
        load(f"grant-{label}.workers.json"),
        parameters,
        request,
        resources,
        load(f"grant-{label}.job.json"),
        load(f"grant-{label}.pods.json"),
        denied=label == "denied",
        diagnostic=(root / "grant-denied.log").read_text() if label == "denied" else "",
    )
    (root / f"grant-{label}.verified.json").write_text(json.dumps(report) + "\n")


if __name__ == "__main__":
    main()
