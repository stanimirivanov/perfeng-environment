"""Offline boundaries for the disposable Argo CD acceptance phase."""

import json
import sys
from pathlib import Path
from typing import Any

import yaml

from scripts import infrastructure_release as infrastructure

APPLICATION = Path("gitops/environments/local/control-plane-application.yaml")


def bootstrap(source: Path) -> Path:
    """Reuse the infrastructure validator and require its exact clean checkout."""
    lock = infrastructure.validate(infrastructure_root=source)
    if infrastructure.git(source, "rev-parse", "HEAD").decode().strip() != lock.revision:
        raise ValueError("CI infrastructure checkout differs from selection")
    if infrastructure.git(source, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("CI infrastructure checkout is modified")
    return source / Path(lock.argocd_bootstrap).parent


def repository_secret(token: str) -> dict[str, Any]:
    """Configure one repository, without implying attenuation of token rights."""
    if not token or any(c.isspace() for c in token):
        raise ValueError("Argo CD source credential is unavailable or malformed")
    sources = yaml.safe_load(APPLICATION.read_text(encoding="utf-8"))["spec"]["sources"]
    repository = "https://github.com/stanimirivanov/perfeng-k8s.git"
    assert len(sources) == 2 and all(s["repoURL"] == repository for s in sources)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": "ci-perfeng-k8s-repository",
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
            json.dumps(repository_secret(Path(first).read_text(encoding="utf-8"))), encoding="utf-8"
        )
    elif mode == "converged":
        converged(json.loads(Path(first).read_text(encoding="utf-8")), *rest)
    else:
        raise ValueError("Unknown Argo CD fixture operation")
