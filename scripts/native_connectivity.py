"""Operate the loopback-only Windows gateway without changing cluster workloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

if __package__:
    from . import cluster, control_plane_health
else:
    import cluster
    import control_plane_health

ROOT = Path(__file__).resolve().parents[1]
CADDY_VERSION = "v2.11.7"
SESSION_SECONDS = 3600
MAX_RESPONSE_BYTES = 64 * 1024
ORIGINS = {
    "controlPlane": "https://127.0.0.1:8443",
    "s3": "https://127.0.0.1:9443",
    "target": "https://127.0.0.1:10443",
}
FORWARDS = {
    "forward-api": ("control-plane", "18080:8080"),
    "forward-s3": ("seaweedfs", "18333:8333"),
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects rather than forwarding credentials or masking endpoint drift."""

    def redirect_request(
        self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def command(action: str, root: Path = ROOT) -> list[str]:
    """Build a fixed gateway command; no credential or endpoint overrides are accepted."""
    if action in FORWARDS:
        service, ports = FORWARDS[action]
        return [
            "kubectl",
            "--kubeconfig",
            str(cluster.kubeconfig(root)),
            "--context",
            cluster.CONTEXT,
            "--request-timeout=10s",
            "-n",
            "perf-platform",
            "port-forward",
            "--address=127.0.0.1",
            "--pod-running-timeout=30s",
            f"service/{service}",
            ports,
        ]
    if action == "gateway":
        return ["caddy", "run", "--config", "native-gateway/Caddyfile", "--adapter", "caddyfile"]
    if action == "validate":
        return ["caddy", "adapt", "--config", "native-gateway/Caddyfile", "--adapter", "caddyfile"]
    raise ValueError("Unknown native connectivity action")


def service_command(action: str, root: Path) -> list[str]:
    """Reuse the dedicated API/context policy without starting a forwarding session."""
    forward = command(action, root)
    service, _ = FORWARDS[action]
    return forward[: forward.index("port-forward")] + ["get", "service", service, "-o", "json"]


def target_identity(root: Path = ROOT) -> str:
    """Fingerprint the exact controlled fixture bytes and names, independent of checkout path."""
    digest = hashlib.sha256()
    for name in ("index.html", "search.js"):
        content = (root / "native-gateway/search" / name).read_bytes()
        digest.update(name.encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


def verify_forward(action: str, root: Path) -> None:
    """Require the dedicated cluster and expected Service before opening a host tunnel."""
    cluster.verify_local_docker()
    cluster.verify_context(root)
    value = json.loads(cluster.run(service_command(action, root)))
    if not isinstance(value, dict) or any(
        not isinstance(value.get(field), dict) for field in ("metadata", "spec")
    ):
        raise ValueError("Invalid Service response; forwarding was not started")
    if action == "forward-api":
        control_plane_health.validate_service(value)
    else:
        spec = value.get("spec", {})
        if (
            value.get("metadata", {}).get("name") != "seaweedfs"
            or value.get("metadata", {}).get("namespace") != "perf-platform"
            or spec.get("type") != "ClusterIP"
            or spec.get("clusterIP") != "None"
            or spec.get("selector")
            != {"app.kubernetes.io/instance": "seaweedfs", "app.kubernetes.io/name": "seaweedfs"}
            or spec.get("ports")
            != [{"name": "s3", "port": 8333, "targetPort": "s3", "protocol": "TCP"}]
        ):
            raise ValueError("SeaweedFS Service differs from the reviewed local release")


def verify_serving_build(build_info: str) -> None:
    """Reject known-vulnerable Go/x/net builds; this is not a substitute for an advisory audit."""
    go = re.search(r"^go\s+go(\d+)\.(\d+)\.(\d+)\s*$", build_info, re.MULTILINE)
    net = re.search(r"^dep\s+golang.org/x/net\s+v(\d+)\.(\d+)\.(\d+)\s", build_info, re.MULTILINE)
    if (
        go is None
        or tuple(map(int, go.groups())) < (1, 26, 9)
        or net is None
        or tuple(map(int, net.groups())) < (0, 60, 0)
        or re.search(r"^=>", build_info, re.MULTILINE)
    ):
        raise ValueError(
            "Caddy serving requires reviewed Go >=1.26.9 and x/net >=v0.60.0 without replacements; see the connectivity guide"
        )


def run_tool(action: str, root: Path = ROOT) -> None:
    """Run one bounded foreground session or offline adaptation; never install trust or tools."""
    if action in FORWARDS:
        verify_forward(action, root)
    else:
        version = subprocess.run(["caddy", "version"], capture_output=True, check=True, timeout=10)
        observed_version = version.stdout.decode("utf-8").split()
        if not observed_version or observed_version[0] != CADDY_VERSION:
            raise ValueError(f"Use reviewed Caddy {CADDY_VERSION}")
        state = root / ".local/native-gateway"
        for path in (root / ".local", state):
            if path.resolve() != path or path.is_symlink():
                raise ValueError("Redirected gateway state is not allowed")
        if state.exists() and any(
            path.is_symlink() or path.is_junction() for path in state.rglob("*")
        ):
            raise ValueError("Redirected gateway state is not allowed")
        if action == "gateway":
            build_info = subprocess.run(
                ["caddy", "build-info"], capture_output=True, check=True, timeout=10
            )
            verify_serving_build(build_info.stdout.decode("utf-8"))
    timeout = 10 if action == "validate" else SESSION_SECONDS
    print(f"Starting {action}; stop with Ctrl+C (maximum {timeout}s).", flush=True)
    subprocess.run(
        command(action, root),
        cwd=root,
        check=True,
        timeout=timeout,
    )


def read_endpoint(url: str, context: ssl.SSLContext) -> tuple[int, bytes]:
    """Read a bounded credential-free response, with verified TLS and no redirects/proxies."""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), NoRedirect()
    )
    try:
        with opener.open(url, timeout=10) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError):
        raise ValueError(
            "TLS or endpoint unavailable; check CA trust, gateway and port-forward sessions"
        ) from None


