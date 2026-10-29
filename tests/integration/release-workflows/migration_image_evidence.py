"""Verify migration evidence and project private native-watch startup observations."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
WAITING = {
    "ContainerCreating",
    "PodInitializing",
    "ErrImagePull",
    "ImagePullBackOff",
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",
    "CrashLoopBackOff",
    "InvalidImageName",
    "ImageInspectError",
}


def startup(stream: str, job: dict, contract: dict, watch_code: int, wait_code: int) -> dict:
    """Retain bounded owned-Pod states; never emit messages, specs or URLs."""
    metadata = job["metadata"]
    job_uid = metadata["uid"]
    assert UUID.fullmatch(job_uid) and metadata["namespace"] == "perf-platform"
    container = job["spec"]["template"]["spec"]["containers"]
    assert len(container) == 1 and container[0]["name"] == "migrate"
    assert container[0]["image"] == contract["image"]
    assert container[0]["command"] == ["/usr/local/bin/migrate"]
    report = {
        "schemaVersion": 1,
        "purpose": "diagnostic-only",
        "jobUID": job_uid,
        "nativeWaitExitCode": wait_code,
        "watch": "complete"
        if watch_code == 0
        else "interrupted"
        if watch_code in {124, 137, 143}
        else "failed",
        "limitations": [],
        "observations": [],
    }
    limits, observations = report["limitations"], report["observations"]
    # Kubernetes may put registry URLs or credentials in messages. Only fixed
    # signals derived from them may cross the private fixture/artifact boundary.
    patterns = {
        "authorization": r"unauthorized|forbidden|authentication required|status code (401|403)",
        "manifest-missing": r"manifest_unknown|name_unknown|manifest unknown|404 not found",
        "tls": r"x509|tls handshake|certificate signed by unknown authority",
        "network": r"no such host|connection refused|i/o timeout|context deadline exceeded|network is unreachable",
    }

    def pod_observation(pod: dict) -> None:
        meta = pod.get("metadata", {})
        if meta.get("namespace") != "perf-platform" or not any(
            owner.get("kind") == "Job"
            and owner.get("uid") == job_uid
            and owner.get("name") == metadata["name"]
            and owner.get("controller") is True
            for owner in meta.get("ownerReferences", [])
        ):
            return
        uid = meta.get("uid", "")
        if not isinstance(uid, str) or not UUID.fullmatch(uid):
            limits.append("invalid-pod-identity")
            return
        status = pod.get("status", {})
        phase = status.get("phase")
        value = {
            "podUID": uid,
            "phase": phase
            if phase in {"Pending", "Running", "Succeeded", "Failed", "Unknown"}
            else "Other",
            "scheduled": bool(pod.get("spec", {}).get("nodeName")),
            "schedulingReason": None,
            "containerState": "absent",
            "waitingReason": None,
            "exitCode": None,
            "signals": [],
        }
        conditions = [c for c in status.get("conditions", []) if c.get("type") == "PodScheduled"]
        if conditions and conditions[0].get("status") == "False":
            reason = conditions[0].get("reason")
            value["schedulingReason"] = (
                reason if reason in {"Unschedulable", "SchedulingGated"} else "Other"
            )
        containers = [c for c in status.get("containerStatuses", []) if c.get("name") == "migrate"]
        if len(containers) == 1:
            state = containers[0].get("state", {})
            if "waiting" in state:
                reason = state["waiting"].get("reason")
                value["containerState"] = "waiting"
                value["waitingReason"] = reason if reason in WAITING else "Other"
                message = state["waiting"].get("message", "")
                if isinstance(message, str):
                    value["signals"] = [
                        name
                        for name, pattern in patterns.items()
                        if re.search(pattern, message, re.I)
                    ]
            elif "running" in state:
                value["containerState"] = "running"
            elif "terminated" in state:
                value["containerState"] = "terminated"
                code = state["terminated"].get("exitCode")
                value["exitCode"] = code if type(code) is int and 0 <= code <= 255 else None
        if value not in observations:
            if len(observations) < 128:
                observations.append(value)
            else:
                limits.append("observation-limit")

    decoder = json.JSONDecoder()
    position = 0
    while position < len(stream):
        if stream[position].isspace():
            position += 1
            continue
        try:
            event, position = decoder.raw_decode(stream, position)
        except json.JSONDecodeError:
            limits.append("incomplete-or-invalid-stream")
            break
        try:
            if not isinstance(event, dict):
                limits.append("invalid-watch-event")
            elif event.get("type") == "ERROR":
                limits.append("api-watch-error")
            elif event.get("type") in {"ADDED", "MODIFIED", "DELETED"}:
                pod = event["object"]
                if pod.get("kind") == "Pod":
                    pod_observation(pod)
            elif event.get("type") != "BOOKMARK":
                limits.append("unknown-watch-event")
        except (KeyError, TypeError, AttributeError):
            limits.append("invalid-watch-event")
    if not observations:
        limits.append("no-owned-pod-observed")
    report["limitations"] = sorted(set(limits))
    return report


def validate_schema(snapshot: dict, contract: dict) -> None:
    assert snapshot["schemaOwner"] == "perfeng_migration"
    assert isinstance(snapshot["schemaOID"], str) and snapshot["schemaOID"].isdigit()
    relations = snapshot["relations"]
    assert relations and all(
        r["owner"] == "perfeng_migration" and r["oid"].isdigit() for r in relations
    )
    assert (
        len({r["oid"] for r in relations}) == len({r["name"] for r in relations}) == len(relations)
    )
    assert "schema_migrations" in {r["name"] for r in relations}
    expected = contract["migrations"]
    assert len(expected) == 18
    assert [int(name[:4]) for name in sorted(expected)] == list(range(1, 19))
    assert all(
        re.fullmatch(r"[0-9]{4}_[a-z0-9_]+\.sql", name) and re.fullmatch(r"[0-9a-f]{64}", digest)
        for name, digest in expected.items()
    )
    ledger = snapshot["ledger"]
    assert len(ledger) == len(expected)
    assert {row["version"]: row["sha256"] for row in ledger} == expected
    assert all(isinstance(row["appliedAt"], str) and row["appliedAt"] for row in ledger)


def check(directory: Path, contract: dict) -> None:
    def load(name):
        return json.loads((directory / name).read_text())

    selected = json.loads(Path("deployments/local-control-plane.json").read_text())["controlPlane"]
    assert (contract["image"], contract["sourceRevision"]) == (
        selected["image"],
        selected["sourceRevision"],
    )
    preflight = load("parent-first.result.json")
    workflow_uids, job_uids, checkpoint_uids = set(), set(), set()
    for label, key, code in (
        ("runtime-role", "runtime-url", 1),
        ("first", "migration-url", 0),
        ("repeat", "migration-url", 0),
    ):
        prefix = f"migration-image-{label}"
        workflow, job, checkpoint_workflow, checkpoint, workers = (
            load(prefix + suffix)
            for suffix in (
                ".workflow.json",
                ".job.json",
                ".checkpoint.workflow.json",
                ".checkpoint.json",
                ".worker.json",
            )
        )
        assert checkpoint_workflow["status"]["phase"] == "Succeeded"
        root = checkpoint_workflow["status"]["nodes"][checkpoint_workflow["metadata"]["name"]]
        materialized = [p["value"] for p in root["outputs"]["parameters"] if p["name"] == "result"]
        assert len(materialized) == 1 and json.loads(materialized[0]) == checkpoint
        assert (
            checkpoint["kind"] == "PerfengMaintenanceCheckpoint"
            and checkpoint["status"] == "quiescence-observed"
        )
        for field in ("requestRevision", "requestDigest", "selectionRevision", "clusterUID"):
            assert checkpoint[field] == preflight[field]
        checkpoint_time = datetime.fromisoformat(checkpoint_workflow["status"]["finishedAt"])
        workflow_time = datetime.fromisoformat(workflow["metadata"]["creationTimestamp"])
        job_time = datetime.fromisoformat(job["metadata"]["creationTimestamp"])
        assert all(t.utcoffset() is not None for t in (checkpoint_time, workflow_time, job_time))
        assert checkpoint_time <= workflow_time <= job_time
        pod = job["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        install = label == "first" and workflow.get("spec", {}).get("workflowTemplateRef") == {
            "name": "perfeng-release-schema-migration-v1"
        }
        assert pod["imagePullSecrets"] == [
            {"name": "perfeng-control-plane-registry" if install else "migration-ci-registry"}
        ]
        assert len(pod["containers"]) == 1
        container = pod["containers"][0]
        assert container["imagePullPolicy"] == "Always"
        assert container["image"] == contract["image"] and container["command"] == [
            "/usr/local/bin/migrate"
        ]
        environment = {v["name"]: v for v in container["env"]}
        assert environment["PERFENG_DATABASE_URL"]["valueFrom"]["secretKeyRef"] == {
            "name": "perfeng-control-plane-database",
            "key": key,
        }
        assert environment["PGAPPNAME"]["value"] == (
            "perfeng-schema-migration-v1" if install else f"migration-ci-{label}"
        )
        assert len(workers) == 1 and workers[0]["uid"]
        assert workers[0]["containers"] == [
            {"name": "migrate", "exitCode": code, "waitingReason": None}
        ]
        if code == 0:
            assert workflow["status"]["phase"] == "Succeeded" and job["status"]["succeeded"] == 1
        else:
            assert (
                workflow["status"]["phase"] in {"Failed", "Error"} and job["status"]["failed"] == 1
            )
        workflow_uids.add(workflow["metadata"]["uid"])
        checkpoint_uids.add(checkpoint_workflow["metadata"]["uid"])
        job_uids.add(job["metadata"]["uid"])
    assert len(workflow_uids) == len(checkpoint_uids) == len(job_uids) == 3
    first, repeat = (
        load("migration-image-first.schema.json"),
        load("migration-image-repeat.schema.json"),
    )
    validate_schema(first, contract)
    validate_schema(repeat, contract)
    assert first == repeat


if __name__ == "__main__":
    try:
        if sys.argv[1] == "startup":
            watch, job_path, contract_path, watch_code, wait_code = sys.argv[2:]
            with Path(watch).open(encoding="utf-8", errors="replace") as source:
                stream = source.read(4_194_305)
            report = startup(
                stream[:4_194_304],
                json.loads(Path(job_path).read_text()),
                json.loads(Path(contract_path).read_text()),
                int(watch_code),
                int(wait_code),
            )
            if len(stream) > 4_194_304:
                report["limitations"].append("watch-size-limit")
            print(json.dumps(report, sort_keys=True))
        else:
            check(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()))
    except (AssertionError, ValueError, KeyError, TypeError) as error:
        print(f"Migration image evidence refused ({type(error).__name__})", file=sys.stderr)
        sys.exit(1)
