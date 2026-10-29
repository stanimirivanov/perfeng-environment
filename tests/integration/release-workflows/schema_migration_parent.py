"""Evidence for repeat execution of the unchanged parent on the paused CI baseline."""

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
LABELS = ("none", "denied", "first", "repeat")
SOURCES = (
    "release-schema-migration-v1.yaml",
    "schema-migration-job-v1.yaml",
    "release-preflight-v3.yaml",
    "release-task-selection-v3.yaml",
    "verify-release-quiescence-v3.yaml",
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


def installed(templates: dict[str, Any], intent: dict[str, Any]) -> dict[str, str]:
    expected = [yaml.safe_load((DIRECTORY / name).read_text()) for name in SOURCES]
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
    assert request["promotion"]["expectedCurrent"] == pointer["data"]["requestDigest"] == digest
    accepted = json.loads(accepted_raw)
    assert request == {
        **accepted,
        "promotion": {
            **accepted["promotion"],
            "expectedCurrent": digest,
            "migration": {"mode": "none"}
            if label == "none"
            else {"mode": "forward-only", "id": "0018"},
        },
    }
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
    )


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
) -> dict[str, Any]:
    """Validate the unchanged native parent independently of adopted/empty baseline."""
    assert label in {*LABELS, "install", "contention"}
    assert workflow["metadata"]["namespace"] == "argo-workflows" and workflow["metadata"]["uid"]
    assert workflow["spec"]["workflowTemplateRef"] == {
        "name": "perfeng-release-schema-migration-v1"
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
    if label != "none":
        expected_pods |= {"quiescence", "migrate"}
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
        refused = label == "none" and node["displayName"] == "migration-intent"
        denied = label in {"denied", "contention"} and node["displayName"] == "migrate"
        assert worker["exitCode"] == (2 if refused else 1 if denied else 0)
        assert node["phase"] in (
            {"Failed", "Error"} if denied else {"Failed" if refused else "Succeeded"}
        )
    sequence = [preflight, selection, intent_node]
    parent = nodes[workflow["metadata"]["name"]]
    values = {
        p["name"]: p["value"]
        for p in parent.get("outputs", {}).get("parameters", [])
        if "value" in p
    }
    report = {
        "decision": "schema-parent-intent-refused"
        if label == "none"
        else "schema-parent-job-create-refused"
        if label == "denied"
        else "schema-parent-contention-refused"
        if label == "contention"
        else "schema-parent-install-verified"
        if label == "install"
        else "schema-parent-repeat-verified",
        "workflowUID": workflow["metadata"]["uid"],
        "requestDigest": parameters["request-sha256"],
    }
    if label == "none":
        assert workflow["status"]["phase"] in {"Failed", "Error"}
        assert job is None and pods["items"] == []
        assert not values.get("job-name") and not values.get("checkpoint")
        for node in nodes.values():
            if node["displayName"] in {"quiescence", "migrate"}:
                assert node["type"] != "Pod" and node["phase"] in {"Skipped", "Omitted"}
    else:
        assert workflow["status"]["phase"] in (
            {"Failed", "Error"} if label in {"denied", "contention"} else {"Succeeded"}
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
        if label == "contention":
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
        if label == "contention":
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
            "Failed" if label == "contention" else "Succeeded"
        )
        assert len(pod["status"]["containerStatuses"]) == 1
        assert pod["status"]["containerStatuses"][0]["name"] == "migrate"
        assert pod["status"]["containerStatuses"][0]["state"]["terminated"]["exitCode"] == (
            1 if label == "contention" else 0
        )
        sequence += [checkpoint_node, migrate]
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
        )
        if label == "after":
            assert current == installed(
                load("schema-parent.templates-before.json"),
                load("schema-parent.intent-before.json"),
            )
        return
    if mode.startswith("diagnose-"):
        label = mode.removeprefix("diagnose-")
        assert label in LABELS
        job = load(f"schema-parent-{label}.job.json")
        report: dict[str, Any]
        if job and label != "none":
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
            for name in ("preflight", "task-selection", "migration-intent", "quiescence", "migrate")
            for n in workflow.get("status", {}).get("nodes", {}).values()
            if n.get("displayName") == name
        ]
        (results / f"schema-parent-{label}.startup.json").write_text(json.dumps(report) + "\n")
        return
    assert mode in {*LABELS, "complete", "baseline"}
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
        path = fixture / "schema-contention.job.json"
        failed = json.loads(path.read_text()) if path.exists() else None
        if failed and original["metadata"]["uid"] == failed["metadata"]["uid"]:
            retained_job(failed, original, failed=True)
            retained_job(failed, current, failed=True)
        else:
            retained_job(original, current)

    def check(label: str) -> dict[str, Any]:
        prefix = "schema-parent-" + label
        key = "migration-none" if label == "none" else "upgrade-request"
        parameters = load(key + "-parameters.json")
        accepted = (fixture / "parent-request.json").read_bytes()
        raw = (fixture / (key + "-request.json")).read_bytes()
        base = load("parent-parameters.json")
        assert (
            parameters["request-revision"]
            == base["request-revision"]
            == load("request-ref.json")["commit"]
        )
        assert parameters["selection-revision"] == base["selection-revision"]
        report = verify(
            label,
            load(prefix + ".workflow.json"),
            load(prefix + ".workers.json"),
            parameters,
            raw,
            accepted,
            state("maintenance-before"),
            state(prefix),
            load(prefix + ".job.json"),
            load(prefix + ".job-pods.json"),
            (fixture / (prefix + ".denied.log")).read_text() if label == "denied" else "",
        )
        for suffix in ("schema.json", "roles.json"):
            assert load(prefix + "." + suffix) == load("schema-parent-baseline." + suffix)
        assert (fixture / f"{prefix}.data.txt").read_bytes() == (
            fixture / "schema-parent-baseline.data.txt"
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
            0 if label in {"none", "denied"} else 1 if label == "first" else 2
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

    if mode != "complete":
        (results / f"schema-parent-{mode}.outcome.json").write_text(json.dumps(check(mode)) + "\n")
        return
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
    (results / "schema-parent-complete.outcome.json").write_text(
        json.dumps({"decision": "schema-parent-repeat-suite-verified", "cases": reports}) + "\n"
    )


if __name__ == "__main__":
    main()
