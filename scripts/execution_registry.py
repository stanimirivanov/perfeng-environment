"""Explicitly provision or verify pull access for the selected execution images."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

if __package__:
    from . import cluster
    from . import control_plane_registry as registry
    from . import control_plane_runtime as runtime
    from .control_plane_release import DeploymentLock
    from .control_plane_release import validate as validate_release
else:
    import cluster
    import control_plane_registry as registry
    import control_plane_runtime as runtime
    from control_plane_release import DeploymentLock
    from control_plane_release import validate as validate_release

SECRET = "perfeng-execution-registry"
NAMESPACE = "perf-generators"
IDENTITY = {"name": SECRET, "namespace": NAMESPACE, "purpose": "execution-image-pull"}
IMAGE = re.compile(
    r"ghcr\.io/stanimirivanov/(perfeng-k6|perfeng-kube-burner|perfeng-analysis)@sha256:[0-9a-f]{64}"
)


def selected_images(release: DeploymentLock) -> tuple[str, ...]:
    """Read exact pinned registry bytes, rejecting unexpected package identities."""
    entries = json.loads(runtime.registry_files(release)["registry.json"])["entries"]
    images = set()
    for entry in entries:
        images.add(entry["rawProducer"]["artifact"]["image"])
        images.add(entry["normalizerProducer"]["image"])
        images.add(entry["reportProducer"]["image"])
    if any(not isinstance(image, str) or IMAGE.fullmatch(image) is None for image in images):
        raise ValueError("Execution registry contains an unreviewed image identity")
    if {image.split("/")[-1].split("@")[0] for image in images} != {
        "perfeng-k6",
        "perfeng-kube-burner",
        "perfeng-analysis",
    }:
        raise ValueError("Execution registry must select all three reviewed packages")
    return tuple(sorted(images))


def ensure_secret(root: Path, images: tuple[str, ...], *, create: bool) -> None:
    """Verify every digest before creation; never copy or rotate another Secret."""
    if not images:
        raise ValueError("Execution image selection is empty")
    kube = registry.kubectl(root, namespace=NAMESPACE)
    text = cluster.run([*kube, "get", "secret", SECRET, "--ignore-not-found", "-o", "json"])
    if text.strip():
        credentials = registry.decode_secret(json.loads(text), **IDENTITY)
    elif not create:
        raise ValueError(
            "Execution registry credentials are missing; run execution_registry deploy"
        )
    else:
        token = os.environ.get(registry.TOKEN_ENVIRONMENT_VARIABLE)
        if token is None:
            raise ValueError(
                f"Set {registry.TOKEN_ENVIRONMENT_VARIABLE} before explicit deployment"
            )
        credentials = (registry.USERNAME, token)
    for image in images:
        if IMAGE.fullmatch(image) is None:
            raise ValueError("Execution registry requires a reviewed digest-only image")
        manifest = registry.verify_registry_credentials(*credentials, image)
        if hashlib.sha256(manifest).hexdigest() != image.split("@sha256:")[1]:
            raise ValueError("Execution image manifest bytes do not match the selected digest")
        print(f"Verified execution image pull access: {image}", flush=True)
    if not text.strip():
        document = registry.secret_document(*credentials, **IDENTITY)
        cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(document).encode())
    print(
        "Execution registry credentials verified; existing credentials were not rotated.",
        flush=True,
    )


def execute(action: str, root: Path = cluster.ROOT) -> None:
    """Bind credential access to the dedicated cluster and pinned runtime selection."""
    images = selected_images(validate_release())
    with cluster.command_deadline(60):
        cluster.verify_local_docker()
        cluster.verify_context(root)
    ensure_secret(root, images, create=action == "deploy")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["deploy", "health"])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            execute(args.action)
        else:
            print(
                "Preview: verify all selected execution images and the dedicated immutable pull Secret."
            )
            print(
                "Deploy reads the environment token only when the Secret is absent; health never creates."
            )
            print("Preview only. Add --execute for GHCR and dedicated-cluster access.")
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Execution registry error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
