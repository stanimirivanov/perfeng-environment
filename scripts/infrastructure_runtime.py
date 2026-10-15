"""Run the immutable Kubernetes infrastructure selection."""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

if __package__:
    from .infrastructure_lock import portable_path
    from .infrastructure_release import (
        DEFAULT_INFRASTRUCTURE_ROOT,
        git,
        validate,
    )
else:
    from infrastructure_lock import portable_path
    from infrastructure_release import DEFAULT_INFRASTRUCTURE_ROOT, git, validate

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_LIMIT = 16 * 1024 * 1024
PROVIDER_TIMEOUT = 10_800
MODULES = {
    "cluster": "scripts.local_kind",
    "argocd": "scripts.argocd",
    "metrics": "scripts.resource_metrics",
}
ACTIONS = {
    "cluster": {"up", "ensure", "health", "down"},
    "argocd": {"validate", "bootstrap", "health"},
    "metrics": {"health"},
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=MODULES)
    parser.add_argument("action")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-delete")
    parser.add_argument(
        "--infrastructure-root",
        type=Path,
        default=DEFAULT_INFRASTRUCTURE_ROOT,
    )
    args = parser.parse_args()
    try:
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
        tarfile.TarError,
    ) as error:
        print(f"Infrastructure runtime error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
