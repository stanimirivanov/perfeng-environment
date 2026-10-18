"""Verify bounded release observations; Kubernetes waiting belongs to Argo resource templates."""

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class ReadinessRefused(ValueError):
    """A fixed diagnostic that contains no resource values or response bodies."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise ReadinessRefused(message)


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def verify(
    preflight: dict[str, Any],
    raw_lock: bytes,
    observations: dict[str, Any],
    selection_revision: str,
) -> dict[str, Any]:
    require(
        preflight.get("schemaVersion") == 1
        and preflight.get("kind") == "PerfengReleasePreflight"
        and preflight["status"] == "identity-verified"
        and preflight["environment"] == "perfeng-local"
        and preflight["selectionRevision"] == selection_revision,
        "preflight identity differs from the local selection",
    )
    require(
        "sha256:" + hashlib.sha256(raw_lock).hexdigest()
        == preflight["lockDigests"]["deployments/local-control-plane.json"],
        "selected deployment lock differs from preflight",
    )
    lock = json.loads(raw_lock, object_pairs_hook=unique)
    require(lock.get("schemaVersion") == 1, "unsupported deployment lock version")
    deployment = lock["deployment"]
    image = lock["controlPlane"]["image"]
    require(
        re.fullmatch(r"ghcr.io/stanimirivanov/perfeng-control-plane@sha256:[0-9a-f]{64}", image)
        and re.fullmatch(r"[0-9a-f]{40}", deployment["revision"])
        and deployment["repository"] == "https://github.com/stanimirivanov/perfeng-k8s.git",
        "unsupported deployment identity",
    )
    runtime = deployment["runtimeResources"]
    sources = [
        {
            "repository": deployment["repository"],
            "revision": deployment["revision"],
            "path": path,
            "image": image if chart else None,
            "runtimeConfigMap": runtime["runtimeConfigMap"] if chart else None,
            "registryConfigMap": runtime["registryConfigMap"] if chart else None,
        }
        for path, chart in (
            (deployment["chart"]["path"], True),
            (deployment["runtimePackage"]["path"], False),
        )
    ]
    app = observations["application"]
    require(
        app["name"] == "perfeng-local-control-plane"
        and app["namespace"] == "argocd"
        and app["uid"]
        and app["project"] == "perfeng-local"
        and app["destination"]
        == {"server": "https://kubernetes.default.svc", "namespace": "perf-platform"}
        and app["sources"] == sources
        and app["comparedSources"] == sources
        and app["revisions"] == [deployment["revision"], deployment["revision"]]
        and app["sync"] == "Synced"
        and app["health"] == "Healthy"
        and not app["conditions"]
        and app["operation"] in (None, "Succeeded"),
        "Application does not report the selected converged release",
    )
    resources = []
    for key, kind, name in (
        ("postgres", "StatefulSet", "postgres"),
        ("seaweedfs", "StatefulSet", "seaweedfs"),
        ("deployment", "Deployment", "control-plane"),
    ):
        observed = observations[key]
        require(
            observed["name"] == name
            and observed["namespace"] == "perf-platform"
            and observed["uid"]
            and type(observed["generation"]) is int
            and observed["generation"] > 0
            and observed["observedGeneration"] == observed["generation"]
            and all(observed[field] == 1 for field in ("desired", "replicas", "ready", "updated")),
            f"{name} is stale or not fully ready",
        )
        if kind == "StatefulSet":
            require(
                observed["currentRevision"]
                and observed["currentRevision"] == observed["updateRevision"],
                f"{name} has not completed its StatefulSet revision",
            )
        else:
            require(
                observed["available"] == 1
                and observed["unavailable"] == 0
                and observed["images"] == [image]
                and observed["runtimeConfigMaps"] == [runtime["runtimeConfigMap"]]
                and observed["registryConfigMaps"] == [runtime["registryConfigMap"]]
                and observed["runtimeSecrets"] == ["perfeng-control-plane-runtime"]
                and observed["imagePullSecrets"] == ["perfeng-control-plane-registry"]
                and observed["authenticationSecrets"] == ["perfeng-control-plane-authentication"],
                "Deployment differs from the selected runtime",
            )
        resources.append(
            {
                "kind": kind,
                "name": name,
                "namespace": "perf-platform",
                "uid": observed["uid"],
                "generation": observed["generation"],
                "observedGeneration": observed["observedGeneration"],
            }
        )
    service = observations["service"]
    require(
        service["name"] == "control-plane"
        and service["namespace"] == "perf-platform"
        and service["uid"]
        and service["type"] == "ClusterIP"
        and service["selector"]
        == {
            "app.kubernetes.io/name": "perfeng-control-plane",
            "app.kubernetes.io/instance": "control-plane",
        }
        and service["ports"]
        == [{"name": "http", "port": 8080, "protocol": "TCP", "targetPort": "http"}],
        "Service differs from the reviewed control-plane endpoint",
    )
    return {
        "schemaVersion": 1,
        "kind": "PerfengReleaseReadiness",
        "scope": "control-plane-foundation",
        "status": "readiness-verified",
        "requestDigest": preflight["requestDigest"],
        "requestRevision": preflight["requestRevision"],
        "selectionRevision": selection_revision,
        "clusterUID": preflight["clusterUID"],
        "applicationUID": app["uid"],
        "serviceUID": service["uid"],
        "deploymentRevision": deployment["revision"],
        "image": image,
        "resources": resources,
        "checkedEndpoints": ["livez", "readyz", "metrics"],
    }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise ValueError("operational endpoint redirected")


def probe_endpoints() -> None:
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for endpoint in ("livez", "readyz", "metrics"):
        try:
            with opener.open(
                f"http://control-plane.perf-platform.svc.cluster.local:8080/{endpoint}", timeout=10
            ) as response:
                body = response.read(1048577)
                require(response.status == 200 and len(body) <= 1048576, "invalid response")
                require(
                    body == b"ok\n"
                    if endpoint != "metrics"
                    else b"perfeng_control_plane_ready 1" in body.splitlines(),
                    "invalid readiness response",
                )
        except (OSError, HTTPError, ValueError) as error:
            raise ReadinessRefused(f"operational endpoint failed: {endpoint}") from error


def main() -> None:
    root = Path("/inputs/selection")
    revision = os.environ["SELECTION_REVISION"]
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "selection must be a full Git revision")
    require(
        (root / ".git/HEAD").read_text(encoding="ascii").strip() == revision,
        "checkout differs from selection",
    )
    lock_path = root / "deployments/local-control-plane.json"
    require(
        not lock_path.is_symlink() and lock_path.resolve().is_relative_to(root.resolve()),
        "lock escapes checkout",
    )
    require(lock_path.stat().st_size <= 1048576, "lock exceeds size limit")
    preflight = json.loads(os.environ["PREFLIGHT_RESULT"], object_pairs_hook=unique)
    observations = {
        name: json.loads(os.environ[name.upper()], object_pairs_hook=unique)
        for name in ("application", "postgres", "seaweedfs", "deployment", "service")
    }
    result = verify(preflight, lock_path.read_bytes(), observations, revision)
    probe_endpoints()
    Path("/tmp/readiness-result.json").write_text(
        json.dumps(result, sort_keys=True), encoding="utf-8"
    )
    print("Selected control-plane and foundation readiness verified")


def cli() -> int:
    """Report controlled diagnostics without vendor/resource input details."""
    try:
        main()
    except ReadinessRefused as error:
        print(f"Readiness refused: {error}", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "Readiness refused: invalid selection, observations or operational response",
            file=sys.stderr,
        )
    else:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(cli())
