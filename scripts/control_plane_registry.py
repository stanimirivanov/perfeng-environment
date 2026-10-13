"""Provision and verify local control-plane image-pull credentials."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import cluster
    from .control_plane_release import validate as validate_release
else:
    import cluster
    from control_plane_release import validate as validate_release

NAMESPACE = "perf-platform"
SECRET = "perfeng-control-plane-registry"
OWNER = "perfeng-environment"
REGISTRY = "ghcr.io"
USERNAME = "stanimirivanov"
TOKEN_ENDPOINT = "https://ghcr.io/token"
IMAGE_REPOSITORY = "stanimirivanov/perfeng-control-plane"
TOKEN_ENVIRONMENT_VARIABLE = "PERFENG_REPOSITORIES_READ_TOKEN"
TOKEN_LIMIT = 16 * 1024
MANIFEST_LIMIT = 1024 * 1024


def kubectl(root: Path) -> list[str]:
    """Return the explicitly bound local-cluster command prefix."""
    return [
        "kubectl",
        "--kubeconfig",
        str(cluster.kubeconfig(root)),
        "--context",
        cluster.CONTEXT,
        "--namespace",
        NAMESPACE,
        "--request-timeout=30s",
    ]


def valid_token(value: str) -> bool:
    """Return whether an opaque credential is safe to carry as one value."""
    return (
        len(value) >= 20
        and len(value) <= 4096
        and value.strip() == value
        and not any(character.isspace() or ord(character) < 32 for character in value)
    )


def docker_config(username: str, token: str) -> dict[str, Any]:
    """Build the single-registry Docker credential document."""
    if username != USERNAME or not valid_token(token):
        raise ValueError("Invalid GHCR credential input")
    authorization = base64.b64encode(f"{username}:{token}".encode()).decode("ascii")
    return {
        "auths": {
            REGISTRY: {
                "username": username,
                "password": token,
                "auth": authorization,
            }
        }
    }


def secret_document(username: str, token: str) -> dict[str, Any]:
    """Build the immutable, environment-owned Kubernetes pull Secret."""
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": SECRET,
            "namespace": NAMESPACE,
            "labels": {
                "app.kubernetes.io/managed-by": OWNER,
                "perfeng.io/purpose": "control-plane-image-pull",
            },
        },
        "type": "kubernetes.io/dockerconfigjson",
        "immutable": True,
        "stringData": {".dockerconfigjson": json.dumps(docker_config(username, token))},
    }


def decode_secret(value: dict[str, Any]) -> tuple[str, str]:
    """Validate and return credentials from an existing owned pull Secret."""
    try:
        if (
            value["type"] != "kubernetes.io/dockerconfigjson"
            or value["immutable"] is not True
            or value["metadata"]["name"] != SECRET
            or value["metadata"]["namespace"] != NAMESPACE
            or value["metadata"]["labels"]
            != {
                "app.kubernetes.io/managed-by": OWNER,
                "perfeng.io/purpose": "control-plane-image-pull",
            }
            or set(value["data"]) != {".dockerconfigjson"}
        ):
            raise ValueError()
        encoded = base64.b64decode(value["data"][".dockerconfigjson"], validate=True)
        config = json.loads(encoded)
        if not isinstance(config, dict) or set(config) != {"auths"}:
            raise ValueError()
        auths = config["auths"]
        if not isinstance(auths, dict) or set(auths) != {REGISTRY}:
            raise ValueError()
        credential = auths[REGISTRY]
        if not isinstance(credential, dict) or set(credential) != {
            "username",
            "password",
            "auth",
        }:
            raise ValueError()
        username = credential["username"]
        token = credential["password"]
        if not isinstance(username, str) or not isinstance(token, str):
            raise ValueError()
        if credential["auth"] != base64.b64encode(f"{username}:{token}".encode()).decode("ascii"):
            raise ValueError()
        docker_config(username, token)
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(
            f"Invalid owned immutable Secret {SECRET}; replace it through explicit recovery"
        ) from None
    return username, token


def read_response(response: Any, limit: int) -> bytes:
    """Read one bounded HTTP response body."""
    content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError("GHCR returned an oversized authentication response")
    return content


def verify_registry_credentials(
    username: str,
    token: str,
    image: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> bytes:
    """Verify a reviewed platform package digest and return its bounded manifest bytes."""
    selected = re.fullmatch(
        r"ghcr\.io/(stanimirivanov/(?:perfeng-control-plane|perfeng-native-search))@sha256:([0-9a-f]{64})",
        image,
    )
    if selected is None:
        raise ValueError("Registry verification requires a reviewed digest-only platform image")
    repository, digest = selected.groups()
    config = docker_config(username, token)
    authorization = config["auths"][REGISTRY]["auth"]
    query = urllib.parse.urlencode({"service": REGISTRY, "scope": f"repository:{repository}:pull"})
    token_request = urllib.request.Request(
        f"{TOKEN_ENDPOINT}?{query}",
        headers={"Authorization": f"Basic {authorization}"},
    )
    try:
        with opener(token_request, timeout=30) as response:
            payload = json.loads(read_response(response, TOKEN_LIMIT))
        bearer = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(bearer, str) or not valid_token(bearer):
            raise ValueError("GHCR returned an invalid bearer credential")

        manifest_request = urllib.request.Request(
            f"https://{REGISTRY}/v2/{repository}/manifests/sha256:{digest}",
            headers={
                "Authorization": f"Bearer {bearer}",
                "Accept": (
                    "application/vnd.oci.image.index.v1+json, "
                    "application/vnd.oci.image.manifest.v1+json, "
                    "application/vnd.docker.distribution.manifest.list.v2+json, "
                    "application/vnd.docker.distribution.manifest.v2+json"
                ),
            },
        )
        with opener(manifest_request, timeout=30) as response:
            content = read_response(response, MANIFEST_LIMIT)
            actual = response.headers.get("Docker-Content-Digest")
        if actual != f"sha256:{digest}":
            raise ValueError("GHCR returned a different manifest digest")
        return content
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise ValueError("GHCR authentication or manifest verification failed") from error


def ensure_secret(
    root: Path,
    image: str,
    *,
    create: bool,
    read_token: Callable[[str], str] = getpass.getpass,
    read_environment: Callable[[str], str | None] = os.environ.get,
) -> tuple[str, str]:
    """Validate and reuse credentials, or create them after registry verification."""
    kube = kubectl(root)
    current = cluster.run([*kube, "get", "secret", SECRET, "--ignore-not-found", "-o", "json"])
    if current.strip():
        credentials = decode_secret(json.loads(current))
        verify_registry_credentials(*credentials, image)
        print("Reusing existing control-plane registry credentials (not rotated).", flush=True)
        return credentials
    if not create:
        raise ValueError("Control-plane registry credentials are missing; run deploy first")

    token = read_environment(TOKEN_ENVIRONMENT_VARIABLE)
    if token is None:
        print(
            f"{TOKEN_ENVIRONMENT_VARIABLE} is not set; enter the token below (input is hidden).",
            flush=True,
        )
        token = read_token("GHCR package-read token: ")
    else:
        print(f"Using {TOKEN_ENVIRONMENT_VARIABLE} from the environment.", flush=True)
    verify_registry_credentials(USERNAME, token, image)
    document = secret_document(USERNAME, token)
    cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(document).encode())
    print("Created immutable local control-plane registry credentials.", flush=True)
    return USERNAME, token


def execute(action: str, root: Path = cluster.ROOT) -> None:
    """Provision or verify the pull Secret in the verified local cluster."""
    release = validate_release()
    cluster.verify_local_docker()
    cluster.verify_context(root)
    kube = kubectl(root)
    cluster.run([*kube, "get", "namespace", NAMESPACE, "-o", "name"])
    ensure_secret(root, release.image, create=action == "deploy")
    print(f"Local control-plane registry action completed: {action}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["deploy", "health"])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            execute(args.action)
        else:
            print("Preview: verify the local cluster, namespace, locked image and pull Secret.")
            if args.action == "deploy":
                print(
                    "Missing credentials use PERFENG_REPOSITORIES_READ_TOKEN, "
                    "or a hidden prompt when it is unset."
                )
            print("Preview only. Add --execute to access GHCR or Kubernetes.")
    except (OSError, ValueError, TypeError, yaml.YAMLError, json.JSONDecodeError) as error:
        print(f"Control-plane registry error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
