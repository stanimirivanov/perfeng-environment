"""Read a bounded Linux/X11 host snapshot; unsupported probes fail closed."""

import os
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any


def command(*arguments: str, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=10, check=False)
    if result.returncode not in allowed or len(result.stdout) > 1024 * 1024:
        raise ValueError(f"Linux host probe failed: {arguments[0]}")
    return result


def cpu_ticks() -> tuple[int, int]:
    fields = Path("/proc/stat").read_text().splitlines()[0].split()
    if fields[0] != "cpu" or len(fields) < 9:
        raise ValueError("Unsupported Linux CPU accounting")
    values = [int(value) for value in fields[1:9]]
    return sum(values), values[3] + values[4]


def display_state(output: str) -> dict[str, Any]:
    """Require one active untransformed X11 output, not a guessed Wayland display."""
    active = re.findall(r"^\S+ connected(?: primary)? (\d+)x(\d+)\+\d+\+\d+", output, re.M)
    modes = re.findall(r"^\s+v:.*?clock\s+([\d.]+)Hz", output, re.M)
    transforms = re.findall(r"Transform:\s+([^\n]+)\n\s+([^\n]+)\n\s+([^\n]+)", output)
    current = re.search(r"^\s+\S+ .*\*current.*\n[^\n]*\n\s+v:.*?clock\s+([\d.]+)Hz", output, re.M)
    if len(active) != 1 or current is None or not modes:
        raise ValueError("Qualification requires one observable active X11 display")
    if not transforms or any(
        [float(value) for line in transform for value in line.split()]
        != [1, 0, 0, 0, 1, 0, 0, 0, 1]
        for transform in transforms
    ):
        raise ValueError("Unknown or scaled X11 display transform")
    width, height = active[0]
    return {
        "width": int(width),
        "height": int(height),
        "scaleFactor": 1,
        "refreshRateHz": float(current.group(1)),
    }


def snapshot() -> dict[str, Any]:
    if platform.system() != "Linux":
        raise ValueError("Live browser-host inspection requires Linux")
    if os.environ.get("XDG_SESSION_TYPE") != "x11" or not os.environ.get("DISPLAY"):
        raise ValueError(
            "Live qualification requires an interactive X11 session; use reviewed snapshot evidence for other desktops"
        )
    architecture = {"x86_64": "x64", "aarch64": "arm64"}.get(platform.machine())
    if architecture is None:
        raise ValueError("Unsupported Linux architecture")
    cpuinfo = Path("/proc/cpuinfo").read_text()
    model = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
    topology = command("lscpu", "-p=CPU,CORE,SOCKET").stdout
    cores = {
        tuple(line.split(",")[1:])
        for line in topology.splitlines()
        if line and not line.startswith("#")
    }
    if model is None or not cores:
        raise ValueError("Linux CPU topology is unavailable")
    before = cpu_ticks()
    time.sleep(0.25)
    after = cpu_ticks()
    total, idle = after[0] - before[0], after[1] - before[1]
    if total <= 0 or not 0 <= idle <= total:
        raise ValueError("Linux CPU sample is inconsistent")
    memory = dict(
        re.findall(
            r"^(MemTotal|MemAvailable):\s+(\d+) kB$", Path("/proc/meminfo").read_text(), re.M
        )
    )
    if set(memory) != {"MemTotal", "MemAvailable"}:
        raise ValueError("Linux memory evidence is unavailable")
    graphics = command("glxinfo", "-B").stdout
    renderer = re.search(r"^OpenGL renderer string:\s*(.+)$", graphics, re.M)
    version = re.search(r"^OpenGL version string:\s*(.+)$", graphics, re.M)
    if renderer is None or version is None or not re.search(r"Accelerated:\s*(yes|no)", graphics):
        raise ValueError("GPU acceleration evidence is unavailable")
    accelerated = bool(re.search(r"Accelerated:\s*yes", graphics))
    display = display_state(command("xrandr", "--current", "--verbose").stdout)
    supplies = list(Path("/sys/class/power_supply").glob("*/online"))
    states = [path.read_text().strip() for path in supplies]
    if not states or any(value not in ("0", "1") for value in states):
        raise ValueError("Power source evidence is unavailable")
    plan = command("powerprofilesctl", "get").stdout.strip()
    if plan not in ("balanced", "performance", "power-saver"):
        raise ValueError("Power profile evidence is unavailable")
    clock = command("timedatectl", "show", "--property=NTPSynchronized", "--value").stdout.strip()
    if clock not in ("yes", "no"):
        raise ValueError("Clock synchronization evidence is unavailable")
    virtual = command("systemd-detect-virt", "--quiet", allowed=(0, 1)).returncode == 0
    return {
        "virtualization": "virtual" if virtual else "physical",
        "os": {
            "family": "linux",
            "edition": platform.freedesktop_os_release().get("PRETTY_NAME", "Linux"),
            "version": platform.release(),
            "build": int(platform.release().split(".")[0]),
            "architecture": architecture,
        },
        "cpu": {
            "model": model.group(1),
            "logicalCores": os.cpu_count(),
            "physicalCores": len(cores),
            "availableLogicalCores": len(os.sched_getaffinity(0)),
            "idleCpuPercent": 100 * (1 - idle / total),
        },
        "memory": {
            "totalBytes": int(memory["MemTotal"]) * 1024,
            "availableBytes": int(memory["MemAvailable"]) * 1024,
        },
        "gpu": {
            "model": renderer.group(1),
            "driverVersion": version.group(1),
            "hardwareAcceleration": accelerated,
            "softwareRendering": not accelerated,
        },
        "display": display,
        "power": {
            "source": "ac" if "1" in states else "battery",
            "plan": plan,
            "batterySaver": plan == "power-saver",
        },
        "clockSynchronized": clock == "yes",
    }
