"""Read-only, bounded maintenance checkpoint; never pause, repair or authorize migration."""

import hashlib
import json
import os
import re
import ssl
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

API = "https://kubernetes.default.svc"
PLATFORM = "/apis/apps/v1/namespaces/perf-platform/deployments/control-plane"
APPLICATION = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/"
NAMES = ("perfeng-local-root", "perfeng-local-control-plane")
SELECTOR = "app.kubernetes.io/name=perfeng-control-plane,app.kubernetes.io/instance=control-plane"
PODS = "/api/v1/namespaces/perf-platform/pods?limit=1&labelSelector=" + SELECTOR.replace(
    "=", "%3D"
).replace(",", "%2C")
ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        require(key not in value, "duplicate JSON key")
        value[key] = item
    return value


def identity(value: dict[str, Any], kind: str, namespace: str, name: str) -> dict[str, str]:
    metadata = value["metadata"]
    require(
        value["kind"] == kind
        and value["apiVersion"]
        == {"Application": "argoproj.io/v1alpha1", "Deployment": "apps/v1", "Namespace": "v1"}[kind]
        and metadata["name"] == name
        and metadata.get("namespace", "") == namespace
        and isinstance(metadata.get("uid"), str)
        and metadata["uid"]
        and isinstance(metadata.get("resourceVersion"), str)
        and metadata["resourceVersion"]
        and not metadata.get("deletionTimestamp")
        and not metadata.get("ownerReferences"),
        "resource identity or ownership differs",
    )
    return {
        "kind": kind,
        "namespace": namespace,
        "name": name,
        "uid": metadata["uid"],
        "resourceVersion": metadata["resourceVersion"],
    }


def verify(
    preflight: dict[str, Any],
    raw_lock: bytes,
    revision: str,
    before: list[dict[str, Any]],
    pods: dict[str, Any],
    after: list[dict[str, Any]],
    namespace: dict[str, Any],
) -> dict[str, Any]:
    require(
        preflight["schemaVersion"] == 1
        and preflight["kind"] == "PerfengReleasePreflight"
        and preflight["status"] == "identity-verified"
        and preflight["environment"] == "perfeng-local"
        and preflight["selectionRevision"] == revision,
        "preflight identity differs",
    )
    require(
        "sha256:" + hashlib.sha256(raw_lock).hexdigest()
        == preflight["lockDigests"]["deployments/local-control-plane.json"],
        "selected lock differs from preflight",
    )
    lock = json.loads(raw_lock, object_pairs_hook=unique)
    require(lock["schemaVersion"] == 1, "unsupported lock version")
    deployment = lock["deployment"]
    require(
        re.fullmatch(r"[0-9a-f]{40}", deployment["revision"])
        and deployment["repository"] == "https://github.com/stanimirivanov/perfeng-k8s.git",
        "unsupported deployment source",
    )
    require(len(before) == len(after) == 3, "incomplete observations")
    resources = []
    for name, value in zip(NAMES, before[:2], strict=True):
        resources.append(identity(value, "Application", "argocd", name))
        spec = value["spec"]
        require(
            value["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by")
            == "perfeng-environment"
            and spec["project"] == ("default" if name == NAMES[0] else "perfeng-local")
            and spec["destination"]
            == {"server": API, "namespace": "argocd" if name == NAMES[0] else "perf-platform"}
            and spec.get("syncPolicy", {}).get("automated", {}).get("enabled") is False
            and not value.get("operation")
            and value.get("status", {}).get("operationState", {}).get("phase")
            not in {"Running", "Terminating"},
            "Application is foreign, unpaused or syncing",
        )
        if name == NAMES[0]:
            require(
                "sources" not in spec
                and spec["source"]
                == {
                    "repoURL": "https://github.com/stanimirivanov/perfeng-environment.git",
                    "targetRevision": "main",
                    "path": "gitops/environments/local",
                },
                "root source differs",
            )
        else:
            require(
                "source" not in spec
                and len(spec["sources"]) == 2
                and [
                    (source["repoURL"], source["targetRevision"], source["path"])
                    for source in spec["sources"]
                ]
                == [
                    (deployment["repository"], deployment["revision"], path)
                    for path in (deployment["chart"]["path"], deployment["runtimePackage"]["path"])
                ],
                "child sources differ from selected lock",
            )
    value = before[2]
    resources.append(identity(value, "Deployment", "perf-platform", "control-plane"))
    labels = value["metadata"].get("labels", {})
    generation = value["metadata"]["generation"]
    require(
        labels.get("app.kubernetes.io/name") == "perfeng-control-plane"
        and labels.get("app.kubernetes.io/instance") == "control-plane"
        and value["spec"]["template"]["spec"]["serviceAccountName"] == "control-plane"
        and value["spec"]["selector"]
        == {
            "matchLabels": {
                "app.kubernetes.io/name": "perfeng-control-plane",
                "app.kubernetes.io/instance": "control-plane",
            }
        }
        and type(generation) is int
        and generation > 0
        and value["status"]["observedGeneration"] == generation
        and value["spec"]["replicas"] == 0
        and all(
            value["status"].get(field, 0) == 0
            for field in ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas")
        ),
        "Deployment is foreign, stale or not stopped",
    )
    require(
        pods["apiVersion"] == "v1"
        and pods["kind"] == "PodList"
        and pods["items"] == []
        and not pods["metadata"].get("continue")
        and isinstance(pods["metadata"].get("resourceVersion"), str)
        and pods["metadata"]["resourceVersion"],
        "Pods remain or absence is unproven",
    )
    for expected, value in zip(resources, after, strict=True):
        require(
            identity(value, expected["kind"], expected["namespace"], expected["name"]) == expected,
            "resource changed during checkpoint; inspect and resubmit",
        )
    identity(namespace, "Namespace", "", "kube-system")
    require(namespace["metadata"]["uid"] == preflight["clusterUID"], "cluster incarnation changed")
    return {
        "schemaVersion": 1,
        "kind": "PerfengMaintenanceCheckpoint",
        "status": "quiescence-observed",
        "environment": "perfeng-local",
        "requestDigest": preflight["requestDigest"],
        "requestRevision": preflight["requestRevision"],
        "selectionRevision": revision,
        "clusterUID": preflight["clusterUID"],
        "resources": resources,
        "podListResourceVersion": pods["metadata"]["resourceVersion"],
    }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise ValueError("redirect refused")


