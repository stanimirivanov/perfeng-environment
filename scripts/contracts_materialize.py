"""Activate an independently selected contract projection in a dedicated local store."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scripts.contracts_distribution_tools import local_bytes
from scripts.contracts_inspect import METADATA_LIMIT, VerifiedArchive, inspect_verified, json_data

DIGEST = re.compile(r"[a-f0-9]{64}")
DESTINATION = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*")
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def projection(archive: VerifiedArchive, selection: Any) -> dict[str, bytes]:
    """Apply only reviewed source identities, digests and destination mappings."""
    if not isinstance(selection, dict) or selection.keys() != {"sourceRevision", "members"}:
        raise ValueError("Invalid independent consumer selection")
    if selection["sourceRevision"] != archive.source_revision:
        raise ValueError("Consumer source revision mismatch")
    mappings = selection["members"]
    if not isinstance(mappings, dict) or mappings.keys() != archive.members.keys():
        raise ValueError("Consumer member inventory mismatch")

    result: dict[str, bytes] = {}
    folded: set[str] = set()
    for source, content in archive.members.items():
        mapping = mappings[source]
        if not isinstance(mapping, dict) or mapping.keys() != {"destination", "sha256"}:
            raise ValueError("Invalid consumer member mapping")
        if hashlib.sha256(content).hexdigest() != mapping["sha256"]:
            raise ValueError("Consumer member digest mismatch")
        destination = mapping["destination"]
        if (
            not isinstance(destination, str)
            or len(destination) > 512
            or not DESTINATION.fullmatch(destination)
            or any(len(part) > 255 for part in destination.split("/"))
            or destination.lower() in folded
        ):
            raise ValueError("Invalid or conflicting consumer destination")
        folded.add(destination.lower())
        result[destination] = content
    if any(str(parent) in folded for name in folded for parent in Path(name).parents):
        raise ValueError("Conflicting consumer destination hierarchy")
    return result


@contextmanager
def owned_store(target: Path) -> Iterator[Path]:
    """Anchor all changes at a locked, non-symlinked directory owned by this user."""
    target = target.absolute()
    if target == Path("/") or ".." in target.parts:
        raise ValueError("Expected a dedicated projection store path")
    fd = os.open("/", DIRECTORY_FLAGS)
    try:
        for part in target.parts[1:]:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("Projection store must be owned and writable only by the current user")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Projection store is busy") from exc
        yield Path(f"/proc/self/fd/{fd}")
    finally:
        os.close(fd)


def validate_store(root: Path) -> None:
    for entry in root.iterdir():
        info = entry.lstat()
        if entry.name == "current" and stat.S_ISREG(info.st_mode):
            pointer = local_bytes(entry, limit=65).decode("ascii")
            if len(pointer) != 65 or pointer[-1] != "\n" or not DIGEST.fullmatch(pointer[:-1]):
                raise ValueError("Invalid current projection pointer")
            previous = root / pointer[:-1]
            if not previous.is_dir() or previous.is_symlink():
                raise ValueError("Current projection is unavailable")
        elif (
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.geteuid()
            and not info.st_mode & 0o022
            and (DIGEST.fullmatch(entry.name) or re.fullmatch(r"\.stage-[a-z0-9_]{8}", entry.name))
        ):
            continue
        else:
            raise ValueError("Projection store contains unrelated or unsafe content")


def verify_generation(root: Path, expected: dict[str, bytes]) -> None:
    pending = [root]
    seen: set[str] = set()
    directories = {parent for name in expected for parent in Path(name).parents}
    while pending:
        for entry in pending.pop().iterdir():
            relative = entry.relative_to(root)
            name = relative.as_posix()
            info = entry.lstat()
            if stat.S_ISDIR(info.st_mode) and relative in directories:
                pending.append(entry)
            elif stat.S_ISREG(info.st_mode) and name in expected:
                with entry.open("rb") as stream:
                    if stream.read(len(expected[name]) + 1) != expected[name]:
                        raise ValueError("Existing projection member differs from verified bytes")
                seen.add(name)
            else:
                raise ValueError("Existing projection contains unrelated or unsafe content")
    if seen != expected.keys():
        raise ValueError("Existing projection is missing expected members")


def materialize(target: Path, archive: VerifiedArchive, selection: Any) -> Path:
    """Stage one complete projection and atomically replace the regular current pointer."""
    files = projection(archive, selection)
    identity = {
        "sourceRevision": archive.source_revision,
        "members": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    generation = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    with owned_store(target) as root:
        validate_store(root)
        final = root / generation
        with tempfile.TemporaryDirectory(dir=root, prefix=".stage-") as temporary:
            stage = Path(temporary)
            if final.exists():
                verify_generation(final, files)
            else:
                tree = stage / "projection"
                tree.mkdir(mode=0o700)
                for name, content in sorted(files.items()):
                    member = tree / name
                    member.parent.mkdir(parents=True, exist_ok=True)
                    member.write_bytes(content)
                tree.rename(final)
            pointer = stage / "current"
            pointer.write_text(generation + "\n", encoding="ascii")
            os.replace(pointer, root / "current")
    return target.absolute() / generation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "archive", "bundle", "policy", "verifier", "selection", "target"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args()
    try:
        selection = json_data(local_bytes(args.selection, limit=METADATA_LIMIT))
        archive = inspect_verified(
            args.manifest, args.archive, args.bundle, args.policy, args.verifier
        )
        target = materialize(args.target, archive, selection)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"contracts-materialize: {exc}", file=sys.stderr)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
