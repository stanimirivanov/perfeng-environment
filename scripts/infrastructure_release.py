"""Validate the immutable local Kubernetes infrastructure selection."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import yaml

if __package__:
    from .infrastructure_lock import (
        DEFAULT_LOCK,
        INFRASTRUCTURE_REPOSITORY,
        InfrastructureLock,
        load_lock,
        portable_path,
        validate_source,
    )
    from .workspace import canonical_origin
else:
    from infrastructure_lock import (
        DEFAULT_LOCK,
        INFRASTRUCTURE_REPOSITORY,
        InfrastructureLock,
        load_lock,
        portable_path,
        validate_source,
    )
    from workspace import canonical_origin

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INFRASTRUCTURE_ROOT = ROOT.parent / "k8s-infrastructure"


def git(root: Path, *arguments: str) -> bytes:
    """Run a bounded, read-only Git query with private failure output."""
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *arguments],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Git could not inspect the infrastructure source") from error
    if completed.returncode:
        raise ValueError("Git could not inspect the pinned infrastructure revision")
    return completed.stdout


def revision_reader(root: Path, revision: str) -> Callable[[str], bytes]:
    """Return a reader for files at one verified infrastructure revision."""
    if root.is_symlink() or root.is_junction():
        raise ValueError("Infrastructure source must be an ordinary repository directory")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Infrastructure source must be an ordinary repository directory")
    origin = git(root, "remote", "get-url", "origin").decode("utf-8").strip()
    if canonical_origin(origin) != canonical_origin(INFRASTRUCTURE_REPOSITORY):
        raise ValueError("Infrastructure source has the wrong Git origin")
    if git(root, "cat-file", "-t", revision).decode("ascii").strip() != "commit":
        raise ValueError("Infrastructure revision is not a commit")

    def read(path: str) -> bytes:
        portable_path(path, "infrastructure source path")
        return git(root, "show", f"{revision}:{path}")

    return read


def validate(
    lock_path: Path = DEFAULT_LOCK,
    infrastructure_root: Path = DEFAULT_INFRASTRUCTURE_ROOT,
) -> InfrastructureLock:
    """Validate the lock and exact infrastructure-source commit offline."""
    lock = load_lock(lock_path)
    read = revision_reader(infrastructure_root, lock.revision)
    validate_source(lock, read)
    return lock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--infrastructure-root", type=Path, default=DEFAULT_INFRASTRUCTURE_ROOT)
    args = parser.parse_args()
    try:
        lock = validate(args.lock, args.infrastructure_root)
        print(
            f"Validated immutable infrastructure revision {lock.revision[:12]} for {lock.profile}."
        )
    except (
        UnicodeError,
        ValueError,
        OSError,
        TypeError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as error:
        print(f"Infrastructure release error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
