"""Accept unchanged owner grants on real adopted resources while recovery is blocked."""

import base64
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import runtime_grants_parent as grants
import yaml
from jsonschema import Draft202012Validator

STATE_FIELDS = (
    "applications",
    "deployment",
    "namespace",
    "server",
    "workloads",
    "pods",
    "records",
    "secrets",
    "config",
    "git",
)
PERSISTENT = (
    *(
        f"postgres.{field}.json"
        for field in (
            "storage",
            "pv",
            "service",
            "statefulset",
            "external",
            "server",
            "repository",
            "roles",
            "acl",
            "schema",
        )
    ),
    "postgres.canary.txt",
    "postgres.sentinel.txt",
    *(
        f"seaweedfs.{field}.json"
        for field in ("storage", "pv", "service", "proxy", "statefulset", "credentials", "object")
    ),
    "seaweedfs.bytes",
)


def state(root: Path, prefix: str) -> dict[str, Any]:
    result = {key: json.loads((root / f"{prefix}.{key}.json").read_text()) for key in STATE_FIELDS}
    result["serverPod"] = json.loads((root / f"{prefix}.server-pods.json").read_text())
    return result


def request(root: Path, before: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    parameters = json.loads((root / "upgrade-request-parameters.json").read_text())
    assert set(parameters) == {
        "request-revision",
        "request-path",
        "request-sha256",
        "selection-revision",
    }
    assert re.fullmatch(r"[0-9a-f]{40}", parameters["request-revision"])
    assert (
        parameters["request-revision"]
        == json.loads((root / "request-ref.json").read_text())["commit"]
    )
    raw = (root / "upgrade-request-request.json").read_bytes()
    assert parameters["request-sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    candidate = json.loads(raw)
    Draft202012Validator(
        json.loads(Path("deployments/release-request-v1.schema.json").read_text())
    ).validate(candidate)
    accepted_raw = (root / "parent-request.json").read_bytes()
    accepted = json.loads(accepted_raw)
    digest = "sha256:" + hashlib.sha256(accepted_raw).hexdigest()
    assert candidate == {
        **accepted,
        "promotion": {
            **accepted["promotion"],
            "expectedCurrent": digest,
            "migration": {"mode": "forward-only", "id": "0018"},
        },
    }
    assert candidate["selection"]["revision"] == parameters["selection-revision"]
    assert len(before["records"]) == 2
    pointer = next(o for o in before["records"] if o["metadata"]["name"] == "perfeng-local-current")
    receipt = next(
        o for o in before["records"] if o["metadata"]["name"] == pointer["data"]["receiptName"]
    )
    assert pointer["data"]["requestDigest"] == candidate["promotion"]["expectedCurrent"] == digest
    assert (
        pointer["data"]["clusterUID"]
        == before["namespace"]["metadata"]["uid"]
        == candidate["environment"]["clusterUID"]
    )
    assert pointer["data"]["receiptUID"] == receipt["metadata"]["uid"]
    assert (
        pointer["metadata"]["uid"] and receipt["metadata"]["uid"] and receipt["immutable"] is True
    )
    for obj in (pointer, receipt):
        assert obj["metadata"]["namespace"] == "perfeng-release-ledger" and not obj["metadata"].get(
            "deletionTimestamp"
        )
    assert base64.b64decode(receipt["binaryData"]["request.json"], validate=True) == accepted_raw
    record = json.loads(base64.b64decode(receipt["binaryData"]["receipt.json"], validate=True))
    assert record["schemaVersion"] == 1 and record["status"] == "adopted-baseline"
    assert record["kind"] == "PerfengReleaseAdoptionReceipt" and record["requestDigest"] == digest
    assert record["environment"] == candidate["environment"]
    assert record["selectionRevision"] == candidate["selection"]["revision"]
    applications = {o["metadata"]["name"]: o for o in before["applications"]["items"]}
    assert record["rootUID"] == applications["perfeng-local-root"]["metadata"]["uid"]
    assert (
        record["controlPlaneApplicationUID"]
        == applications["perfeng-local-control-plane"]["metadata"]["uid"]
    )
    assert (
        record["image"]
        == before["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"]
    )
    return parameters, candidate


def paused(root: Path, label: str) -> dict[str, Any]:
    prefix = "grant-adopted-" + label
    observed = state(root, prefix)
    grants.parent.maintenance.retained(state(root, "maintenance-before"), observed, paused=True)
    deployment = observed["deployment"]
    server = next(o for o in observed["server"] if o["kind"] == "Deployment")
    assert deployment["metadata"]["uid"] == server["uid"] and deployment["spec"] == server["spec"]
    assert deployment["spec"]["replicas"] == 0 and observed["serverPod"]["items"] == []
    assert deployment["status"]["observedGeneration"] == deployment["metadata"]["generation"]
    assert all(
        deployment["status"].get(k, 0) == 0
        for k in ("replicas", "readyReplicas", "updatedReplicas", "availableReplicas")
    )
    assert (
        deployment["spec"]["template"]["spec"]["containers"][0]["name"] != "never-started-fixture"
    )
    for suffix in PERSISTENT:
        assert (root / (prefix + "." + suffix)).read_bytes() == (
            root / ("schema-parent-baseline." + suffix)
        ).read_bytes()
    for suffix in ("schema.json", "roles.json", "data.txt"):
        assert (root / (prefix + "." + suffix)).read_bytes() == (
            root / ("schema-parent-baseline." + suffix)
        ).read_bytes()
    return observed


def main() -> None:
    mode, directory, output = sys.argv[1:]
    root, results = Path(directory), Path(output)
    assert mode in {"before", "verify", "complete", "retained"}

    def load(name: str) -> Any:
        return json.loads((root / name).read_text())

    def sources(label: str) -> Any:
        return grants.sources(
            load(f"grant-adopted-{label}.templates.json"),
            load(f"grant-adopted-{label}.sql.json"),
            load(f"grant-adopted-{label}.intent.json"),
        )

    before = paused(root, "before")
    parameters, candidate = request(root, before)
    original_sources = sources("before")
    grants.parent.migration.validate_schema(
        load("grant-adopted-before.schema.json"),
        json.loads(Path("tests/integration/release-workflows/migration-image.json").read_text()),
    )
    if mode == "before":
        return
    after = paused(root, "after")
    assert sources("after") == original_sources
    for suffix in ("acl.json", "schema.json", "roles.json", "data.txt"):
        assert (root / f"grant-adopted-before.{suffix}").read_bytes() == (
            root / f"grant-adopted-after.{suffix}"
        ).read_bytes()
    report = grants.execution(
        load("grant-adopted.workflow.json"),
        load("grant-adopted.workers.json"),
        parameters,
        candidate,
        [*after["applications"]["items"], after["deployment"]],
        load("grant-adopted.job.json"),
        load("grant-adopted.pods.json"),
    )
    if mode == "verify":
        (root / "grant-adopted.verified.json").write_text(json.dumps(report) + "\n")
        return
    assert report == load("grant-adopted.verified.json")
    paused(root, "observed")
    assert sources("observed") == original_sources
    assert (root / "grant-adopted-before.acl.json").read_bytes() == (
        root / "grant-adopted-observed.acl.json"
    ).read_bytes()
    prefix = results / "runtime-grants-adopted"
    workflow = json.loads(prefix.with_suffix(".workflow.json").read_text())
    job = json.loads(prefix.with_suffix(".job.json").read_text())
    worker = json.loads(prefix.with_suffix(".worker.json").read_text())
    source = yaml.safe_load(
        Path("tests/integration/release-workflows/runtime-grants-workflow.yaml").read_text()
    )
    grants.parent.subset(source["spec"], workflow["spec"])
    manifest = source["spec"]["templates"][0]["resource"]["manifest"].replace(
        "{{workflow.name}}", workflow["metadata"]["name"]
    )
    grants.parent.subset(yaml.safe_load(manifest), job)
    assert workflow["metadata"]["uid"] and job["metadata"]["uid"]
    assert workflow["metadata"]["namespace"] == "argo-workflows"
    assert not any(o["metadata"].get("deletionTimestamp") for o in (workflow, job))
    assert workflow["status"]["phase"] == "Succeeded" and job["status"]["succeeded"] == 1
    assert len(worker) == 1 and worker[0]["uid"]
    assert worker[0]["containers"] == [{"name": "probe", "exitCode": 0, "waitingReason": None}]
    assert "runtime privilege policy verified" in (root / "grant-adopted.probe.private").read_text()
    if mode == "retained":
        grants.parent.retained_job(
            load("grant-adopted.job.json"), load("grant-adopted.current-job.json")
        )
        original = load("grant-adopted.workflow.json")
        current_parent = load("grant-adopted.current-workflow.json")
        assert grants.install.inventory({"items": [original]}) == grants.install.inventory(
            {"items": [current_parent]}
        )
        assert current_parent["status"]["phase"] == "Succeeded"
        grants.parent.retained_job(job, load("grant-adopted.probe-retained-job.json"))
        current = load("grant-adopted.probe-retained-workflow.json")
        assert grants.install.inventory({"items": [workflow]}) == grants.install.inventory(
            {"items": [current]}
        )
        assert current["status"]["phase"] == "Succeeded"
