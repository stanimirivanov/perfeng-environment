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


def sources(
    templates: dict[str, Any],
    sql: dict[str, Any],
    intent: dict[str, Any],
    *,
    retained_admission: bool = False,
) -> Any:
    """Validate unchanged published declarations and retain their exact identities."""
    paths = (
        (*SOURCES, "release-runtime-grants-v2.yaml", "release-schema-migration-v2.yaml")
        if retained_admission
        else SOURCES
    )
    expected = [yaml.safe_load((parent.DIRECTORY / name).read_text()) for name in paths]
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
    contention: bool = False,
    rollback: bool = False,
    diagnostic: str = "",
    retained_admission: bool = False,
    qualification: bool = False,
) -> dict[str, Any]:
    """Require actual native stages, authority, provenance and fixed owner Job."""
    assert not qualification or (retained_admission and not any((denied, contention, rollback)))
    operation = "qualify" if qualification else "grant"
    leaf = "schema-qualification" if qualification else "runtime-grants"
    parent_template = (
        "perfeng-release-schema-qualification-v1"
        if qualification
        else "perfeng-release-runtime-grants-v2"
        if retained_admission
        else "perfeng-release-runtime-grants-v1"
    )
    assert workflow["metadata"]["namespace"] == "argo-workflows"
    assert workflow["spec"]["workflowTemplateRef"] == {"name": parent_template}
    assert not workflow["spec"].get("shutdown")
    assert sum((denied, contention, rollback)) <= 1
    sql_failure = contention or rollback
    failed_job = denied or sql_failure
    assert not retained_admission or not failed_job
    assert workflow["status"]["phase"] in ({"Failed", "Error"} if failed_job else {"Succeeded"})
    args = workflow["spec"]["arguments"]["parameters"]
    assert len(args) == 4 and {p["name"]: p["value"] for p in args} == parameters
    named, outputs = parent.maintenance.readiness.named, parent.maintenance.readiness.outputs
    sequence = [
        named(workflow, name, "Steps") if name == "preflight" else named(workflow, name)
        for name in ("preflight", "task-selection", "migration-intent", "quiescence", operation)
    ]
    for node, reference, template in zip(
        sequence,
        (
            "perfeng-release-preflight-v3",
            "perfeng-release-task-selection-v3",
            "perfeng-release-schema-migration-v1",
            "perfeng-verify-release-quiescence-v3",
            "perfeng-" + leaf + "-job-v1",
        ),
        ("preflight", "run", "migration-intent", "run", "run"),
        strict=True,
    ):
        assert node["phase"] in (
            {"Failed", "Error"} if failed_job and node is sequence[-1] else {"Succeeded"}
        )
        assert node["templateRef"] == {"name": reference, "template": template}
    if retained_admission:
        ordered = [*sequence[:3]]
        for name in (
            "candidate",
            "pointer",
            "receipt",
            "accepted-bytes",
            "quiescence",
            "receipt-stable",
            "admission",
        ):
            node = named(workflow, name)
            if name != "quiescence":
                assert node["templateRef"] == {
                    "name": "perfeng-release-schema-migration-v2",
                    "template": name,
                }
            assert node["phase"] == "Succeeded"
            ordered.append(node)
        parent.ordered([*ordered, sequence[-1]])
    else:
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
    expected_nodes = {
        "validate-schema",
        "cluster-uid",
        "verify-identities",
        "task-selection",
        "migration-intent",
        "quiescence",
        operation,
    }
    if retained_admission:
        expected_nodes |= {
            "candidate",
            "pointer",
            "receipt",
            "accepted-bytes",
            "receipt-stable",
            "admission",
        }
    assert len(nodes) == len(expected_nodes) and {n["displayName"] for n in nodes} == expected_nodes
    assert len(workers) == len(nodes) and len({w["uid"] for w in workers}) == len(workers)
    assert {w["nodeID"] for w in workers} == {n["id"] for n in nodes}
    for node in nodes:
        failed = failed_job and node["displayName"] == "grant"
        worker = next(w for w in workers if w["nodeID"] == node["id"])
        assert worker["uid"] and worker["account"] == "release-runtime-grants"
        assert worker["exitCode"] == (1 if failed else 0)
        assert node["phase"] in ({"Failed", "Error"} if failed else {"Succeeded"})
    uid = workflow["metadata"]["uid"]
    assert uid and not workflow["metadata"].get("deletionTimestamp")
    expected_name = (
        "perfeng-schema-qualify-" if qualification else "perfeng-runtime-grants-"
    ) + uid
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
    manifest = yaml.safe_load((parent.DIRECTORY / (leaf + "-job-v1.yaml")).read_text())["spec"][
        "templates"
    ][0]["resource"]["manifest"]
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
    state, other = ("failed", "succeeded") if sql_failure else ("succeeded", "failed")
    assert job["status"].get(state) == 1 and not job["status"].get(other)
    assert any(
        c["type"] == ("Failed" if sql_failure else "Complete")
        and c["status"] == "True"
        and (not sql_failure or c["reason"] == "BackoffLimitExceeded")
        for c in job["status"]["conditions"]
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
    assert pod["status"]["phase"] == ("Failed" if sql_failure else "Succeeded")
    statuses = pod["status"]["containerStatuses"]
    assert len(statuses) == 1 and statuses[0]["name"] == operation
    assert (
        statuses[0]["state"]["terminated"]["exitCode"] == (1 if sql_failure else 0)
        and statuses[0].get("restartCount", 0) == 0
    )
    values = {
        p["name"]: p["value"]
        for p in workflow["status"]["nodes"][workflow["metadata"]["name"]]
        .get("outputs", {})
        .get("parameters", [])
        if "value" in p
    }
    if sql_failure:
        assert statuses[0]["state"]["terminated"]["reason"] == "Error"
        assert not values.get("checkpoint") and not values.get("job-name")
    else:
        assert (
            json.loads(values["checkpoint"]) == checkpoint and values["job-name"] == expected_name
        )
    return {
        "decision": "schema-qualification-parent-verified"
        if qualification
        else "owner-grant-rollback-refused"
        if rollback
        else "owner-grant-contention-refused"
        if contention
        else "owner-grant-parent-verified",
        "workflowUID": uid,
        "jobUID": job["metadata"]["uid"],
        "requestDigest": parameters["request-sha256"],
        **({"parentTemplate": parent_template} if retained_admission else {}),
    }


def delete_drift(first: dict[str, Any], current: dict[str, Any]) -> None:
    """Allow exactly one added DELETE bit on the existing owner-granted runs entry."""
    normalized = deepcopy(current)
    tables = [table for table in normalized["tables"] if table["name"] == "runs"]
    assert len(tables) == 1
    entries = tables[0]["acl"]
    indices = [i for i, entry in enumerate(entries) if entry.startswith("perfeng_runtime=")]
    assert len(indices) == 1
    index = indices[0]
    prefix, suffix = "perfeng_runtime=", "/perfeng_migration"
    entry = entries[index]
    assert entry.endswith(suffix)
    flags = entry.removeprefix(prefix).removesuffix(suffix)
    assert flags.count("d") == 1
    original_flags = flags.replace("d", "")
    assert len(original_flags) == 3 and set(original_flags) == {"a", "r", "w"}
    entries[index] = prefix + original_flags + suffix
    assert normalized == first


def verify_case(root: Path, label: str, *, before_only: bool = False) -> dict[str, Any] | None:
    """Replay immutable input, native execution and retained-state evidence."""
    assert label in {"denied", "contention", "rollback", "first", "repeat", "corrected"}

    def load(name: str) -> Any:
        return json.loads((root / name).read_text())

    def installed(label: str) -> Any:
        return sources(
            load(f"grant-{label}.templates.json"),
            load(f"grant-{label}.sql.json"),
            load(f"grant-{label}.intent.json"),
        )

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
    if label == "corrected":
        delete_drift(load("grant-first-after.acl.json"), load("grant-corrected-before.acl.json"))
    if before_only:
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
    if label in {"denied", "contention", "rollback"}:
        assert (root / f"grant-{label}-before.acl.json").read_bytes() == (
            root / f"grant-{label}-after.acl.json"
        ).read_bytes()
    if label == "corrected":
        assert (root / "grant-corrected-after.acl.json").read_bytes() == (
            root / "grant-first-after.acl.json"
        ).read_bytes()
    if label == "denied":
        assert install.inventory(load("grant-denied-before.jobs.json")) == install.inventory(
            load("grant-denied-after.jobs.json")
        )
    if label in {"contention", "rollback"}:
        before_jobs = install.inventory(load(f"grant-{label}-before.jobs.json"))
        after_jobs = load(f"grant-{label}-after.jobs.json")
        expected_job = load(f"grant-{label}.job.json")
        assert install.inventory(after_jobs) == {
            **before_jobs,
            **install.inventory({"items": [expected_job]}),
        }
        assert len(after_jobs["items"]) == len(before_jobs) + 1
    report = execution(
        load(f"grant-{label}.workflow.json"),
        load(f"grant-{label}.workers.json"),
        parameters,
        request,
        resources,
        load(f"grant-{label}.job.json"),
        load(f"grant-{label}.pods.json"),
        denied=label == "denied",
        contention=label == "contention",
        rollback=label == "rollback",
        diagnostic=(root / "grant-denied.log").read_text() if label == "denied" else "",
    )
    return report


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
            retained_admission=label.startswith("adopted") or label == "retained-v2",
        )

    if mode in {"retained", "retained-adopted"}:
        adopted = mode == "retained-adopted"
        job_labels = ["contention", "rollback", "first", "repeat", "corrected"]
        labels = ["denied", *job_labels]
        if adopted:
            job_labels.append("adopted")
            labels.extend(("adopted-null", "adopted"))
        jobs = load("grant-retained.jobs.json")["items"]
        originals = [load(f"grant-{label}.job.json") for label in job_labels]
        assert (
            len(jobs)
            == len(originals)
            == len({j["metadata"]["uid"] for j in originals})
            == (6 if adopted else 5)
        )
        assert {j["metadata"]["uid"] for j in jobs} == {j["metadata"]["uid"] for j in originals}
        for job in originals:
            parent.retained_job(
                job,
                next(j for j in jobs if j["metadata"]["uid"] == job["metadata"]["uid"]),
                failed=job["metadata"]["uid"] in {j["metadata"]["uid"] for j in originals[:2]},
            )
        workflows = [load(f"grant-{label}.workflow.json") for label in labels]
        assert len({w["metadata"]["uid"] for w in workflows}) == (8 if adopted else 6)
        for label, workflow in zip(labels, workflows, strict=True):
            current = load(f"grant-{label}.retained-workflow.json")
            assert install.inventory({"items": [workflow]}) == install.inventory(
                {"items": [current]}
            )
            assert current["status"]["phase"] == workflow["status"]["phase"]
            assert current["status"]["phase"] in (
                {"Failed", "Error"}
                if label in {"denied", "contention", "rollback", "adopted-null"}
                else {"Succeeded"}
            )
            assert (
                installed(label + "-before")
                == installed(label + "-after")
                == installed("retained-v2" if label.startswith("adopted") else "retained")
            )
        assert (
            (root / "grant-first-after.acl.json").read_bytes()
            == (root / "grant-repeat-before.acl.json").read_bytes()
            == (root / "grant-repeat-after.acl.json").read_bytes()
            == (root / "grant-corrected-after.acl.json").read_bytes()
        )
        assert verify_case(root, "corrected") == load("grant-corrected.verified.json")
        # Only bounded, validated identifiers reach shared artifacts, after all phase gates.
        for label in labels:
            report = load(f"grant-{label}.verified.json")
            (results / f"grant-parent-{label}.outcome.json").write_text(json.dumps(report) + "\n")
        return
    action, label = mode.split("-", 1)
    assert action in {"before", "verify"} and label in {
        "denied",
        "contention",
        "rollback",
        "first",
        "repeat",
        "corrected",
    }
    report = verify_case(root, label, before_only=action == "before")
    if report is not None:
        (root / f"grant-{label}.verified.json").write_text(json.dumps(report) + "\n")


if __name__ == "__main__":
    main()
