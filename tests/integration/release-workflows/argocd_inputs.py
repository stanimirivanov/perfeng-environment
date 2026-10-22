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
ENVIRONMENT_REPOSITORY = "https://github.com/stanimirivanov/perfeng-environment.git"


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
        source = yaml.safe_load(POSTGRES_APPLICATION.read_text(encoding="utf-8"))["spec"]["source"]
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


def postgres_application(revision: str) -> dict[str, Any]:
    """Pin the real Application and disable automated reconciliation in CI only."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("PostgreSQL CI source must use a full selected Git commit")
    application = yaml.safe_load(POSTGRES_APPLICATION.read_text(encoding="utf-8"))
    assert application["metadata"]["name"] == "perfeng-local-postgres"
    assert application["metadata"]["namespace"] == "argocd"
    spec = application["spec"]
    assert spec["project"] == "perfeng-local"
    assert spec["source"] == {
        "repoURL": ENVIRONMENT_REPOSITORY,
        "targetRevision": "main",
        "path": "charts/postgres",
        "helm": {"releaseName": "postgres"},
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


def postgres_converged(value: dict[str, Any], uid: str, case: str, revision: str) -> None:
    """Require this single-source sync to adopt the exact selected chart."""
    spec = postgres_application(revision)["spec"]
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
    elif mode == "secret":
        Path(rest[0]).write_text(
            json.dumps(repository_secret(Path(first).read_text(encoding="utf-8"), *rest[1:])),
            encoding="utf-8",
        )
    elif mode == "converged":
        converged(json.loads(Path(first).read_text(encoding="utf-8")), *rest)
    elif mode == "postgres":
        Path(rest[0]).write_text(json.dumps(postgres_application(first)), encoding="utf-8")
    elif mode == "postgres-converged":
        postgres_converged(json.loads(Path(first).read_text(encoding="utf-8")), *rest)
    else:
        raise ValueError("Unknown Argo CD fixture operation")
