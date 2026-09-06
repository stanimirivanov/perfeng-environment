"""Validate and evaluate the native Windows browser execution profile."""

import argparse
import hashlib
import json
import platform
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles/browser/windows-mainstream.yaml"
ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")

POWERSHELL_SNAPSHOT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$os = Get-CimInstance Win32_OperatingSystem
$processors = @(Get-CimInstance Win32_Processor)
$video = @(Get-CimInstance Win32_VideoController |
  Where-Object { $_.CurrentHorizontalResolution -and $_.CurrentVerticalResolution } |
  Select-Object -First 1)[0]
if (-not $video) { $video = @(Get-CimInstance Win32_VideoController)[0] }
$plan = Get-CimInstance -Namespace root/cimv2/power -ClassName Win32_PowerPlan |
  Where-Object IsActive | Select-Object -First 1
$powerPlanId = ([string]$plan.InstanceID).Split('\')[-1]
$cpu = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
& w32tm /query /status 2>$null | Out-Null
$clockSynchronized = $LASTEXITCODE -eq 0
$gpuName = [string]$video.Name
$softwareRendering = $gpuName -match 'Microsoft Basic (Display|Render)'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class PerfengNative {
  [StructLayout(LayoutKind.Sequential)]
  public struct PowerStatus {
    public byte ACLineStatus, BatteryFlag, BatteryLifePercent, SystemStatusFlag;
    public uint BatteryLifeTime, BatteryFullLifeTime;
  }
  [DllImport("kernel32.dll")] public static extern bool GetSystemPowerStatus(out PowerStatus status);
  [DllImport("user32.dll")] public static extern uint GetDpiForSystem();
}
'@
$powerStatus = [PerfengNative+PowerStatus]::new()
if (-not [PerfengNative]::GetSystemPowerStatus([ref]$powerStatus)) {
  throw 'GetSystemPowerStatus failed'
}
$powerSource = switch ($powerStatus.ACLineStatus) { 1 { 'ac' } 0 { 'battery' } default { 'unknown' } }
$scaleFactor = [Math]::Round([PerfengNative]::GetDpiForSystem() / 96.0, 2)
[ordered]@{
  virtualization = if ((Get-CimInstance Win32_ComputerSystem).Model -match 'Virtual|VMware|KVM') { 'virtual' } else { 'physical' }
  os = [ordered]@{
    family = 'windows'; edition = [string]$os.Caption; version = [string]$os.Version
    build = [int]$os.BuildNumber; architecture = if ([Environment]::Is64BitOperatingSystem) { 'x64' } else { 'x86' }
  }
  cpu = [ordered]@{
    model = [string]$processors[0].Name
    logicalCores = [int](($processors | Measure-Object NumberOfLogicalProcessors -Sum).Sum)
    physicalCores = [int](($processors | Measure-Object NumberOfCores -Sum).Sum)
    availableLogicalCores = [int][Environment]::ProcessorCount
    idleCpuPercent = [double]$cpu.PercentProcessorTime
  }
  memory = [ordered]@{
    totalBytes = [int64]$os.TotalVisibleMemorySize * 1024
    availableBytes = [int64]$os.FreePhysicalMemory * 1024
  }
  gpu = [ordered]@{
    model = $gpuName; driverVersion = [string]$video.DriverVersion
    hardwareAcceleration = -not $softwareRendering; softwareRendering = $softwareRendering
  }
  display = [ordered]@{
    width = [int]$video.CurrentHorizontalResolution; height = [int]$video.CurrentVerticalResolution
    scaleFactor = $scaleFactor; refreshRateHz = [double]$video.CurrentRefreshRate
  }
  power = [ordered]@{
    source = $powerSource; plan = $powerPlanId
    batterySaver = $powerStatus.SystemStatusFlag -eq 1
  }
  clockSynchronized = $clockSynchronized
} | ConvertTo-Json -Depth 5 -Compress
"""


def mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return value


def exact_keys(value: dict[str, Any], name: str, expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} has missing or unknown fields")


def positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return float(value)


def nonnegative_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{name} must be zero or greater")
    return float(value)


def integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def load_profile(path: Path = PROFILE) -> dict[str, Any]:
    document = mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "profile")
    exact_keys(document, "profile", {"apiVersion", "kind", "metadata", "spec"})
    if document["apiVersion"] != "perfeng.dev/v1" or document["kind"] != "BrowserExecutionProfile":
        raise ValueError("Unsupported browser execution profile kind")
    metadata = mapping(document["metadata"], "metadata")
    exact_keys(metadata, "metadata", {"id", "version"})
    if not isinstance(metadata["id"], str) or not ID_PATTERN.fullmatch(metadata["id"]):
        raise ValueError("Invalid profile ID")
    if not isinstance(metadata["version"], str) or not VERSION_PATTERN.fullmatch(
        metadata["version"]
    ):
        raise ValueError("Invalid profile version")
    spec = mapping(document["spec"], "spec")
    exact_keys(
        spec,
        "spec",
        {
            "virtualization",
            "os",
            "cpu",
            "memory",
            "gpu",
            "display",
            "power",
            "browser",
            "calibration",
        },
    )
    expected = {
        "os": {"family", "architecture", "minimumBuild"},
        "cpu": {"minimumLogicalCores"},
        "memory": {"minimumTotalBytes", "minimumAvailableBytes"},
        "gpu": {"hardwareAcceleration"},
        "display": {"width", "height", "scaleFactor", "refreshRateHz"},
        "power": {"source", "plan", "batterySaver"},
        "browser": {"name", "channel", "headless"},
        "calibration": {"maximumIdleCpuPercent", "clockSynchronized"},
    }
    for section, keys in expected.items():
        exact_keys(mapping(spec[section], f"spec.{section}"), f"spec.{section}", keys)
    for path_name, value in (
        ("minimumBuild", spec["os"]["minimumBuild"]),
        ("minimumLogicalCores", spec["cpu"]["minimumLogicalCores"]),
        ("minimumTotalBytes", spec["memory"]["minimumTotalBytes"]),
        ("minimumAvailableBytes", spec["memory"]["minimumAvailableBytes"]),
        ("display.width", spec["display"]["width"]),
        ("display.height", spec["display"]["height"]),
        ("display.scaleFactor", spec["display"]["scaleFactor"]),
        ("display.refreshRateHz", spec["display"]["refreshRateHz"]),
    ):
        positive_number(value, path_name)
    maximum_cpu = positive_number(
        spec["calibration"]["maximumIdleCpuPercent"], "maximumIdleCpuPercent"
    )
    if maximum_cpu > 100:
        raise ValueError("maximumIdleCpuPercent must not exceed 100")
    text(spec["browser"]["name"], "spec.browser.name")
    text(spec["browser"]["channel"], "spec.browser.channel")
    boolean(spec["browser"]["headless"], "spec.browser.headless")
    return document


def fingerprint(profile: dict[str, Any]) -> str:
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def live_snapshot() -> dict[str, Any]:
    if platform.system() != "Windows":
        raise ValueError("Live browser-host inspection requires Windows")
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_SNAPSHOT],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        suffix = f": {detail[0]}" if detail else ""
        raise ValueError(f"Windows host inspection failed (exit {completed.returncode}){suffix}")
    try:
        return mapping(json.loads(completed.stdout.decode("utf-8-sig")), "snapshot")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Windows host inspection returned invalid UTF-8 JSON") from error


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    exact_keys(
        snapshot,
        "snapshot",
        {
            "virtualization",
            "os",
            "cpu",
            "memory",
            "gpu",
            "display",
            "power",
            "clockSynchronized",
        },
    )
    sections = {
        "os": {"family", "edition", "version", "build", "architecture"},
        "cpu": {
            "model",
            "logicalCores",
            "physicalCores",
            "availableLogicalCores",
            "idleCpuPercent",
        },
        "memory": {"totalBytes", "availableBytes"},
        "gpu": {
            "model",
            "driverVersion",
            "hardwareAcceleration",
            "softwareRendering",
        },
        "display": {"width", "height", "scaleFactor", "refreshRateHz"},
        "power": {"source", "plan", "batterySaver"},
    }
    values = {name: mapping(snapshot[name], f"snapshot.{name}") for name in sections}
    for name, keys in sections.items():
        exact_keys(values[name], f"snapshot.{name}", keys)
    text(snapshot["virtualization"], "snapshot.virtualization")
    for name in ("family", "edition", "version", "architecture"):
        text(values["os"][name], f"snapshot.os.{name}")
    integer(values["os"]["build"], "snapshot.os.build")
    text(values["cpu"]["model"], "snapshot.cpu.model")
    logical_cores = integer(values["cpu"]["logicalCores"], "snapshot.cpu.logicalCores")
    physical_cores = integer(values["cpu"]["physicalCores"], "snapshot.cpu.physicalCores")
    available_cores = integer(
        values["cpu"]["availableLogicalCores"], "snapshot.cpu.availableLogicalCores"
    )
    if (
        logical_cores < 1
        or physical_cores < 1
        or physical_cores > logical_cores
        or available_cores < 1
        or available_cores > logical_cores
    ):
        raise ValueError("snapshot CPU core counts are inconsistent")
    idle_cpu = nonnegative_number(values["cpu"]["idleCpuPercent"], "snapshot.cpu.idleCpuPercent")
    if idle_cpu > 100:
        raise ValueError("snapshot.cpu.idleCpuPercent must not exceed 100")
    for name in ("totalBytes", "availableBytes"):
        integer(values["memory"][name], f"snapshot.memory.{name}")
    for name in ("model", "driverVersion"):
        text(values["gpu"][name], f"snapshot.gpu.{name}")
    for name in ("hardwareAcceleration", "softwareRendering"):
        boolean(values["gpu"][name], f"snapshot.gpu.{name}")
    for name in ("width", "height", "scaleFactor", "refreshRateHz"):
        positive_number(values["display"][name], f"snapshot.display.{name}")
    for name in ("source", "plan"):
        text(values["power"][name], f"snapshot.power.{name}")
    boolean(values["power"]["batterySaver"], "snapshot.power.batterySaver")
    boolean(snapshot["clockSynchronized"], "snapshot.clockSynchronized")


def evaluate(profile: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    spec = profile["spec"]
    observed = mapping(snapshot, "snapshot")
    validate_snapshot(observed)
    reasons: list[str] = []

    def require(condition: bool, reason: str) -> None:
        if not condition:
            reasons.append(reason)

    os_data = mapping(observed["os"], "snapshot.os")
    cpu = mapping(observed["cpu"], "snapshot.cpu")
    memory = mapping(observed["memory"], "snapshot.memory")
    gpu = mapping(observed["gpu"], "snapshot.gpu")
    display = mapping(observed["display"], "snapshot.display")
    power = mapping(observed["power"], "snapshot.power")
    require(observed["virtualization"] == spec["virtualization"], "virtualization differs")
    require(os_data.get("family") == spec["os"]["family"], "operating system differs")
    require(os_data.get("architecture") == spec["os"]["architecture"], "architecture differs")
    require(os_data["build"] >= spec["os"]["minimumBuild"], "OS build is too old")
    require(
        cpu["availableLogicalCores"] >= spec["cpu"]["minimumLogicalCores"],
        "available CPU capacity is too low",
    )
    require(
        memory["totalBytes"] >= spec["memory"]["minimumTotalBytes"],
        "installed memory is too low",
    )
    require(
        memory["availableBytes"] >= spec["memory"]["minimumAvailableBytes"],
        "available memory is too low",
    )
    require(
        gpu["hardwareAcceleration"] is spec["gpu"]["hardwareAcceleration"],
        "GPU acceleration differs",
    )
    for name in ("width", "height", "scaleFactor", "refreshRateHz"):
        require(display[name] == spec["display"][name], f"display {name} differs")
    for name in ("source", "plan", "batterySaver"):
        require(power[name] == spec["power"][name], f"power {name} differs")
    require(
        cpu["idleCpuPercent"] <= spec["calibration"]["maximumIdleCpuPercent"],
        "background CPU is too high",
    )
    require(
        observed["clockSynchronized"] is spec["calibration"]["clockSynchronized"],
        "clock synchronization differs",
    )
    return {
        "schema": "browser-host-qualification/v1",
        "profile": dict(profile["metadata"]),
        "fingerprint": fingerprint(profile),
        "status": "ACCEPTED" if not reasons else "REJECTED",
        "reasons": reasons,
        "observed": observed,
        "capturedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate", "check"])
    parser.add_argument("--profile", type=Path, default=PROFILE)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        profile = load_profile(args.profile)
        if args.action == "validate":
            if args.output is not None:
                raise ValueError("--output is only supported for host qualification")
            print(json.dumps({"profile": profile["metadata"], "fingerprint": fingerprint(profile)}))
            return 0
        snapshot = (
            mapping(json.loads(args.snapshot.read_text(encoding="utf-8")), "snapshot")
            if args.snapshot
            else live_snapshot()
        )
        result = evaluate(profile, snapshot)
        if args.output is None:
            print(json.dumps(result, indent=2))
        else:
            content = (json.dumps(result, indent=2) + "\n").encode("utf-8")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("xb") as output:
                output.write(content)
            print(
                json.dumps(
                    {
                        "path": str(args.output),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "sizeBytes": len(content),
                        "status": result["status"],
                    }
                )
            )
        return 0 if result["status"] == "ACCEPTED" else 1
    except (
        OSError,
        ValueError,
        yaml.YAMLError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"Browser host error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