def reader() -> Callable[[str], dict[str, Any]]:
    """Use the mounted workload identity and verified serving CA; no ambient proxies."""
    token = (ACCOUNT / "token").read_text(encoding="ascii").strip()
    require(token and "\n" not in token and "\r" not in token, "invalid workload token")
    opener = build_opener(
        ProxyHandler({}),
        NoRedirect(),
        HTTPSHandler(context=ssl.create_default_context(cafile=str(ACCOUNT / "ca.crt"))),
    )

    def read(path: str) -> dict[str, Any]:
        require(
            path
            in {APPLICATION + name for name in NAMES}
            | {PLATFORM, PODS, "/api/v1/namespaces/kube-system"},
            "unexpected observation target",
        )
        with opener.open(
            Request(API + path, headers={"Authorization": "Bearer " + token}), timeout=10
        ) as response:
            raw = response.read(1048577)
            require(response.status == 200 and len(raw) <= 1048576, "invalid API response")
        value = json.loads(raw, object_pairs_hook=unique)
        require(isinstance(value, dict), "invalid API object")
        return value

    return read


def main() -> None:
    root = Path("/inputs/selection")
    revision = os.environ["SELECTION_REVISION"]
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "selection must be a full revision")
    require(
        (root / ".git/HEAD").read_text(encoding="ascii").strip() == revision,
        "checkout differs from selection",
    )
    lock = root / "deployments/local-control-plane.json"
    require(
        not lock.is_symlink()
        and lock.resolve().is_relative_to(root.resolve())
        and lock.stat().st_size <= 1048576,
        "invalid lock path or size",
    )
    preflight = json.loads(os.environ["PREFLIGHT_RESULT"], object_pairs_hook=unique)
    read = reader()
    paths = [*(APPLICATION + name for name in NAMES), PLATFORM]
    before = [read(path) for path in paths]
    pods = read(PODS)
    after = [read(path) for path in paths]
    namespace = read("/api/v1/namespaces/kube-system")
    result = verify(preflight, lock.read_bytes(), revision, before, pods, after, namespace)
    Path("/tmp/checkpoint.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    print("Maintenance quiescence observed; migration still requires independent live checks")


def cli() -> int:
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "Maintenance checkpoint refused: inspect pause, ownership, selection and API access; no success evidence",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
