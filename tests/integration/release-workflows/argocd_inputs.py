"""Offline boundaries for the disposable Argo CD acceptance phase."""

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from scripts import infrastructure_release as infrastructure

APPLICATION = Path("gitops/environments/local/control-plane-application.yaml")
POSTGRES_APPLICATION = Path("gitops/environments/local/postgres-application.yaml")
FOUNDATION_APPLICATIONS = {
    "postgres": POSTGRES_APPLICATION,
    "seaweedfs": Path("gitops/environments/local/seaweedfs-application.yaml"),
}
ENVIRONMENT_REPOSITORY = "https://github.com/stanimirivanov/perfeng-environment.git"


def bootstrap_diagnostics(
    value: dict[str, Any], log_text: str = "", events: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Publish fixed status vocabulary only; never vendor messages or Pod specs."""
    components = {"argocd-redis", "argocd-repo-server", "argocd-application-controller"}
    reasons = {
        "ErrImagePull",
        "ImagePullBackOff",
        "CreateContainerConfigError",
        "CreateContainerError",
        "CrashLoopBackOff",
        "ContainerCreating",
        "PodInitializing",
        "OOMKilled",
        "Error",
        "Completed",
        "DeadlineExceeded",
    }
    signals = {
        "rate-limit": r"too many requests|toomanyrequests|rate.?limit|\b429\b",
        "http-authorization": r"\b(?:401 unauthorized|403 forbidden)\b",
        "registry-unavailable": r"\b50[0234]\b|service unavailable|bad gateway",
        "authorization": r"unauthorized|forbidden|denied|authentication required",
        "manifest-missing": r"manifest unknown|not found|no matching manifest",
        "network": r"timeout|timed out|connection refused|no such host|network is unreachable",
        "tls": r"x509|certificate|tls handshake",
        "capacity": r"insufficient (cpu|memory)|unschedulable|out of memory",
        "missing-secret": r"secret .*not found|couldn't find key",
        "permission": r"permission denied|operation not permitted",
        "configuration": r"fatal config file error|bad directive|invalid argument",
    }
    pods = []
    for pod in value["items"]:
        component = pod.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/name")
        if component not in components:
            continue
        status = pod.get("status", {})
        phase = status.get("phase")
        containers = []
        for field, kind in (("initContainerStatuses", "init"), ("containerStatuses", "container")):
            for container in status.get(field, []):
                state = container.get("state", {})
                active = next(
                    (key for key in ("waiting", "terminated", "running") if key in state), "unknown"
                )
                reason = state.get(active, {}).get("reason")
                name = container.get("name")
                containers.append(
                    {
                        "kind": kind,
                        "name": name
                        if name
                        in {
                            "redis",
                            "secret-init",
                            "argocd-repo-server",
                            "argocd-application-controller",
                        }
                        else "other",
                        "state": active,
                        "reason": reason if reason in reasons else "unknown",
                    }
                )
        # Classify private status text, but emit only constant category names.
        private = json.dumps(status)
        pods.append(
            {
                "component": component,
                "phase": phase
                if phase in {"Pending", "Running", "Succeeded", "Failed", "Unknown"}
                else "unknown",
                "containers": containers,
                "signals": [
                    name
                    for name, pattern in signals.items()
                    if re.search(pattern, private, re.IGNORECASE)
                ],
                "events": [
                    {
                        "reason": event.get("reason")
                        if event.get("reason")
                        in {
                            "Failed",
                            "BackOff",
                            "FailedScheduling",
                            "FailedMount",
                            "FailedCreatePodSandBox",
                        }
                        else "unknown",
                        "signals": [
                            name
                            for name, pattern in signals.items()
                            if re.search(pattern, str(event.get("message", "")), re.IGNORECASE)
                        ],
                    }
                    for event in (events or {}).get("items", [])
                    if pod.get("metadata", {}).get("uid")
                    and event.get("involvedObject", {}).get("uid") == pod["metadata"]["uid"]
                    and event.get("involvedObject", {}).get("kind") == "Pod"
                    and event.get("involvedObject", {}).get("namespace") == "argocd"
                ],
            }
        )
    return {
        "schemaVersion": 1,
        "events": "collected" if events is not None else "unavailable",
        "pods": pods,
        "logSignals": [
            name for name, pattern in signals.items() if re.search(pattern, log_text, re.IGNORECASE)
        ],
    }


def bootstrap(source: Path) -> Path:
    """Reuse the infrastructure validator and require its exact clean checkout."""
    lock = infrastructure.validate(infrastructure_root=source)
    if infrastructure.git(source, "rev-parse", "HEAD").decode().strip() != lock.revision:
        raise ValueError("CI infrastructure checkout differs from selection")
    if infrastructure.git(source, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("CI infrastructure checkout is modified")
    return source / Path(lock.argocd_bootstrap).parent


def repository_secret(
    token: str, repository: str = "https://github.com/stanimirivanov/perfeng-k8s.git"
) -> dict[str, Any]:
    """Configure one repository, without implying attenuation of token rights."""
    if not token or any(c.isspace() for c in token):
        raise ValueError("Argo CD source credential is unavailable or malformed")
    if repository == "https://github.com/stanimirivanov/perfeng-k8s.git":
        name = "ci-perfeng-k8s-repository"
        sources = yaml.safe_load(APPLICATION.read_text(encoding="utf-8"))["spec"]["sources"]
        assert len(sources) == 2 and all(s["repoURL"] == repository for s in sources)
    elif repository == ENVIRONMENT_REPOSITORY:
        name = "ci-perfeng-environment-repository"
        for path in FOUNDATION_APPLICATIONS.values():
            source = yaml.safe_load(path.read_text(encoding="utf-8"))["spec"]["source"]
            assert source["repoURL"] == repository
    else:
        raise ValueError("Repository is outside the approved disposable Argo CD sources")
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": "argocd",
            "labels": {"argocd.argoproj.io/secret-type": "repository"},
        },
        "type": "Opaque",
        "immutable": True,
        "stringData": {
            "type": "git",
            "url": repository,
            "username": "x-access-token",
            "password": token,
        },
    }


def foundation_application(revision: str, workload: str) -> dict[str, Any]:
    """Pin the real Application and disable automated reconciliation in CI only."""
    if workload not in FOUNDATION_APPLICATIONS:
        raise ValueError("Unsupported foundation Application")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Foundation CI source must use a full selected Git commit")
    application = yaml.safe_load(FOUNDATION_APPLICATIONS[workload].read_text(encoding="utf-8"))
    assert application["metadata"]["name"] == f"perfeng-local-{workload}"
    assert application["metadata"]["namespace"] == "argocd"
    spec = application["spec"]
    assert spec["project"] == "perfeng-local"
    assert spec["source"] == {
        "repoURL": ENVIRONMENT_REPOSITORY,
        "targetRevision": "main",
        "path": f"charts/{workload}",
        "helm": {"releaseName": workload},
    }
    assert spec["destination"] == {
        "server": "https://kubernetes.default.svc",
        "namespace": "perf-platform",
    }
    assert spec["syncPolicy"] == {
        "automated": {"enabled": True, "prune": False, "selfHeal": True},
        "syncOptions": ["CreateNamespace=false"],
    }
    spec["source"]["targetRevision"] = revision
    spec["syncPolicy"]["automated"] = {"enabled": False, "prune": False, "selfHeal": False}
    return application


def foundation_converged(
    value: dict[str, Any], uid: str, case: str, revision: str, workload: str
) -> None:
    """Require this single-source sync to adopt the exact selected chart."""
    spec = foundation_application(revision, workload)["spec"]
    assert value["metadata"]["uid"] == uid and value["spec"] == spec
    status = value["status"]
    assert status["sync"]["status"] == "Synced" and status["health"]["status"] == "Healthy"
    assert not status.get("conditions") and not value.get("operation")
    assert status["sync"]["revision"] == revision
    assert status["sync"]["comparedTo"]["source"] == spec["source"]
    assert status["sync"]["comparedTo"]["destination"] == spec["destination"]
    state = status["operationState"]
    assert state["phase"] == "Succeeded" and state["syncResult"]["revision"] == revision
    assert state["syncResult"]["source"] == spec["source"]
    assert state["operation"]["info"] == [{"name": "ci-case", "value": case}]
    assert state["operation"]["sync"] == {"revision": revision, "syncStrategy": {"apply": {}}}


def postgres_application(revision: str) -> dict[str, Any]:
    """Preserve the PostgreSQL fixture adapter while sharing foundation validation."""
    return foundation_application(revision, "postgres")


def postgres_converged(value: dict[str, Any], uid: str, case: str, revision: str) -> None:
    """Validate the existing PostgreSQL single-source adapter."""
    foundation_converged(value, uid, case, revision, "postgres")


def converged(value: dict[str, Any], uid: str, case: str) -> None:
    """Refuse stale operations, source overrides and synthetic/partial convergence."""
    spec = yaml.safe_load(APPLICATION.read_text(encoding="utf-8"))["spec"]
    assert value["metadata"]["uid"] == uid and value["spec"] == spec
    status = value["status"]
    assert status["sync"]["status"] == "Synced" and status["health"]["status"] == "Healthy"
    assert not status.get("conditions") and not value.get("operation")
    assert status["sync"]["comparedTo"]["sources"] == spec["sources"]
    assert status["sync"]["comparedTo"]["destination"] == spec["destination"]
    revisions = [s["targetRevision"] for s in spec["sources"]]
    assert status["sync"]["revisions"] == revisions
    state = status["operationState"]
    assert state["phase"] == "Succeeded" and state["syncResult"]["revisions"] == revisions
    operation = state["operation"]
    assert operation["info"] == [{"name": "ci-case", "value": case}]
    assert operation["sync"] == {
        "revisions": revisions,
        "syncStrategy": {"apply": {}},
    }


if __name__ == "__main__":
    mode, first, *rest = sys.argv[1:]
    if mode == "bootstrap":
        print(bootstrap(Path(first)).as_posix())
    elif mode == "bootstrap-diagnostics":
        print(
            json.dumps(
                bootstrap_diagnostics(
                    json.loads(Path(first).read_text(encoding="utf-8")),
                    Path(rest[0]).read_text(encoding="utf-8", errors="replace"),
                    json.loads(Path(rest[1]).read_text(encoding="utf-8")),
                )
            )
        )
    elif mode == "secret":
        Path(rest[0]).write_text(
            json.dumps(repository_secret(Path(first).read_text(encoding="utf-8"), *rest[1:])),
            encoding="utf-8",
        )
    elif mode == "converged":
        converged(json.loads(Path(first).read_text(encoding="utf-8")), *rest)
    elif mode in {"postgres", "seaweedfs"}:
        Path(rest[0]).write_text(json.dumps(foundation_application(first, mode)), encoding="utf-8")
    elif mode in {"postgres-converged", "seaweedfs-converged"}:
        foundation_converged(
            json.loads(Path(first).read_text(encoding="utf-8")),
            *rest,
            workload=mode.removesuffix("-converged"),
        )
    else:
        raise ValueError("Unknown Argo CD fixture operation")