def check(ca_file: Path, root: Path = ROOT) -> None:
    """Check TLS, readiness, anonymous S3 denial and exact target bytes; no objects are written."""
    context = ssl.create_default_context(cafile=str(ca_file))
    checks = [("control plane", ORIGINS["controlPlane"] + "/readyz", b"ok\n")]
    for name in ("index.html", "search.js"):
        expected = (root / "native-gateway/search" / name).read_bytes()
        checks.append((name, ORIGINS["target"] + "/" + name, expected))
    for label, url, expected in checks:
        status, content = read_endpoint(url, context)
        if status != 200 or len(content) > MAX_RESPONSE_BYTES or content != expected:
            raise ValueError(f"Unexpected {label} response; check gateway and selected fixture")
        print(f"Verified TLS and {label}.", flush=True)
    status, content = read_endpoint(ORIGINS["s3"] + "/perfeng-artifacts", context)
    if (
        status != 403
        or len(content) > MAX_RESPONSE_BYTES
        or b"<Code>AccessDenied</Code>" not in content
    ):
        raise ValueError(
            "Expected anonymous S3 AccessDenied; check tunnel, storage identity and bucket policy"
        )
    print("Verified TLS and anonymous S3 denial.")
    print(f"Controlled target SHA-256: {target_identity(root)}")
    print(
        "Connectivity only: worker authentication, signed PUT/internal read-back and native acquisition are not verified."
    )


def main(argv: list[str] | None = None) -> int:
    """Preview without external access; live checks and foreground sessions require --execute."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["gateway", "forward-api", "forward-s3", "validate", "check"]
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--ca-file", type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.execute:
            print(json.dumps(ORIGINS if args.action == "check" else command(args.action)))
            print(f"Controlled target SHA-256: {target_identity()}")
            print(
                "Preview only. Add --execute; no process, network, trust or state changes were made."
            )
        elif args.action == "check":
            if args.ca_file is None:
                raise ValueError(
                    "check requires --ca-file with the reviewed gateway root certificate"
                )
            check(args.ca_file)
        else:
            run_tool(args.action)
    except ValueError as error:
        print(f"Native connectivity error: {error}")
        return 1
    except (OSError, subprocess.SubprocessError):
        print(
            "Native connectivity failed; check tool version, dedicated cluster, CA and foreground sessions. External output withheld."
        )
        return 1
    except KeyboardInterrupt:
        print("Native connectivity session stopped; cluster resources and gateway CA retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
