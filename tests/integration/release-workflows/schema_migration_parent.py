"""Evidence for repeat execution of the unchanged parent on the paused CI baseline."""

import base64
import hashlib
import json
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import maintenance_pause_evidence as maintenance
import migration_image_evidence as migration
import yaml
from jsonschema import Draft202012Validator

DIRECTORY = Path("gitops/release-workflows")
LABELS = ("none", "denied", "current-null", "first", "repeat")
SOURCES = (
    "release-schema-migration-v1.yaml",
    "schema-migration-job-v1.yaml",
    "release-preflight-v3.yaml",
    "release-task-selection-v3.yaml",
    "verify-release-quiescence-v3.yaml",
)
PERSISTENT = (
    *(
        f"postgres.{key}.json"
        for key in (
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
        f"seaweedfs.{key}.json"
        for key in ("storage", "pv", "service", "proxy", "statefulset", "credentials", "object")
    ),
    "seaweedfs.bytes",
)


def subset(expected: Any, actual: Any) -> None:
    """Match every declared field, allowing server-added defaults, not extra list entries."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        for key, value in expected.items():
            subset(value, actual[key])
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(expected) == len(actual)
        for value, observed in zip(expected, actual, strict=True):
            subset(value, observed)
    else:
        assert expected == actual


def installed(
    templates: dict[str, Any], intent: dict[str, Any], *, retained_admission: bool = False
) -> dict[str, str]:
    sources = (*SOURCES, "release-schema-migration-v2.yaml") if retained_admission else SOURCES
    expected = [yaml.safe_load((DIRECTORY / name).read_text()) for name in sources]
    actual = {obj["metadata"]["name"]: obj for obj in templates["items"]}
    assert set(actual) == {obj["metadata"]["name"] for obj in expected}
    identities = {}
    for obj in expected:
        current = actual[obj["metadata"]["name"]]
        assert current["spec"] == obj["spec"]
        assert current["metadata"]["namespace"] == "argo-workflows"
        identities[obj["metadata"]["name"]] = current["metadata"]["uid"]
        assert identities[obj["metadata"]["name"]]
    config = yaml.safe_load((DIRECTORY / "schema-migration-intent-v1.yaml").read_text())
    subset(config, intent)
    assert intent["data"] == config["data"] and not intent["metadata"].get("ownerReferences")
    assert intent["metadata"]["uid"]
    identities[config["metadata"]["name"]] = intent["metadata"]["uid"]
    return identities


def verify(
    label: str,
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    parameters: dict[str, str],
    raw: bytes,
    accepted_raw: bytes,
    before: dict[str, Any],
    after: dict[str, Any],
    job: Any,
    pods: dict[str, Any],
    diagnostic: str = "",
    *,
    retained_admission: bool = False,
) -> dict[str, Any]:
    assert label in LABELS
    maintenance.retained(before, after, paused=True)
    assert after["serverPod"]["items"] == [] and after["deployment"]["spec"]["replicas"] == 0
    status = after["deployment"]["status"]
    assert status["observedGeneration"] == after["deployment"]["metadata"]["generation"]
    assert all(
        status.get(k, 0) == 0
        for k in ("replicas", "readyReplicas", "updatedReplicas", "availableReplicas")
    )
    assert parameters["request-sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    request = json.loads(raw)
    Draft202012Validator(
        json.loads(Path("deployments/release-request-v1.schema.json").read_text())
    ).validate(request)
    intent = json.loads(
        yaml.safe_load((DIRECTORY / "schema-migration-intent-v1.yaml").read_text())["data"][
            "schema-migration-intent-v1.schema.json"
        ]
    )
    assert Draft202012Validator(intent).is_valid(request) == (label != "none")
    digest = "sha256:" + hashlib.sha256(accepted_raw).hexdigest()
    pointer = next(o for o in before["records"] if o["metadata"]["name"] == "perfeng-local-current")
    assert pointer["data"]["requestDigest"] == digest
    assert request["promotion"]["expectedCurrent"] == (None if label == "current-null" else digest)
    accepted = json.loads(accepted_raw)
    assert request == {
        **accepted,
        "promotion": {
            **accepted["promotion"],
            "expectedCurrent": None if label == "current-null" else digest,
            "migration": {"mode": "none"}
            if label == "none"
            else {"mode": "forward-only", "id": "0018"},
        },
    }
    if label == "current-null":
        assert retained_admission and raw == accepted_raw
    if retained_admission:
        assert label in {"current-null", "first", "repeat"}
        admission_records(accepted_raw, request, after)
        if label != "current-null":
            admission_nodes(workflow, raw, after)
    return execution(
        label,
        workflow,
        workers,
        parameters,
        request,
        before["namespace"]["metadata"]["uid"],
        [*after["applications"]["items"], after["deployment"]],
        job,
        pods,
        diagnostic,
        retained_admission=retained_admission,
    )


def stable_record(value: dict[str, Any]) -> dict[str, Any]:
    """Ignore API bookkeeping while retaining all ledger identity/content/provenance."""
    result = deepcopy(value)
    for key in ("resourceVersion", "managedFields"):
        result["metadata"].pop(key, None)
    return result


def admission_records(raw: bytes, request: dict[str, Any], state: dict[str, Any]) -> None:
    """Independently bind the accepted bytes/record to actual adopted paused resources."""
    policy = maintenance.policy
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    name = "receipt." + digest[7:39] + "." + digest[39:]
    records = {o["metadata"]["name"]: o for o in state["records"]}
    assert len(records) == len(state["records"]) == 2
    pointer, receipt = records[policy.POINTER], records[name]
    policy.owned(pointer, policy.POINTER, "current-release")
    policy.owned(receipt, name, "release-adoption")
    assert pointer["data"] == {
        "requestDigest": digest,
        "clusterUID": request["environment"]["clusterUID"],
        "receiptName": name,
        "receiptUID": receipt["metadata"]["uid"],
    }
    assert pointer.get("immutable", False) is False and not pointer.get("binaryData")
    assert receipt["immutable"] is True and not receipt.get("data")
    annotations = receipt["metadata"]["annotations"]
    assert re.fullmatch(
        r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", annotations["perfeng.io/first-workflow-uid"]
    )
    assert re.fullmatch(r"[0-9a-f]{40}", annotations["perfeng.io/first-request-revision"])
    assert set(receipt["binaryData"]) == {"request.json", "receipt.json"}
    assert base64.b64decode(receipt["binaryData"]["request.json"], validate=True) == raw
    record = json.loads(
        base64.b64decode(receipt["binaryData"]["receipt.json"], validate=True),
        object_pairs_hook=policy.unique,
    )
    applications = {o["metadata"]["name"]: o for o in state["applications"]["items"]}
    lock = json.loads(Path("deployments/local-control-plane.json").read_text())
    assert record == {
        "schemaVersion": 1,
        "kind": "PerfengReleaseAdoptionReceipt",
        "status": "adopted-baseline",
        "environment": request["environment"],
        "requestDigest": digest,
        "selectionRevision": request["selection"]["revision"],
        "rootUID": applications[policy.NAMES[0]]["metadata"]["uid"],
        "controlPlaneApplicationUID": applications[policy.NAMES[1]]["metadata"]["uid"],
        "deploymentRevision": lock["deployment"]["revision"],
        "image": lock["controlPlane"]["image"],
    }
    assert (
        state["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"] == record["image"]
    )


def admission_nodes(workflow: dict[str, Any], raw: bytes, state: dict[str, Any]) -> None:
    """Inspect actual v2 outputs; no projection of a v2 execution into legacy v1."""
    named, outputs = maintenance.readiness.named, maintenance.readiness.outputs
    candidate = named(workflow, "candidate")
    values = [p["value"] for p in candidate["outputs"]["parameters"] if p["name"] == "request"]
    assert len(values) == 1 and values[0] in {raw.decode(), raw.decode().removesuffix("\n")}
    records = {o["metadata"]["name"]: o for o in state["records"]}
    pointer = outputs(named(workflow, "pointer"))["observation"]
    receipt = outputs(named(workflow, "receipt"))["observation"]
    stable = outputs(named(workflow, "receipt-stable"))["observation"]
    for obj in (pointer, receipt, stable):
        assert stable_record(obj) == stable_record(records[obj["metadata"]["name"]])
    assert receipt["metadata"]["uid"] == pointer["data"]["receiptUID"]
    assert outputs(named(workflow, "admission"))["observation"] == {
        "status": "retained-baseline-verified"
    }


def ledger_authority(value: dict[str, Any]) -> dict[str, Any]:
    """Require exactly the reviewed installed get-only grant, including stable UIDs."""
    expected = list(
        yaml.safe_load_all(
            Path(
                "gitops/release-workflows-bootstrap/schema-migration-ledger-access.yaml"
            ).read_text()
        )
    )
    actual = {o["kind"]: o for o in value["items"]}
    assert len(actual) == len(value["items"]) == 2
    for obj in expected:
        current = actual[obj["kind"]]
        subset(obj, current)
        metadata = current["metadata"]
        assert (
            metadata["uid"]
            and not metadata.get("deletionTimestamp")
            and not metadata.get("ownerReferences")
        )
        for key in ("rules", "subjects", "roleRef"):
            assert current.get(key) == obj.get(key)
    return {kind: stable_record(obj) for kind, obj in actual.items()}


def execution(
    label: str,
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    parameters: dict[str, str],
    request: dict[str, Any],
    cluster_uid: str,
    resources: list[dict[str, Any]],
    job: Any,
    pods: dict[str, Any],
    diagnostic: str = "",
    *,
    retained_admission: bool = False,
) -> dict[str, Any]:
    """Validate the unchanged native parent independently of adopted/empty baseline."""
    assert label in {*LABELS, "install", "contention", "rollback"}
    if retained_admission:
        assert label in {"current-null", "first", "repeat"}
    assert workflow["metadata"]["namespace"] == "argo-workflows" and workflow["metadata"]["uid"]
    assert workflow["spec"]["workflowTemplateRef"] == {
        "name": "perfeng-release-schema-migration-v2"
        if retained_admission
        else "perfeng-release-schema-migration-v1"
    }
    assert not workflow["spec"].get("shutdown")
    arguments = workflow["spec"]["arguments"]["parameters"]
    assert len(arguments) == 4 and {p["name"]: p["value"] for p in arguments} == parameters
    named, outputs = maintenance.readiness.named, maintenance.readiness.outputs
    preflight = named(workflow, "preflight", "Steps")
    result = outputs(preflight)["result"]
    Draft202012Validator(
        json.loads(Path("deployments/release-preflight-result-v1.schema.json").read_text())
    ).validate(result)
    assert result["requestDigest"] == parameters["request-sha256"]
    assert result["requestRevision"] == parameters["request-revision"]
    assert result["requestPath"] == parameters["request-path"]
    assert (
        result["selectionRevision"]
        == parameters["selection-revision"]
        == request["selection"]["revision"]
    )
    assert result["clusterUID"] == cluster_uid == request["environment"]["clusterUID"]
    assert result["lockDigests"] == request["selection"]["lockDigests"]
    assert result["environment"] == request["environment"]["name"] == "perfeng-local"
    selection, intent_node = (
        named(workflow, name) for name in ("task-selection", "migration-intent")
    )
    assert preflight["phase"] == selection["phase"] == "Succeeded"
    assert preflight["templateRef"] == {
        "name": "perfeng-release-preflight-v3",
        "template": "preflight",
    }
    assert selection["templateRef"] == {
        "name": "perfeng-release-task-selection-v3",
        "template": "run",
    }
    if retained_admission:
        assert intent_node["templateRef"] == {
            "name": "perfeng-release-schema-migration-v1",
            "template": "migration-intent",
        }
    else:
        assert label != "current-null"
        assert intent_node["templateName"] == "migration-intent"
    assert intent_node["phase"] == ("Failed" if label == "none" else "Succeeded")
    nodes = workflow["status"]["nodes"]
    expected_pods = {
        "validate-schema",
        "cluster-uid",
        "verify-identities",
        "task-selection",
        "migration-intent",
    }
    if label not in {"none", "current-null"}:
        expected_pods |= {"quiescence", "migrate"}
    if retained_admission:
        expected_pods |= {"candidate"}
        if label != "current-null":
            expected_pods |= {"pointer", "receipt", "accepted-bytes", "receipt-stable", "admission"}
    pod_nodes = [n for n in nodes.values() if n["type"] == "Pod"]
    assert {n["displayName"] for n in pod_nodes} == expected_pods and len(pod_nodes) == len(
        expected_pods
    )
    assert len(workers) == len(pod_nodes) and {w["nodeID"] for w in workers} == {
        n["id"] for n in pod_nodes
    }
    assert len({w["uid"] for w in workers}) == len(workers)
    for node in pod_nodes:
        worker = next(w for w in workers if w["nodeID"] == node["id"])
        assert worker["uid"] and worker["account"] == "release-schema-migration"
        refused = (label == "none" and node["displayName"] == "migration-intent") or (
            label == "current-null" and node["displayName"] == "candidate"
        )
        denied = label in {"denied", "contention", "rollback"} and node["displayName"] == "migrate"
        assert worker["exitCode"] == (2 if refused else 1 if denied else 0)
        # A rejected candidate never creates its declared output file. Argo may
        # therefore report output collection Error after the exact main exit 2.
        candidate_refusal = label == "current-null" and node["displayName"] == "candidate"
        assert node["phase"] in (
            {"Failed", "Error"}
            if denied or candidate_refusal
            else {"Failed" if refused else "Succeeded"}
        )
    sequence = [preflight, selection, intent_node]
    if retained_admission:
        for name in (
            ("candidate",)
            if label == "current-null"
            else ("candidate", "pointer", "receipt", "accepted-bytes")
        ):
            node = named(workflow, name)
            assert node["templateName"] == name and not node.get("templateRef")
            sequence.append(node)
    parent = nodes[workflow["metadata"]["name"]]
    values = {
        p["name"]: p["value"]
        for p in parent.get("outputs", {}).get("parameters", [])
        if "value" in p
    }
    report = {
        "decision": "schema-parent-intent-refused"
        if label == "none"
        else "schema-parent-null-current-refused"
        if label == "current-null"
        else "schema-parent-job-create-refused"
        if label == "denied"
        else "schema-parent-rollback-refused"
        if label == "rollback"
        else "schema-parent-contention-refused"
        if label == "contention"
        else "schema-parent-install-verified"
        if label == "install"
        else "schema-parent-repeat-verified",
        "workflowUID": workflow["metadata"]["uid"],
        "parentTemplate": workflow["spec"]["workflowTemplateRef"]["name"],
        "requestDigest": parameters["request-sha256"],
    }
    if label in {"none", "current-null"}:
        assert workflow["status"]["phase"] in {"Failed", "Error"}
        assert job is None and pods["items"] == []
        assert not values.get("job-name") and not values.get("checkpoint")
        for node in nodes.values():
            if node["displayName"] in {
                "pointer",
                "receipt",
                "accepted-bytes",
                "quiescence",
                "receipt-stable",
                "admission",
                "migrate",
            }:
                assert node["type"] != "Pod" and node["phase"] in {"Skipped", "Omitted"}
    else:
        assert workflow["status"]["phase"] in (
            {"Failed", "Error"} if label in {"denied", "contention", "rollback"} else {"Succeeded"}
        )
        checkpoint_node, migrate = (named(workflow, name) for name in ("quiescence", "migrate"))
        assert checkpoint_node["templateRef"] == {
            "name": "perfeng-verify-release-quiescence-v3",
            "template": "run",
        }
        assert migrate["templateRef"] == {
            "name": "perfeng-schema-migration-job-v1",
            "template": "run",
        }
        checkpoint = outputs(checkpoint_node)["result"]
        if retained_admission:
            sequence.append(checkpoint_node)
            for name in ("receipt-stable", "admission"):
                node = named(workflow, name)
                assert node["templateName"] == name and not node.get("templateRef")
                sequence.append(node)
        Draft202012Validator(
            json.loads(Path("deployments/maintenance-checkpoint-v1.schema.json").read_text())
        ).validate(checkpoint)
        assert all(
            checkpoint[k] == result[k]
            for k in ("requestRevision", "requestDigest", "selectionRevision", "clusterUID")
        )
        assert {(o["kind"], o["name"], o["uid"]) for o in checkpoint["resources"]} == {
            (o["kind"], o["metadata"]["name"], o["metadata"]["uid"]) for o in resources
        }
        expected_name = "perfeng-schema-" + workflow["metadata"]["uid"]
        if label == "denied":
            assert job is None and pods["items"] == []
            assert not values.get("job-name") and not values.get("checkpoint")
            # Only the failed native resource worker's main log is supplied here.
            assert re.search(
                r'Error from server \(Forbidden\): [^\r\n]*jobs\.batch(?: "'
                + re.escape(expected_name)
                + r'")? is forbidden: User "system:serviceaccount:argo-workflows:release-schema-migration" '
                r'cannot create resource "jobs" in API group "batch" in (?:the )?namespace "perf-platform"',
                diagnostic,
            )
            sequence += [checkpoint_node, migrate]
            ordered(sequence)
            return report
        if label in {"contention", "rollback"}:
            assert not values.get("job-name") and not values.get("checkpoint")
        else:
            assert (
                values["job-name"] == expected_name
                and json.loads(values["checkpoint"]) == checkpoint
            )
        manifest = yaml.safe_load((DIRECTORY / "schema-migration-job-v1.yaml").read_text())["spec"][
            "templates"
        ][0]["resource"]["manifest"]
        for key, value in {
            "uid": workflow["metadata"]["uid"],
            **{"parameters." + k: v for k, v in parameters.items()},
        }.items():
            manifest = manifest.replace("{{workflow." + key + "}}", value)
        subset(yaml.safe_load(manifest), job)
        assert job["metadata"]["uid"] and not job["metadata"].get("ownerReferences")
        created = datetime.fromisoformat(job["metadata"]["creationTimestamp"])
        assert (
            datetime.fromisoformat(migrate["startedAt"])
            <= created
            <= datetime.fromisoformat(migrate["finishedAt"])
        )
        if label in {"contention", "rollback"}:
            assert job["status"].get("failed") == 1 and not job["status"].get("succeeded")
            assert any(
                c["type"] == "Failed"
                and c["status"] == "True"
                and c["reason"] == "BackoffLimitExceeded"
                for c in job["status"]["conditions"]
            )
        else:
            assert job["status"].get("succeeded") == 1 and not job["status"].get("failed")
            assert any(
                c["type"] == "Complete" and c["status"] == "True"
                for c in job["status"]["conditions"]
            )
        pod_spec = job["spec"]["template"]["spec"]
        assert not any(
            pod_spec.get(k)
            for k in (
                "initContainers",
                "ephemeralContainers",
                "volumes",
                "hostNetwork",
                "hostPID",
                "hostIPC",
            )
        )
        assert len(pods["items"]) == 1
        pod = pods["items"][0]
        assert pod["metadata"]["namespace"] == job["metadata"]["namespace"] == "perf-platform"
        assert any(
            o["kind"] == "Job"
            and o["uid"] == job["metadata"]["uid"]
            and o["name"] == expected_name
            and o["controller"]
            for o in pod["metadata"]["ownerReferences"]
        )
        expected_pod = deepcopy(pod_spec)
        declared_tolerations = expected_pod.pop("tolerations")
        subset(expected_pod, pod["spec"])
        observed_tolerations = pod["spec"].get("tolerations", [])
        assert all(t in observed_tolerations for t in declared_tolerations)
        defaults = [
            {
                "key": "node.kubernetes.io/" + key,
                "operator": "Exists",
                "effect": "NoExecute",
                "tolerationSeconds": 300,
            }
            for key in ("not-ready", "unreachable")
        ]
        # TaintNodesByCondition admits this for the declared Burstable resources.
        defaults.append(
            {
                "key": "node.kubernetes.io/memory-pressure",
                "operator": "Exists",
                "effect": "NoSchedule",
            }
        )
        assert all(t in declared_tolerations or t in defaults for t in observed_tolerations)
        assert len({json.dumps(t, sort_keys=True) for t in observed_tolerations}) == len(
            observed_tolerations
        )
        assert not any(
            pod["spec"].get(k)
            for k in (
                "initContainers",
                "ephemeralContainers",
                "volumes",
                "hostNetwork",
                "hostPID",
                "hostIPC",
            )
        )
        for current in (pod_spec, pod["spec"]):
            container = current["containers"][0]
            assert not any(
                container.get(k)
                for k in (
                    "args",
                    "workingDir",
                    "envFrom",
                    "volumeMounts",
                    "lifecycle",
                    "livenessProbe",
                    "readinessProbe",
                    "startupProbe",
                )
            )
            security = container["securityContext"]
            assert not security.get("privileged", False)
            assert security.get("runAsNonRoot", True) is True
            assert security.get("runAsUser", 65532) == security.get("runAsGroup", 65532) == 65532
            assert not security["capabilities"].get("add", [])
            assert security.get("procMount", "Default") == "Default"
            assert not container["env"][0]["valueFrom"]["secretKeyRef"].get("optional", False)
        assert pod["metadata"]["uid"] and pod["status"]["phase"] == (
            "Failed" if label in {"contention", "rollback"} else "Succeeded"
        )
        assert len(pod["status"]["containerStatuses"]) == 1
        assert pod["status"]["containerStatuses"][0]["name"] == "migrate"
        assert pod["status"]["containerStatuses"][0]["state"]["terminated"]["exitCode"] == (
            1 if label in {"contention", "rollback"} else 0
        )
        sequence += [migrate] if retained_admission else [checkpoint_node, migrate]
        report["jobUID"] = job["metadata"]["uid"]
    ordered(sequence)
    return report


def ordered(sequence: list[dict[str, Any]]) -> None:
    """Require each completed stage before the next begins."""
    for node in sequence:
        start, finish = (datetime.fromisoformat(node[k]) for k in ("startedAt", "finishedAt"))
        assert start.utcoffset() is not None and start <= finish
    for earlier, later in zip(sequence, sequence[1:], strict=False):
        assert datetime.fromisoformat(earlier["finishedAt"]) <= datetime.fromisoformat(
            later["startedAt"]
        )


def retained_job(
    original: dict[str, Any], current: dict[str, Any], *, failed: bool = False
) -> None:
    """Retain native Job identity, declaration and provenance through controller refreshes."""
    assert current["spec"] == original["spec"]
    assert {
        k: v
        for k, v in current["metadata"].items()
        if k not in {"resourceVersion", "managedFields"}
    } == {
        k: v
        for k, v in original["metadata"].items()
        if k not in {"resourceVersion", "managedFields"}
    }
    key, other = ("failed", "succeeded") if failed else ("succeeded", "failed")
    assert current["status"][key] == original["status"][key] == 1
    assert not current["status"].get(other) and not original["status"].get(other)
    if failed:
        assert any(
            c["type"] == "Failed"
            and c["status"] == "True"
            and c["reason"] == "BackoffLimitExceeded"
            for c in current["status"]["conditions"]
        )


def main() -> None:
    mode, directory, output = sys.argv[1:]
    fixture, results = Path(directory), Path(output)

    def load(name: str) -> Any:
        return json.loads((fixture / name).read_text())

    def state(prefix: str) -> dict[str, Any]:
        return {
            key: load(prefix + "." + suffix + ".json")
            for key, suffix in (
                ("serverPod", "server-pods"),
                *(
                    (k, k)
                    for k in (
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
                ),
            )
        }

    if mode.startswith("installed-"):
        label = mode.removeprefix("installed-")
        assert label in {"before", "after"}
        current = installed(
            load(f"schema-parent.templates-{label}.json"),
            load(f"schema-parent.intent-{label}.json"),
            retained_admission=True,
        )
        authority = ledger_authority(load(f"schema-parent.ledger-{label}.json"))
        if label == "after":
            assert current == installed(
                load("schema-parent.templates-before.json"),
                load("schema-parent.intent-before.json"),
                retained_admission=True,
            )
            assert authority == ledger_authority(load("schema-parent.ledger-before.json"))
        return
    if mode.startswith("diagnose-"):
        label = mode.removeprefix("diagnose-")
        assert label in LABELS
        job = load(f"schema-parent-{label}.job.json")
        report: dict[str, Any]
        if job and label not in {"none", "current-null"}:
            report = migration.startup(
                (fixture / f"schema-parent-{label}.watch.json").read_text(),
                job,
                json.loads(
                    Path("tests/integration/release-workflows/migration-image.json").read_text()
                ),
                int((fixture / f"schema-parent-{label}.watch-code.txt").read_text()),
                int((fixture / f"schema-parent-{label}.wait-code.txt").read_text()),
            )
        else:
            report = {"purpose": "diagnostic-only", "job": "unexpected" if job else "absent"}
        text = (fixture / f"schema-parent-{label}.failures.log").read_text(errors="replace").lower()
        report["failureSignals"] = [
            name
            for name, pattern in {
                "permission-denied": r"permission denied|operation not permitted",
                "file-missing": r"no such file|cannot open|could not open",
                "authorization": r"unauthorized|forbidden|authentication failed|authentication required",
                "network": r"connection refused|no such host|i/o timeout|connection timed out|tls handshake",
                "schema-validation": r"at instance location|at evaluate path",
            }.items()
            if re.search(pattern, text)
        ]
        worker_pods = load(f"schema-parent-{label}.workflow-pods.json")
        report["workerWaitingReasons"] = sorted(
            {
                status["state"]["waiting"]["reason"]
                for pod in worker_pods["items"]
                for status in [
                    *pod.get("status", {}).get("containerStatuses", []),
                    *pod.get("status", {}).get("initContainerStatuses", []),
                ]
                if status.get("state", {}).get("waiting", {}).get("reason") in migration.WAITING
            }
        )
        workflow = load(f"schema-parent-{label}.workflow.json")
        report["stages"] = [
            {
                "name": name,
                "phase": n["phase"]
                if n.get("phase")
                in {"Pending", "Running", "Succeeded", "Failed", "Error", "Skipped", "Omitted"}
                else "Unknown",
            }
            for name in (
                "preflight",
                "task-selection",
                "migration-intent",
                "candidate",
                "pointer",
                "receipt",
                "accepted-bytes",
                "quiescence",
                "receipt-stable",
                "admission",
                "migrate",
            )
            for n in workflow.get("status", {}).get("nodes", {}).values()
            if n.get("displayName") == name
        ]
        (results / f"schema-parent-{label}.startup.json").write_text(json.dumps(report) + "\n")
        return
    assert mode in {*LABELS, "complete", "final", "baseline"}
    maintenance.retained(state("maintenance-before"), state("schema-parent-baseline"), paused=True)
    contract = json.loads(
        Path("tests/integration/release-workflows/migration-image.json").read_text()
    )
    selected = json.loads(Path("deployments/local-control-plane.json").read_text())["controlPlane"]
    assert (contract["image"], contract["sourceRevision"]) == (
        selected["image"],
        selected["sourceRevision"],
    )
    migration.validate_schema(load("schema-parent-baseline.schema.json"), contract)
    if mode == "baseline":
        return

    def retain_baseline(original: dict[str, Any], current: dict[str, Any]) -> None:
        for label in ("contention", "rollback"):
            path = fixture / f"schema-{label}.job.json"
            failed = json.loads(path.read_text()) if path.exists() else None
            if failed and original["metadata"]["uid"] == failed["metadata"]["uid"]:
                retained_job(failed, original, failed=True)
                retained_job(failed, current, failed=True)
                return
        retained_job(original, current)

    def check(label: str, workflow: dict[str, Any] | None = None) -> dict[str, Any]:
        prefix = "schema-parent-" + label
        key = (
            "migration-none"
            if label == "none"
            else "parent"
            if label == "current-null"
            else "upgrade-request"
        )
        parameters = load(key + "-parameters.json")
        accepted = (fixture / "parent-request.json").read_bytes()
        raw = (
            accepted
            if label == "current-null"
            else (fixture / (key + "-request.json")).read_bytes()
        )
        base = load("parent-parameters.json")
        assert (
            parameters["request-revision"]
            == base["request-revision"]
            == load("request-ref.json")["commit"]
        )
        assert parameters["selection-revision"] == base["selection-revision"]
        report = verify(
            label,
            workflow if workflow is not None else load(prefix + ".workflow.json"),
            load(prefix + ".workers.json"),
            parameters,
            raw,
            accepted,
            state("maintenance-before"),
            state(prefix),
            load(prefix + ".job.json"),
            load(prefix + ".job-pods.json"),
            (fixture / (prefix + ".denied.log")).read_text() if label == "denied" else "",
            retained_admission=label in {"current-null", "first", "repeat"},
        )
        for suffix in ("schema.json", "roles.json"):
            assert load(prefix + "." + suffix) == load("schema-parent-baseline." + suffix)
        assert (fixture / f"{prefix}.data.txt").read_bytes() == (
            fixture / "schema-parent-baseline.data.txt"
        ).read_bytes()
        for suffix in PERSISTENT:
            assert (fixture / f"{prefix}.{suffix}").read_bytes() == (
                fixture / f"schema-parent-baseline.{suffix}"
            ).read_bytes()
        migration.validate_schema(
            load(prefix + ".schema.json"),
            json.loads(
                Path("tests/integration/release-workflows/migration-image.json").read_text()
            ),
        )
        previous_jobs = load(prefix + ".jobs-before.json")["items"]
        current_jobs = load(prefix + ".jobs-after.json")["items"]
        baseline_jobs = load("schema-parent-baseline.jobs.json")["items"]
        assert len(previous_jobs) == len(baseline_jobs) + (1 if label == "repeat" else 0)
        assert len(current_jobs) == len(baseline_jobs) + (
            0 if label in {"none", "denied", "current-null"} else 1 if label == "first" else 2
        )
        for original in baseline_jobs:
            current = next(
                o for o in current_jobs if o["metadata"]["uid"] == original["metadata"]["uid"]
            )
            retain_baseline(original, current)
        assert {o["metadata"]["uid"] for o in previous_jobs} <= {
            o["metadata"]["uid"] for o in current_jobs
        }
        return report

    if mode in LABELS:
        (results / f"schema-parent-{mode}.outcome.json").write_text(json.dumps(check(mode)) + "\n")
        return
    assert installed(
        load("schema-parent.templates-before.json"),
        load("schema-parent.intent-before.json"),
        retained_admission=True,
    ) == installed(
        load("schema-parent.templates-after.json"),
        load("schema-parent.intent-after.json"),
        retained_admission=True,
    )
    assert ledger_authority(load("schema-parent.ledger-before.json")) == ledger_authority(
        load("schema-parent.ledger-after.json")
    )
    reports = [check(label) for label in LABELS]
    assert len({r["workflowUID"] for r in reports}) == len(LABELS)
    jobs = load("schema-parent.retained-jobs.json")["items"]
    baseline_jobs = load("schema-parent-baseline.jobs.json")["items"]
    assert len(jobs) == 2 + len(baseline_jobs) and {o["metadata"]["uid"] for o in jobs} == {
        r["jobUID"] for r in reports if "jobUID" in r
    } | {o["metadata"]["uid"] for o in baseline_jobs}
    for original in baseline_jobs:
        retain_baseline(
            original, next(o for o in jobs if o["metadata"]["uid"] == original["metadata"]["uid"])
        )
    for label in ("first", "repeat"):
        original = load(f"schema-parent-{label}.job.json")
        current = next(o for o in jobs if o["metadata"]["uid"] == original["metadata"]["uid"])
        retain_baseline(original, current)
    retained = load("schema-parent.retained-workflows.json")["items"]
    assert len(retained) == len(LABELS) and {o["metadata"]["uid"] for o in retained} == {
        r["workflowUID"] for r in reports
    }
    for label in LABELS:
        original = load(f"schema-parent-{label}.workflow.json")
        current = next(o for o in retained if o["metadata"]["uid"] == original["metadata"]["uid"])
        assert (
            current["spec"] == original["spec"]
            and current["status"]["phase"] == original["status"]["phase"]
        )
        assert check(label, current) == next(
            r for r in reports if r["workflowUID"] == current["metadata"]["uid"]
        )
    (results / f"schema-parent-{mode}.outcome.json").write_text(
        json.dumps(
            {
                "decision": "schema-parent-final-retained"
                if mode == "final"
                else "schema-parent-repeat-suite-verified",
                "cases": reports,
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
