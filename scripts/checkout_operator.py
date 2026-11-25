"""Operate one retained checkout Run on the reviewed local deployment."""

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

if __package__ in ("", None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import (
    checkout_api,
    cluster,
    control_plane_health,
    control_plane_maintenance,
    control_plane_release,
    control_plane_smoke,
    gitops,
    infrastructure_release,
    k6_run,
)
from scripts.checkout_checkpoint import Checkpoint, private_bytes
from scripts.kubernetes_benchmark_contract import document


@dataclass
class Selection:
    release: control_plane_release.DeploymentLock
    identity: dict[str, Any]
    request: dict[str, Any]
    entry: dict[str, Any]
    policy: dict[str, Any]


def select(root: Path, lock_sha256: str | None, cluster_uid: str | None) -> Selection:
    lock = root / "deployments/local-control-plane.json"
    content = lock.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if lock_sha256 is not None and lock_sha256 != digest:
        raise ValueError("Deployment Lock digest differs from owner selection")
    k8s = root.parent / "perfeng-k8s"
    release = control_plane_release.validate(lock, k8s, root.parent / "perfeng-control-plane")
    infra = infrastructure_release.validate(
        root / "deployments/local-infrastructure.json", root.parent / "k8s-infrastructure"
    )
    gitops.validate_configuration(root)
    read = control_plane_release.revision_reader(
        k8s, control_plane_release.DEPLOYMENT_REPOSITORY, release.deployment_revision
    )
    entries = json.loads(read(f"{release.bundle_path}/registry.json"))["entries"]
    uids = {e["benchmark"]["clusterUid"] for e in entries if "benchmark" in e}
    if len(uids) != 1 or (cluster_uid is not None and cluster_uid not in uids):
        raise ValueError("Reviewed cluster UID differs from owner selection")
    revision = control_plane_smoke.candidate_revision(root)
    request = control_plane_smoke.smoke_request(release, revision, k8s)
    entry = next(e for e in entries if e["testId"] == "checkout-api")
    if entry.get("principals") != ["local-operator"]:
        raise ValueError("Reviewed checkout principal differs")
    policy = json.loads(read(f"{release.bundle_path}/{entry['policy']}"))
    identity = {
        "environmentRevision": revision,
        "lockSha256": digest,
        "deploymentLock": json.loads(content),
        "infrastructureRevision": infra.revision,
        "clusterUid": uids.pop(),
        "context": cluster.CONTEXT,
        "principal": "local-operator",
    }
    return Selection(release, identity, request, entry, policy)


def verify_substrate(root: Path, lock_sha256: str, cluster_uid: str) -> Selection:
    """Check the reviewed lock and live incarnation before owner preparation."""
    selection = select(root, lock_sha256, cluster_uid)
    with cluster.command_deadline(60):
        cluster.verify_local_docker()
        cluster.verify_context(root)
        kube = control_plane_maintenance.kube(root, "kube-system")
        uid = cluster.run(
            [*kube, "get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"]
        )
        if uid.strip() != cluster_uid:
            raise ValueError("Observed kube-system UID differs from owner selection")
        if cluster.run([*kube, "get", "--raw=/readyz"]).strip() != "ok":
            raise ValueError("Kubernetes API readiness check failed")
    return selection


def verify_selection(root: Path, lock_sha256: str, cluster_uid: str) -> Selection:
    """Additionally require the retained child Application at the selected release."""
    selection = verify_substrate(root, lock_sha256, cluster_uid)
    with cluster.command_deadline(60):
        child = control_plane_maintenance.read(
            root, "argocd", "application", gitops.CONTROL_PLANE_APPLICATION
        )
        if child is None:
            raise ValueError("Retained control-plane Application missing")
        control_plane_maintenance.validate_application(child, gitops.CONTROL_PLANE_APPLICATION)
        desired = gitops.load_yaml(
            root / "gitops/environments/local/control-plane-application.yaml"
        )
        for field in ("sources", "project", "destination", "syncPolicy"):
            if child["spec"].get(field) != desired["spec"][field]:
                raise ValueError("Retained Application differs from reviewed deployment pins")
    return selection


RUN_ID = re.compile(r"perf-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}")
ACTIVE = {
    "CREATED",
    "VALIDATING",
    "PROVISIONING",
    "WARMING_UP",
    "RUNNING",
    "COLLECTING",
    "ANALYZING",
    "REPORTING",
    "CANCELLING",
}
TERMINAL = {"COMPLETED", "INVALID", "ABORTED", "INFRASTRUCTURE_FAILURE", "TEST_FAILURE"}


def now() -> str:
    return datetime.now(UTC).isoformat()


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("UTC offset required on evidence timestamps")
    return parsed


def submit(record: Checkpoint, selection: Selection, api) -> None:
    state = {
        "schemaVersion": 1,
        "phase": "submission-uncertain",
        "selection": selection.identity,
        "request": selection.request,
        "requestKey": "checkout-" + uuid.uuid4().hex,
        "startedAt": now(),
        "runId": None,
    }
    record.save(state)
    accepted = api("/v1/runs", request=selection.request, key=state["requestKey"])
    run_id = accepted.get("id")
    if (
        not isinstance(run_id, str)
        or not RUN_ID.fullmatch(run_id)
        or accepted.get("request") != selection.request
        or accepted.get("state") != "CREATED"
    ):
        raise ValueError("Invalid acknowledgement; original submission remains uncertain")
    state.update(runId=run_id, acknowledgedAt=now(), phase="acknowledged")
    record.save(state)
    print(f"Acknowledged checkout Run {run_id}.", flush=True)


def observe(
    record: Checkpoint, selection: Selection, api, original_id: str | None, source_path: Path | None
) -> tuple[dict, dict, dict]:
    state = record.load()
    if state["selection"] != selection.identity or state["request"] != selection.request:
        raise ValueError("Checkpoint selection or request differs")
    run_id = state["runId"]
    proof = None
    if run_id is None:
        if not original_id or not source_path or not RUN_ID.fullmatch(original_id):
            raise ValueError(
                "Uncertain submission requires independent evidence of the original Run"
            )
        proof = document(private_bytes(source_path, 16384))
        if (
            not isinstance(proof, dict)
            or proof.get("requestKey") != state["requestKey"]
            or proof.get("principal") != selection.identity["principal"]
            or proof.get("runId") != original_id
            or not isinstance(proof.get("source"), str)
            or not proof["source"].strip()
        ):
            raise ValueError("Independent original Run correlation differs from checkpoint")
        run_id = original_id
    elif original_id is not None or source_path is not None:
        raise ValueError("Acknowledged Run cannot be replaced")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid checkpoint Run ID")
    path = "/v1/runs/" + run_id
    deadline = time.monotonic() + 1100
    while True:
        current = api(path)
        revision = current.get("revision")
        lifecycle = current.get("state")
        if (
            current.get("id") != run_id
            or current.get("request") != selection.request
            or lifecycle not in ACTIVE | TERMINAL
            or type(revision) is not int
            or revision < 1
            or revision < state.get("revision", 0)
        ):
            raise ValueError("Authenticated Run identity, state or revision differs")
        if proof is not None:
            if instant(current["createdAt"]) < instant(state["startedAt"]):
                raise ValueError("Correlated Run predates the original submission")
            state.update(runId=run_id, originalRunSource=proof["source"], attachedAt=now())
            proof = None
        if state.get("terminalState") and (
            state["terminalState"] != lifecycle or state["revision"] != revision
        ):
            raise ValueError("Authenticated terminal state differs from retained evidence")
        if state.get("revision") != revision or not state.get("observedAt"):
            state.update(revision=revision, observedAt=now())
            record.save(state)
        if lifecycle in TERMINAL:
            if not state.get("terminalState"):
                state.update(terminalState=lifecycle, terminalObservedAt=now(), phase="terminal")
                record.save(state)
            if lifecycle != "COMPLETED":
                raise ValueError(f"Run ended in {lifecycle}; no checkout SLO verdict")
            break
        if time.monotonic() >= deadline:
            raise ValueError("Run polling timed out; resume this checkpoint")
        time.sleep(2)
    registrations = api(path + "/artifacts")
    return state, current, registrations


def require_active(root: Path, selection: Selection) -> None:
    with cluster.command_deadline(300):
        for name in control_plane_maintenance.APPLICATIONS:
            application = control_plane_maintenance.read(root, "argocd", "application", name)
            if application is None:
                raise ValueError("Retained Application missing; refer to Environment owner")
            control_plane_maintenance.validate_application(application, name)
            status = application["status"]
            if name == gitops.CONTROL_PLANE_APPLICATION:
                if (
                    status.get("sync", {}).get("status") != "Synced"
                    or status.get("health", {}).get("status") != "Healthy"
                    or status.get("sync", {}).get("revisions")
                    != [selection.release.deployment_revision] * 2
                    or status.get("operationState", {}).get("phase") not in (None, "Succeeded")
                ):
                    raise ValueError(
                        "Retained child is not healthy and synced to both selected pins"
                    )
            elif (
                application["spec"].get("syncPolicy", {}).get("automated", {}).get("enabled")
                is not True
            ):
                raise ValueError(
                    "Unexpected maintenance; Environment owner must qualify resumption"
                )
        control_plane_health.verify_resources(
            control_plane_maintenance.kube(root, "perf-platform"), selection.release
        )
        kube = control_plane_maintenance.kube(root, "perf-sut")
        cluster.run([*kube, "rollout", "status", "deployment/sample-sut", "--timeout=60s"])
        k6_run.validate_sample(
            cluster.run([*kube, "get", "deployment", "sample-sut", "-o", "json"]), root
        )


def evidence(
    record: Checkpoint,
    selection: Selection,
    state: dict,
    run: dict,
    registrations: dict,
    args: argparse.Namespace,
) -> None:
    from scripts import checkout_report

    claim = checkout_report.report_registration(state["runId"], registrations)
    if state.get("reportRegistration", claim) != claim:
        raise ValueError("Authenticated report registration changed; preserve the checkpoint")
    # Save exact logical snapshots of the independent authenticated reads for the return visit.
    for name, value in (("run.json", run), ("artifacts.json", registrations)):
        content = json.dumps(value, sort_keys=True, allow_nan=False).encode() + b"\n"
        record.retain(name, content)
    if "reportRegistration" not in state:
        state.update(reportRegistration=claim, registeredAt=now(), phase="awaiting-report")
        record.save(state)
    if args.report_file is None and "decision" not in state:
        print(f"Awaiting approved original bytes for {claim['uri']}.")
        return
    delivery = state.get("delivery")
    if args.report_file is not None:
        instant(args.fulfilled_at)
        supplied = {"fulfilledBy": args.fulfilled_by, "fulfilledAt": args.fulfilled_at}
        if delivery is not None and delivery != supplied:
            raise ValueError("Recorded fulfillment differs; preserve the original evidence")
        delivery = supplied
    path = args.report_file or (record.directory / "report.json")
    content = private_bytes(path, claim["sizeBytes"])
    decision = checkout_report.validate_report(
        root=cluster.ROOT,
        run_id=state["runId"],
        request=selection.request,
        entry=selection.entry,
        policy=selection.policy,
        run=run,
        registrations=registrations,
        content=content,
    )
    if state.get("decision", decision) != decision:
        raise ValueError("Decision differs from retained evidence")
    record.retain("report.json", content)
    if "decision" not in state:
        state.update(decision=decision, delivery=delivery, verifiedAt=now(), phase="verified")
        record.save(state)
    print(f"Verified checkout decision: {decision['verdict']}.")


def operate(args: argparse.Namespace) -> None:
    if args.action == "preview":
        if args.execute:
            raise ValueError("Preview is offline")
        result = select(cluster.ROOT, args.lock_sha256, args.cluster_uid)
        print(json.dumps({"selection": result.identity, "request": result.request}))
        return
    if not args.execute or not args.lock_sha256 or not args.cluster_uid:
        raise ValueError("Live commands require --execute, --lock-sha256 and --cluster-uid")
    if args.action in ("submit", "resume") and not (args.checkpoint_dir and args.token_file):
        raise ValueError("Specify the private --checkpoint-dir and --token-file")
    if args.action != "resume" and any(
        (
            args.original_run_id,
            args.original_run_source,
            args.report_file,
            args.fulfilled_at,
            args.fulfilled_by,
        )
    ):
        raise ValueError("Correlation and report delivery apply only to resume")
    if bool(args.original_run_id) != bool(args.original_run_source):
        raise ValueError("Specify both original Run ID and independent correlation source")
    delivery_args = (args.report_file, args.fulfilled_by, args.fulfilled_at)
    if any(delivery_args) and not all(delivery_args):
        raise ValueError(
            "Report delivery requires --report-file, --fulfilled-by and --fulfilled-at"
        )
    selection = verify_selection(cluster.ROOT, args.lock_sha256, args.cluster_uid)
    if args.action == "check":
        print("Verified retained deployment selection.")
        return
    require_active(cluster.ROOT, selection)
    with checkout_api.session(cluster.ROOT, args.token_file) as api:
        with Checkpoint(args.checkpoint_dir, create=args.action == "submit") as record:
            if args.action == "submit":
                submit(record, selection, api)
            state, run, registrations = observe(
                record, selection, api, args.original_run_id, args.original_run_source
            )
            evidence(record, selection, state, run, registrations, args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preview", "check", "submit", "resume"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--lock-sha256")
    parser.add_argument("--cluster-uid")
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--original-run-id")
    parser.add_argument("--original-run-source", type=Path)
    parser.add_argument("--report-file", type=Path)
    parser.add_argument("--fulfilled-by")
    parser.add_argument("--fulfilled-at")
    args = parser.parse_args(argv)
    try:
        operate(args)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        print(f"Checkout stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
