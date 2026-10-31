"""Own a bounded group of foreground children without adopting other host processes."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from scripts.native_gateway_tool import GatewayError


class ProcessScope:
    """Supervise direct Caddy/kubectl children and terminate the complete owned scope on exit."""

    def __init__(self) -> None:
        self.children: list[tuple[str, subprocess.Popen[bytes]]] = []

    def start(self, name: str, command: list[str], root: Path) -> None:
        """Start a direct child with no console/request output and no shell or wrapper process."""
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            raise GatewayError(
                "CHILD_START",
                f"Could not start {name}",
                "run task native:doctor; external output is withheld",
            ) from None
        self.children.append((name, process))

    def check(self) -> None:
        """Reject any exited child, including an unexpected zero exit; do not restart or adopt."""
        for name, process in self.children:
            code = process.poll()
            if code is not None:
                raise GatewayError(
                    "CHILD_EXIT",
                    f"{name} exited with status {code}",
                    "stop/review the session, inspect its fixed endpoint and rerun task native:doctor",
                )

    def close(self) -> None:
        """Stop only owned children; preserve cluster, certificates and unrelated listeners."""
        for _, process in reversed(self.children):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
