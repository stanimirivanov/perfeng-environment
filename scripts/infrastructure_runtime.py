"""Run the immutable Kubernetes infrastructure selection or restore its handoff."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

if __package__:
    from . import cluster
    from .infrastructure_lock import portable_path
    from .infrastructure_release import (
        DEFAULT_INFRASTRUCTURE_ROOT,
        git,
        validate,
    )
else:
    import cluster
    from infrastructure_lock import portable_path
    from infrastructure_release import DEFAULT_INFRASTRUCTURE_ROOT, git, validate

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_LIMIT = 16 * 1024 * 1024
PROVIDER_TIMEOUT = 10_800
KUBECTL_TIMEOUT = 60
INFRASTRUCTURE_OWNER = "k8s-infrastructure"
LEGACY_OWNER = "perfeng-environment"
NAMESPACE = "argocd"
MODULES = {"cluster": "scripts.local_kind", "argocd": "scripts.argocd"}
ACTIONS = {
    "cluster": {"up", "ensure", "health", "down"},
    "argocd": {"validate", "bootstrap", "health"},
}
RUN_MODULE = (
    "import runpy,sys; "
    "source=sys.argv.pop(1); module=sys.argv.pop(1); "
    "sys.path.insert(0,source); sys.argv[0]=module; "
    "runpy.run_module(module,run_name='__main__')"
)


def extract_archive(content: bytes, destination: Path) -> None:
    """Extract one bounded Git archive without accepting links or traversal."""
    if len(content) > ARCHIVE_LIMIT:
        raise ValueError("Pinned infrastructure archive is too large")
    total = 0
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
        for member in archive.getmembers():
            name = member.name.rstrip("/") if member.isdir() else member.name
            portable_path(name, "infrastructure archive path")
            target = destination.joinpath(*PurePosixPath(name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError("Pinned infrastructure archive contains a link or special file")
            total += member.size
            if total > ARCHIVE_LIMIT:
                raise ValueError("Pinned infrastructure archive expands beyond its limit")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Pinned infrastructure archive contains an unreadable file")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())


def provider_command(
    source: Path,
    component: str,
    action: str,
    *,
    execute: bool,
    confirm_delete: str | None = None,
) -> list[str]:
    """Build an isolated command for one selected provider entry point."""
    if component not in ACTIONS or action not in ACTIONS[component]:
        raise ValueError("Unsupported infrastructure runtime action")
    command = [
        sys.executable,
        "-I",
        "-c",
        RUN_MODULE,
        str(source),
        MODULES[component],
        action,
        "--state-root",
        str(ROOT),
    ]
    if execute:
        command.append("--execute")
    if confirm_delete is not None:
        if component != "cluster" or action != "down":
            raise ValueError("Delete confirmation is valid only for cluster destruction")
        command.extend(["--confirm-delete", confirm_delete])
    return command


def invoke_provider(
    component: str,
    action: str,
    *,
    execute: bool,
    infrastructure_root: Path = DEFAULT_INFRASTRUCTURE_ROOT,
    confirm_delete: str | None = None,
) -> int:
    """Validate, materialize and invoke exactly the selected provider revision."""
    lock = validate(infrastructure_root=infrastructure_root)
    archive = git(
        infrastructure_root.resolve(strict=True), "archive", "--format=tar", lock.revision
    )
    with tempfile.TemporaryDirectory(prefix="perfeng-infrastructure-") as temporary:
        source = Path(temporary).resolve()
        extract_archive(archive, source)
        command = provider_command(
            source,
            component,
            action,
            execute=execute,
            confirm_delete=confirm_delete,
        )
        try:
            completed = subprocess.run(
                command,
                cwd=source,
                check=False,
                timeout=PROVIDER_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("Pinned infrastructure runtime could not complete") from error
    return completed.returncode


def kubectl() -> list[str]:
    """Return the exact local namespace command prefix for rollback."""
    return [
        "kubectl",
        "--kubeconfig",
        str(cluster.kubeconfig(ROOT)),
        "--context",
        cluster.CONTEXT,
        "--namespace",
        NAMESPACE,
        "--request-timeout=30s",
    ]


def namespace_command(kube: list[str]) -> list[str]:
    """Build the read-only Argo CD namespace query."""
    return [*kube, "get", "namespace", NAMESPACE, "-o", "json"]


def run_private(command: list[str], *, input_data: bytes | None = None) -> str:
    """Run a bounded rollback command without exposing its captured output."""
    try:
        completed = subprocess.run(
            command,
            input=input_data,
            capture_output=True,
            check=False,
            timeout=KUBECTL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Infrastructure rollback command could not complete") from error
    if completed.returncode:
        raise ValueError(
            f"{Path(command[0]).stem} failed (exit {completed.returncode}); inspect the local target"
        )
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Infrastructure rollback returned invalid UTF-8") from None


def validate_namespace_owner(output: str, owner: str) -> None:
    """Require the complete current or legacy namespace handoff identity."""
    value = json.loads(output)
    if not isinstance(value, dict):
        raise ValueError("Argo CD namespace has an unexpected ownership identity")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("Argo CD namespace has an unexpected ownership identity")
    labels = metadata.get("labels")
    if (
        value.get("apiVersion") != "v1"
        or value.get("kind") != "Namespace"
        or metadata.get("name") != NAMESPACE
        or not isinstance(labels, dict)
        or labels.get("app.kubernetes.io/managed-by") != owner
    ):
        raise ValueError("Argo CD namespace has an unexpected ownership identity")
    if owner == INFRASTRUCTURE_OWNER and (
        labels.get("app.kubernetes.io/name") != NAMESPACE
        or labels.get("app.kubernetes.io/part-of") != "kubernetes-substrate"
    ):
        raise ValueError("Argo CD namespace has incomplete infrastructure ownership")
    if owner == LEGACY_OWNER and labels.get("perfeng.io/purpose") != "gitops-control-plane":
        raise ValueError("Argo CD namespace has incomplete legacy ownership")


def restore_legacy_owner(infrastructure_root: Path) -> int:
    """Restore only the reviewed legacy namespace labels after provider health."""
    result = invoke_provider(
        "cluster",
        "health",
        execute=True,
        infrastructure_root=infrastructure_root,
    )
    if result:
        return result
    kube = kubectl()
    validate_namespace_owner(run_private(namespace_command(kube)), INFRASTRUCTURE_OWNER)
    manifest = (ROOT / "gitops/bootstrap/argocd/namespace.yaml").read_bytes()
    run_private(
        [*kube, "apply", "--server-side", "--force-conflicts", "-f", "-"],
        input_data=manifest,
    )
    validate_namespace_owner(run_private(namespace_command(kube)), LEGACY_OWNER)
    print("Restored the reviewed perfeng-environment Argo CD namespace owner.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=[*MODULES, "restore-legacy-owner"])
    parser.add_argument("action", nargs="?")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-delete")
    parser.add_argument("--confirm-owner")
    parser.add_argument(
        "--infrastructure-root",
        type=Path,
        default=DEFAULT_INFRASTRUCTURE_ROOT,
    )
    args = parser.parse_args()
    try:
        if args.component == "restore-legacy-owner":
            if args.action is not None or args.confirm_delete is not None:
                raise ValueError("Legacy-owner restoration does not accept an action or deletion")
            if not args.execute:
                print(
                    "Preview only. Restoration changes only the reviewed Argo CD namespace labels."
                )
                print(f"Execution requires --confirm-owner {LEGACY_OWNER}.")
                return 0
            if args.confirm_owner != LEGACY_OWNER:
                raise ValueError(f"Restoration requires --confirm-owner {LEGACY_OWNER}")
            return restore_legacy_owner(args.infrastructure_root)

        if args.action is None or args.confirm_owner is not None:
            raise ValueError("Infrastructure component execution requires one valid action")
        return invoke_provider(
            args.component,
            args.action,
            execute=args.execute,
            infrastructure_root=args.infrastructure_root,
            confirm_delete=args.confirm_delete,
        )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        tarfile.TarError,
    ) as error:
        print(f"Infrastructure runtime error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
