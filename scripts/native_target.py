"""Build and verify the isolated controlled browser candidate; never deploy or publish."""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

if __package__:
    from . import cluster
    from .native_connectivity import ROOT, target_identity
else:
    import cluster
    from native_connectivity import ROOT, target_identity

REPOSITORY = "ghcr.io/stanimirivanov/perfeng-native-search"
SOURCE = "https://github.com/stanimirivanov/perfeng-environment"
REVISION = re.compile(r"[0-9a-f]{40}")
IMAGE = re.compile(
    rf"(?:{re.escape(REPOSITORY)}|perfeng-native-search)"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|@sha256:[0-9a-f]{64})"
)
DIGEST = re.compile(rf"{re.escape(REPOSITORY)}@sha256:[0-9a-f]{{64}}")
OWNER = "perfeng-native-target-verification"
CSP = "default-src 'none'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"


def validate(root: Path = ROOT) -> dict[str, Any]:
    """Validate packaging offline without touching Docker, state or the registry."""
    directory = root / "native-gateway/search"
    value = json.loads((directory / "image.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "version",
        "repository",
        "platform",
        "baseImage",
    }:
        raise ValueError("Invalid native target image selection")
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or value["version"] != "1.0.0"
        or value["repository"] != REPOSITORY
        or value["platform"] != "linux/amd64"
        or not isinstance(value["baseImage"], str)
        or re.fullmatch(
            r"docker\.io/nginxinc/nginx-unprivileged:[0-9.]+-alpine-slim@sha256:[0-9a-f]{64}",
            value["baseImage"],
        )
        is None
    ):
        raise ValueError("Invalid native target immutable identity")
    dockerfile = (directory / "Dockerfile").read_text(encoding="utf-8")
    for instruction in (
        f"FROM {value['baseImage']}",
        "USER 101:101",
        'ENTRYPOINT ["nginx"]',
        'CMD ["-g", "daemon off;"]',
        "COPY index.html search.js /usr/share/nginx/html/",
    ):
        if instruction not in dockerfile.splitlines():
            raise ValueError("Native target Dockerfile differs from its reviewed packaging")
    if (directory / ".dockerignore").read_text(encoding="utf-8").splitlines() != [
        "*",
        "!Dockerfile",
        "!nginx.conf",
        "!index.html",
        "!search.js",
        "!LICENSE",
    ]:
        raise ValueError("Native target build context is not restricted")
    if (directory / "LICENSE").read_text(encoding="utf-8").strip() != (root / "LICENSE").read_text(
        encoding="utf-8"
    ).strip():
        raise ValueError("Native target license differs from the project license")
    return {**value, "targetSha256": target_identity(root)}


def identity(image: str, revision: str) -> None:
    """Reject argument-like images and synthetic provenance outside a local development build."""
    if IMAGE.fullmatch(image) is None:
        raise ValueError("Invalid native target image reference")
    if REVISION.fullmatch(revision) is None and not (
        revision == "development" and image.startswith("perfeng-native-search:")
    ):
        raise ValueError("Native target requires an exact source revision")


def build_failure_reason(output: str) -> str:
    """Classify known backend signals without copying paths, credentials or raw logs."""
    lowered = output.lower()
    for signals, reason in (
        (("no space left on device",), "[BUILD_STORAGE] Runner storage is exhausted"),
        (
            ("toomanyrequests", "unauthenticated pull rate limit"),
            "[BUILD_REGISTRY_RATE_LIMIT] Registry pull rate limit reached",
        ),
        (
            ("pull access denied", "failed to authorize", "unauthorized: authentication required"),
            "[BUILD_REGISTRY_AUTH] Registry authorization failed",
        ),
        (
            ("x509:", "certificate verify failed"),
            "[BUILD_REGISTRY_TLS] Certificate verification failed",
        ),
        (
            (
                "tls handshake timeout",
                "i/o timeout",
                "connection reset by peer",
                "no such host",
                "temporary failure in name resolution",
            ),
            "[BUILD_NETWORK] Registry/build transport or name resolution failed",
        ),
        (
            ("cannot connect to the docker daemon", "is the docker daemon running"),
            "[BUILD_DAEMON] Docker daemon connection failed",
        ),
        (
            ("buildx component is missing or broken", "buildx is not a docker command"),
            "[BUILD_BUILDER] Buildx is missing or broken",
        ),
        (("failed to read dockerfile",), "[BUILD_CONTEXT] Dockerfile could not be read"),
        (
            ("manifest unknown", "no matching manifest for linux/amd64"),
            "[BUILD_MANIFEST] Base manifest lookup or platform selection failed",
        ),
        (
            ("failed to resolve source metadata",),
            "[BUILD_BASE_RESOLUTION] Base metadata resolution failed; check registry, network and manifest access",
        ),
    ):
        if any(signal in lowered for signal in signals):
            return reason
    return "[BUILD_UNKNOWN] Backend cause is unclassified; no raw output was printed"


