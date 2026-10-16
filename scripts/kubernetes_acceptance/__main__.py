"""Preview or explicitly run M04 integration acceptance without implicit repairs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

import yaml

from scripts import (
    benchmark_authority,
    cluster,
    control_plane_health,
    kubernetes_benchmark,
    postgres,
    resource_metrics_health,
)
from scripts import kubernetes_benchmark_capture as capture
from scripts import kubernetes_benchmark_profile as cohort
from scripts.kubernetes_acceptance import schemas, selection
from scripts.kubernetes_acceptance.errors import AcceptanceError
from scripts.kubernetes_acceptance.journal import Journal
from scripts.kubernetes_acceptance.lifecycle import Lifecycle
from scripts.kubernetes_acceptance.transport import RUN, Session
from scripts.kubernetes_acceptance.workflow import Acceptance


def execute(revision: str, approve: bool, source: str | None, root: Path = cluster.ROOT) -> None:
    """Preflight without repair, then create an isolated retained acceptance session."""
    revision = selection.candidate_revision(revision)
    print("M04 preflight: verifying pinned release and contract closure (offline)...", flush=True)
    selected = selection.load(root, revision)
    contracts = schemas.load(root)
    with cluster.command_deadline(3400):
        print(
            "M04 preflight: verifying the activated control plane and metrics without repair...",
            flush=True,
        )
        cluster.verify_local_docker()
        cluster.verify_context(root)
        # The broad credential-health helper decodes Secrets; keep this client
        # path on resource/endpoint reads so bearer and S3 values stay in Pods.
        control_plane_health.verify_resources(postgres.kubectl(root), selected.lock)
        benchmark_authority.verify(root, selected.lock)
        resource_metrics_health.verify(root)
        print("M04 preflight: collecting current dedicated-cluster qualification...", flush=True)
        profile = cohort.load(root / "profiles/kubernetes/local-kind-integration.json", root)
        observed = capture.collect(root, profile)
        evidence, _ = kubernetes_benchmark.evaluate(profile, observed, root)
        if (
            evidence["status"] != "ACCEPTED"
            or evidence["environment"] != selected.entry["environment"]
        ):
            raise AcceptanceError(
                "Current qualification must be all-PASS for the exact selected environment"
            )
        lifecycle = Lifecycle(root, selected)
        print("M04 preflight: requiring an exclusive operator window...", flush=True)
        lifecycle.database.exclusive()
        name = "m04-acceptance-" + uuid4().hex[:16]
        journal = Journal(root, name)
        try:
            journal.record("session-planned", clientPod=name, request=selected.request)
            print(f"Retaining progress at {journal.path}", flush=True)
            session = Session(root, name)
            session.create()
            Acceptance(session, lifecycle, selected, contracts, journal).run(
                approve_baseline=approve,
                reference_run=source,
            )
            print(f"M04 acceptance passed; receipt: {journal.receipt}", flush=True)
        except BaseException:
            journal.record("acceptance-incomplete", clientPod=name)
            raise
        finally:
            journal.close()


def main(argv: list[str] | None = None) -> int:
    """Preview makes no subprocess/network/state calls; mutations require exact confirmation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--candidate-git-sha")
    parser.add_argument("--approve-baseline", action="store_true")
    parser.add_argument("--reference-run")
    args = parser.parse_args(argv)
    try:
        if args.reference_run is not None and RUN.fullmatch(args.reference_run) is None:
            raise AcceptanceError("Invalid explicit reference Run")
        if args.candidate_git_sha is not None:
            selection.candidate_revision(args.candidate_git_sha)
        if not args.execute:
            print(
                json.dumps(
                    {
                        "preview": True,
                        "cluster": "perfeng-local",
                        "purpose": "integration",
                        "steps": [
                            "verify runtime/metrics/current qualification",
                            "verify or explicitly approve reference baseline",
                            "dispatch and cancel owned Run",
                            "restart exact controller Pod during cleanup hold",
                            "verify cleanup and post-recovery baseline decision",
                        ],
                        "baselineApproval": args.approve_baseline,
                    }
                )
            )
            print(
                "Preview only: no processes, network or writes. Read docs/kubernetes-benchmark-acceptance.md before --execute --confirm perfeng-local."
            )
            return 0
        if args.confirm != cluster.NAME:
            raise AcceptanceError(
                "Execution requires --confirm perfeng-local and an exclusive operator window"
            )
        execute(
            selection.candidate_revision(args.candidate_git_sha),
            args.approve_baseline,
            args.reference_run,
        )
        return 0
    except AcceptanceError as error:
        print(
            f"M04 acceptance failed: {error}. Inspect the retained session/journal and docs/kubernetes-benchmark-acceptance.md.",
            flush=True,
        )
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
        print(
            "M04 acceptance failed: inspect the last named phase, retained session/journal and docs/kubernetes-benchmark-acceptance.md. No automatic repair, baseline replacement or cleanup bypass was performed. External payloads withheld.",
            flush=True,
        )
        return 1
    except KeyboardInterrupt:
        print(
            "M04 acceptance interrupted; Runs, holds, client Pod and journal retained. Inspect before retry."
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
