"""Bounded artifact and HTTP adapters; Argo owns all orchestration and Kubernetes writes."""

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

import release_policy_v2 as policy

API, NAMES = policy.API, policy.NAMES
PLATFORM = "/apis/apps/v1/namespaces/perf-platform/deployments/control-plane"
APPLICATION = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/"
PODS = "/api/v1/namespaces/perf-platform/pods?limit=1&labelSelector=app.kubernetes.io%2Fname%3Dperfeng-control-plane%2Capp.kubernetes.io%2Finstance%3Dcontrol-plane"
ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")


def decode(raw: str | bytes) -> Any:
    return json.loads(
        raw.decode("utf-8") if isinstance(raw, bytes) else raw, object_pairs_hook=policy.unique
    )


def inputs(*names: str) -> dict[str, Any]:
    return {name: decode(os.environ[name.upper()]) for name in names}


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def commit(root: Path, revision: str) -> None:
    policy.require(re.fullmatch(r"[0-9a-f]{40}", revision), "revision must be a full Git commit")
    policy.require(
        (root / ".git/HEAD").read_text(encoding="ascii").strip() == revision,
        "Git artifact checkout differs from selection/request",
    )


def checked_bytes(root: Path, relative: str, limit: int) -> bytes:
    path = root / relative
    policy.require(
        not path.is_symlink() and path.resolve().is_relative_to(root.resolve()),
        "Git artifact path escapes its checkout",
    )
    try:
        policy.require(path.stat().st_size <= limit, "Git artifact exceeds size limit")
        return path.read_bytes()
    except OSError:
        raise policy.ReadinessRefused("Git artifact is unavailable") from None


def request_bytes(root: Path) -> bytes:
    commit(root, os.environ["REQUEST_REVISION"])
    path = os.environ["REQUEST_PATH"]
    policy.require(
        re.fullmatch(r"deployments/release-requests/[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.json", path),
        "request path is outside the reviewed request directory",
    )
    raw = checked_bytes(root, path, 65536)
    policy.require(
        digest(raw) == os.environ.get("REQUEST_SHA256", os.environ.get("REQUEST_DIGEST")),
        "request bytes differ from the submitted digest",
    )
    return raw


def selection() -> tuple[bytes, str]:
    root, revision = Path("/inputs/selection"), os.environ["SELECTION_REVISION"]
    commit(root, revision)
    return checked_bytes(root, "deployments/local-control-plane.json", 1048576), revision


def output(path: str, value: Any) -> None:
    Path(path).write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise ValueError("redirect refused")


def probe_endpoints() -> None:
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for endpoint in ("livez", "readyz", "metrics"):
        try:
            with opener.open(
                f"http://control-plane.perf-platform.svc.cluster.local:8080/{endpoint}", timeout=10
            ) as response:
                body = response.read(1048577)
                policy.require(response.status == 200 and len(body) <= 1048576, "invalid response")
                policy.require(
                    body == b"ok\n"
                    if endpoint != "metrics"
                    else b"perfeng_control_plane_ready 1" in body.splitlines(),
                    "invalid readiness response",
                )
        except (OSError, ValueError) as error:
            raise policy.ReadinessRefused(f"operational endpoint failed: {endpoint}") from error


def reader() -> Callable[[str], dict[str, Any]]:
    """Use the mounted workload identity and verified serving CA; no ambient proxies."""
    token = (ACCOUNT / "token").read_text(encoding="ascii").strip()
    policy.require(token and "\n" not in token and "\r" not in token, "invalid workload token")
    opener = build_opener(
        ProxyHandler({}),
        NoRedirect(),
        HTTPSHandler(context=ssl.create_default_context(cafile=str(ACCOUNT / "ca.crt"))),
    )

    def read(path: str) -> dict[str, Any]:
        policy.require(
            path
            in {APPLICATION + name for name in NAMES}
            | {PLATFORM, PODS, "/api/v1/namespaces/kube-system"},
            "unexpected observation target",
        )
        with opener.open(
            Request(API + path, headers={"Authorization": "Bearer " + token}), timeout=10
        ) as response:
            raw = response.read(1048577)
            policy.require(response.status == 200 and len(raw) <= 1048576, "invalid API response")
        value = json.loads(raw, object_pairs_hook=policy.unique)
        policy.require(isinstance(value, dict), "invalid API object")
        return value

    return read


def run_readiness() -> None:
    raw, revision = selection()
    value = inputs(
        "preflight_result", "application", "postgres", "seaweedfs", "deployment", "service"
    )
    result = policy.verify_readiness(value.pop("preflight_result"), raw, value, revision)
    probe_endpoints()
    output("/tmp/readiness-result.json", result)
    print("Selected control-plane and foundation readiness verified")