def docker(arguments: list[str], timeout: int = 30) -> str:
    """Run one bounded Docker operation with progress and safe errors, never raw logs."""
    print(f"Native target: docker {arguments[0]}...", file=sys.stderr, flush=True)
    try:
        result = subprocess.run(
            ["docker", *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            f"Native target Docker {arguments[0]} was unavailable or timed out"
        ) from error
    if result.returncode != 0:
        reason = ""
        if arguments[0] == "build":
            reason = ": " + build_failure_reason(result.stderr + "\n" + result.stdout)
        raise ValueError(
            f"Native target Docker {arguments[0]} failed (exit {result.returncode}){reason}"
        )
    return result.stdout.strip()


def build_command(image: str, revision: str, root: Path = ROOT) -> list[str]:
    """Construct the explicit build with a narrow context and exact content provenance."""
    identity(image, revision)
    selected = validate(root)
    if "@" in image:
        raise ValueError("A build requires a temporary tag, not a digest")
    return [
        "build",
        "--platform=linux/amd64",
        "--build-arg",
        f"VCS_REF={revision}",
        "--build-arg",
        f"TARGET_SHA256={selected['targetSha256']}",
        "--tag",
        image,
        str(root / "native-gateway/search"),
    ]


def verify_metadata(value: object, revision: str, root: Path = ROOT) -> None:
    """Require actual image architecture, unprivileged entry point and expected labels."""
    selected = validate(root)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError("Native target image metadata is invalid")
    image = value[0]
    config = image.get("Config", {})
    labels = config.get("Labels", {}) if isinstance(config, dict) else {}
    if (
        not isinstance(labels, dict)
        or any(
            labels.get(name) != expected
            for name, expected in {
                "org.opencontainers.image.source": SOURCE,
                "org.opencontainers.image.revision": revision,
                "org.opencontainers.image.version": selected["version"],
                "dev.perfeng.target.sha256": selected["targetSha256"],
            }.items()
        )
        or image.get("Os") != "linux"
        or image.get("Architecture") != "amd64"
        or (
            not isinstance(config, dict)
            or config.get("User") != "101:101"
            or config.get("Entrypoint") != ["nginx"]
            or config.get("Cmd") != ["-g", "daemon off;"]
        )
    ):
        raise ValueError("Native target image differs from reviewed identity or runtime policy")


def probe(port: int, path: str, method: str = "GET") -> tuple[int, bytes, dict[str, str]]:
    """Read only bounded loopback HTTP; no redirects, proxies or credentials are used."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read(65537)
        if len(body) > 65536:
            raise ValueError("Native target response exceeds its bound")
        return response.status, body, {name.lower(): value for name, value in response.getheaders()}
    finally:
        connection.close()


def verify_http(port: int, root: Path = ROOT) -> None:
    """Check exact fixture bytes, method/path denial and no-store/CSP on the running image."""
    directory = root / "native-gateway/search"
    for path, content in (
        ("/", (directory / "index.html").read_bytes()),
        ("/index.html", (directory / "index.html").read_bytes()),
        ("/search.js", (directory / "search.js").read_bytes()),
        ("/healthz", b"ready\n"),
    ):
        status, actual, headers = probe(port, path)
        if (
            status != 200
            or actual != content
            or headers.get("cache-control") != "no-store"
            or (
                headers.get("x-content-type-options") != "nosniff"
                or headers.get("content-security-policy") != CSP
            )
        ):
            raise ValueError("Native target content or security headers differ")
    if probe(port, "/", "HEAD")[:2] != (200, b""):
        raise ValueError("Native target HEAD behavior differs")
    for path in ("/Dockerfile", "/nginx.conf", "/.env", "/search.js/extra"):
        if probe(port, path)[0] != 404:
            raise ValueError("Native target exposed an unexpected path")
    for method in ("POST", "PUT", "DELETE"):
        if probe(port, "/", method)[0] != 405:
            raise ValueError("Native target accepted a modifying method")


def verify(image: str, revision: str, root: Path = ROOT) -> None:
    """Verify one isolated disposable container; cleanup touches only its exact owned ID."""
    identity(image, revision)
    verify_metadata(json.loads(docker(["image", "inspect", image])), revision, root)
    invocation = uuid.uuid4().hex
    container = docker(
        [
            "run",
            "--detach",
            "--name",
            f"perfeng-native-search-check-{invocation}",
            "--label",
            f"dev.perfeng.owner={OWNER}",
            "--label",
            f"dev.perfeng.invocation={invocation}",
            "--platform=linux/amd64",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--pids-limit=64",
            "--memory=64m",
            "--cpus=0.5",
            "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16m",
            "--publish=127.0.0.1::8080",
            image,
        ]
    )
    if re.fullmatch(r"[0-9a-f]{64}", container) is None:
        raise ValueError("Native target Docker returned an invalid container identity")
    failure: Exception | None = None
    try:
        bindings = json.loads(
            docker(["inspect", "--format", "{{json .NetworkSettings.Ports}}", container])
        )
        values = bindings.get("8080/tcp") if isinstance(bindings, dict) else None
        if (
            not isinstance(values, list)
            or len(values) != 1
            or not isinstance(values[0], dict)
            or values[0].get("HostIp") != "127.0.0.1"
        ):
            raise ValueError("Native target listener is not loopback-only")
        port = int(values[0]["HostPort"])
        if not 1 <= port <= 65535:
            raise ValueError("Native target returned an invalid listener port")
        deadline = time.monotonic() + 15
        while True:
            try:
                ready = probe(port, "/healthz")[:2] == (200, b"ready\n")
            except (OSError, http.client.HTTPException):
                ready = False
            if ready:
                break
            if time.monotonic() >= deadline:
                raise ValueError("Native target did not become ready")
            time.sleep(0.2)
        verify_http(port, root)
    except (OSError, ValueError, KeyError, http.client.HTTPException) as error:
        failure = error
    try:
        labels = json.loads(docker(["inspect", "--format", "{{json .Config.Labels}}", container]))
        if (
            not isinstance(labels, dict)
            or labels.get("dev.perfeng.owner") != OWNER
            or labels.get("dev.perfeng.invocation") != invocation
        ):
            raise ValueError("Native target verification ownership changed; container retained")
        docker(["rm", "--force", container])
    except (OSError, ValueError) as cleanup:
        if failure is not None:
            raise ExceptionGroup(
                "Native target verification and cleanup failed", [failure, cleanup]
            ) from cleanup
        raise
    if failure is not None:
        raise failure


def reference(image: str) -> str:
    """Select exactly one publisher repository digest after Docker push; never invent one."""
    if IMAGE.fullmatch(image) is None:
        raise ValueError("Invalid native target image reference")
    values = json.loads(docker(["image", "inspect", "--format", "{{json .RepoDigests}}", image]))
    matches = (
        {value for value in values if isinstance(value, str) and DIGEST.fullmatch(value)}
        if isinstance(values, list)
        else set()
    )
    if len(matches) != 1:
        raise ValueError("Native target has no unique published repository digest")
    return matches.pop()


def main(argv: list[str] | None = None) -> int:
    """Preview all container actions by default; publication belongs to the opt-in workflow."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "build", "verify", "reference"))
    parser.add_argument("--image", default="perfeng-native-search:local-check")
    parser.add_argument("--revision", default="development")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        selected = validate()
        identity(args.image, args.revision)
        if args.action == "validate":
            print(json.dumps(selected, sort_keys=True))
        elif not args.execute:
            command = build_command(args.image, args.revision) if args.action == "build" else None
            print(
                json.dumps(
                    {
                        "action": args.action,
                        "image": args.image,
                        "sourceRevision": args.revision,
                        "command": command,
                        "execute": False,
                    }
                )
            )
        elif args.action == "reference":
            with contextlib.redirect_stdout(sys.stderr):
                cluster.verify_local_docker()
            print(reference(args.image))
        else:
            with contextlib.redirect_stdout(sys.stderr):
                cluster.verify_local_docker()
            if args.action == "build":
                docker(build_command(args.image, args.revision), timeout=300)
            verify(args.image, args.revision)
            print(
                json.dumps(
                    {
                        "image": args.image,
                        "sourceRevision": args.revision,
                        "targetSha256": selected["targetSha256"],
                        "platform": selected["platform"],
                        "published": False,
                    }
                )
            )
        return 0
    except (OSError, ValueError, ExceptionGroup, http.client.HTTPException) as error:
        print(f"Native target error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
