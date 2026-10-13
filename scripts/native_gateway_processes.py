"""Own a bounded group of foreground children without adopting other host processes."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Protocol, cast

from scripts.native_gateway_tool import GatewayError


class _WindowsChild(Protocol):
    """CPython's Windows process handle, omitted from the cross-platform Popen type stub."""

    _handle: int


class WindowsJob:
    """Kill only assigned children on handle close, including supervisor termination on Windows."""

    kernel: ctypes.CDLL

    def __init__(self) -> None:
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD),
                ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t),
                ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class Counters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "read_operations",
                    "write_operations",
                    "other_operations",
                    "read_bytes",
                    "write_bytes",
                    "other_bytes",
                )
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io", Counters),
                ("process_memory_limit", ctypes.c_size_t),
                ("job_memory_limit", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        if sys.platform == "win32":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        else:
            raise GatewayError(
                "PROCESS_PLATFORM",
                "Windows Job ownership is unavailable on this platform",
                "use the supported Windows host",
            )
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise GatewayError(
                "PROCESS_OWNERSHIP",
                "Cannot create an isolated process job",
                "inspect Windows process restrictions; no session started",
            )
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise GatewayError(
                "PROCESS_OWNERSHIP",
                "Cannot configure child cleanup",
                "inspect Windows job restrictions",
            )

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        """Assign a newly created direct child, never a PID found on the host."""
        if not self.kernel.AssignProcessToJobObject(
            self.handle, int(cast(_WindowsChild, process)._handle)
        ):
            process.kill()
            process.wait(timeout=5)
            raise GatewayError(
                "PROCESS_OWNERSHIP",
                "Cannot own the new child process",
                "inspect Windows job restrictions; this child was stopped",
            )

    def close(self) -> None:
        """Close the non-inheritable job handle; the operating system terminates assigned descendants."""
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class ProcessScope:
    """Supervise direct Caddy/kubectl children and terminate the complete owned scope on exit."""

    def __init__(self) -> None:
        self.children: list[tuple[str, subprocess.Popen[bytes]]] = []
        self.job = WindowsJob() if os.name == "nt" else None

    def start(self, name: str, command: list[str], root: Path) -> None:
        """Start a direct child with no console/request output and no shell or wrapper process."""
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        except OSError:
            raise GatewayError(
                "CHILD_START",
                f"Could not start {name}",
                "run task native:doctor; external output is withheld",
            ) from None
        self.children.append((name, process))
        if self.job:
            self.job.assign(process)

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
        if self.job:
            self.job.close()
        for _, process in reversed(self.children):
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
