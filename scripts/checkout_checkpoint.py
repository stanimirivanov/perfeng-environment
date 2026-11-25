"""Single-writer private checkout evidence and crash-safe checkpoint updates."""

import fcntl
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from scripts.kubernetes_benchmark_contract import document


def private_bytes(path: Path, maximum: int) -> bytes:
    if path.absolute() != path.resolve():
        raise ValueError("Private input must use its original path without symlinks")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > maximum
        ):
            raise ValueError("Expected an owner-only regular file of bounded size")
        data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Private input exceeded its size bound")
        return data


class Checkpoint:
    def __init__(self, directory: Path, *, create: bool):
        self.directory = directory.absolute()
        self.create = create
        self.fd = -1

    def __enter__(self):
        path = self.directory
        if path != path.resolve() or any(
            (parent / ".git").is_file() or (parent / ".git/HEAD").is_file()
            for parent in (path, *path.parents)
        ):
            raise ValueError("Checkpoint must be outside Git and use its original path")
        if self.create:
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                raise ValueError(
                    "Refusing existing checkpoint directory; resume the original"
                ) from None
            parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        self.fd = os.open(path, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(self.fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("Checkpoint directory must be private to its owner")
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Checkpoint already has another writer") from None
        except BaseException:
            os.close(self.fd)
            raise
        return self

    def __exit__(self, *exc):
        os.close(self.fd)

    def load(self) -> dict[str, Any]:
        state = document(private_bytes(self.directory / "checkpoint.json", 2 << 20))
        if (
            not isinstance(state, dict)
            or type(state.get("schemaVersion")) is not int
            or state["schemaVersion"] != 1
        ):
            raise ValueError("Unknown checkout checkpoint version")
        return state

    def save(self, state: dict[str, Any]) -> None:
        body = json.dumps(state, allow_nan=False, sort_keys=True, indent=2).encode() + b"\n"
        self._publish(self.directory / "checkpoint.json", body)

    def _publish(self, path: Path, body: bytes) -> None:
        with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stream.name, path)
        os.fsync(self.fd)

    def retain(self, name: str, content: bytes) -> None:
        """Publish whole immutable evidence bytes, including after a prior interruption."""
        path = self.directory / name
        if path.exists() or path.is_symlink():
            if private_bytes(path, len(content)) != content:
                raise ValueError(f"Retained {name} differs; refusing overwritten evidence")
            return
        self._publish(path, content)