def run_quiescence() -> None:
    raw, revision = selection()
    read = reader()
    paths = [*(APPLICATION + name for name in NAMES), PLATFORM]
    before = [read(path) for path in paths]
    pods = read(PODS)
    after = [read(path) for path in paths]
    preflight = inputs("preflight_result")["preflight_result"]
    namespace = read("/api/v1/namespaces/kube-system")
    result = policy.verify_quiescence(preflight, raw, revision, before, pods, after, namespace)
    output("/tmp/checkpoint.json", result)
    print("Maintenance quiescence observed; migration still requires independent live checks")


def run_adoption() -> None:
    raw = request_bytes(Path("/inputs/request"))
    value = inputs("readiness", "root", "receipt", "pointer", "root_after")
    result = policy.plan_adoption(raw, **value)
    phase = os.environ["PHASE"]
    policy.require(phase in {"plan", "pointer", "finish"}, "invalid ledger phase")
    if phase != "plan":
        policy.require(
            value["receipt"] is not None and value["root_after"] is not None,
            "receipt or fresh root observation missing",
        )
    if phase == "finish":
        policy.require(value["pointer"] is not None, "stored pointer missing")
    for key, item in result.items():
        if key == "result" and phase != "finish":
            item = None
        if isinstance(item, dict) or item is None:
            output("/tmp/" + key, item)
        else:
            Path("/tmp/" + key).write_text(item, encoding="utf-8")
    print("Adoption observations verified; no workload or promotion operation performed")


def run_preflight() -> None:
    root = Path(os.environ.get("REQUEST_ROOT", "/inputs/request"))
    selected = Path(os.environ.get("SELECTION_ROOT", "/inputs/selection"))
    revision = os.environ["SELECTION_REVISION"]
    commit(selected, revision)
    raw = request_bytes(root)
    request = decode(raw)
    policy.require(
        request["selection"]["repository"] == policy.ROOT_SOURCE["repoURL"],
        "request repository differs from the reviewed origin",
    )
    policy.require(
        request["selection"]["revision"] == revision,
        "selected Git revision differs from the request",
    )
    uid = os.environ["OBSERVED_CLUSTER_UID"]
    policy.require(
        request["environment"]["clusterUID"] == uid, "target cluster UID differs from the request"
    )
    locks = request["selection"]["lockDigests"]
    for path, expected in locks.items():
        policy.require(
            re.fullmatch(r"deployments/[a-z][a-z0-9-]{0,62}\.json", path),
            "invalid deployment lock path",
        )
        policy.require(
            digest(checked_bytes(selected, path, 1048576)) == expected,
            f"deployment lock digest mismatch: {path}",
        )
    result = {
        "schemaVersion": 1,
        "kind": "PerfengReleasePreflight",
        "status": "identity-verified",
        "requestDigest": digest(raw),
        "requestRevision": os.environ["REQUEST_REVISION"],
        "requestPath": os.environ["REQUEST_PATH"],
        "selectionRevision": revision,
        "environment": request["environment"]["name"],
        "clusterUID": uid,
        "lockDigests": locks,
    }
    output(os.environ.get("RESULT_PATH", "/tmp/preflight-result.json"), result)
    print("Preflight identity verified")


def run_selection() -> None:
    result = inputs("preflight_result")["preflight_result"]
    policy.require(
        result["environment"] == os.environ["EXPECTED_ENVIRONMENT"]
        and result["lockDigests"].get(os.environ["EXPECTED_LOCK_PATH"])
        == os.environ["EXPECTED_LOCK_DIGEST"],
        "External-state v1 does not match the reviewed control-plane selection",
    )


def cli(mode: str) -> int:
    runners = {
        "readiness": run_readiness,
        "quiescence": run_quiescence,
        "adoption": run_adoption,
        "preflight": run_preflight,
        "selection": run_selection,
    }
    try:
        policy.require(mode in runners, "invalid verification mode")
        runners[mode]()
    except policy.ReadinessRefused as error:
        detail = (
            str(error)
            if mode in {"readiness", "preflight", "selection"}
            else "inspect reviewed inputs, ownership and observations; no success evidence"
        )
        print(f"{mode.title()} refused: {detail}", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError):
        print(
            f"{mode.title()} refused: invalid selection, observations or operational response",
            file=sys.stderr,
        )
    else:
        return 0
    return 1 if mode == "selection" else 2


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1] if len(sys.argv) == 2 else ""))
