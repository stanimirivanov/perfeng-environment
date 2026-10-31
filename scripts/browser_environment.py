"""Assemble a qualified browser-environment/v1 artifact."""

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

try:
    from . import browser_host
except ImportError:
    import browser_host

RUN_ID_PATTERN = re.compile(r"perf-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}")
TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)


def mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return value


def exact_keys(value: dict[str, Any], name: str, expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} has missing or unknown fields")


def timestamp(value: object, name: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a time-zone offset")
    return value, parsed


def qualification_data(value: object) -> dict[str, Any]:
    qualification = mapping(value, "qualification")
    exact_keys(
        qualification,
        "qualification",
        {
            "schema",
            "profile",
            "fingerprint",
            "status",
            "reasons",
            "observed",
            "capturedAt",
        },
    )
    if qualification["schema"] != "browser-host-qualification/v1":
        raise ValueError("Unsupported browser host qualification schema")
    profile = mapping(qualification["profile"], "qualification.profile")
    exact_keys(profile, "qualification.profile", {"id", "version"})
    fingerprint = qualification["fingerprint"]
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise ValueError("Invalid qualification fingerprint")
    reasons = qualification["reasons"]
    if not isinstance(reasons, list) or not all(
        isinstance(reason, str) and reason for reason in reasons
    ):
        raise ValueError("qualification.reasons must contain non-empty strings")
    if qualification["status"] != "ACCEPTED" or reasons:
        raise ValueError("Browser host qualification is not accepted")
    observed = mapping(qualification["observed"], "qualification.observed")
    browser_host.validate_snapshot(observed)
    timestamp(qualification["capturedAt"], "qualification.capturedAt")
    return qualification


def measurement_data(value: object) -> dict[str, Any]:
    measurements = mapping(value, "measurements")
    if (
        measurements.get("schemaVersion") != 2
        or measurements.get("kind") != "PlaywrightMeasurements"
    ):
        raise ValueError("Expected a PlaywrightMeasurements v2 artifact")
    run_id = measurements.get("runId")
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("Invalid measurement run ID")
    environment = mapping(measurements.get("environment"), "measurements.environment")
    exact_keys(environment, "measurements.environment", {"profile", "fingerprint"})
    profile = mapping(environment["profile"], "measurements.environment.profile")
    exact_keys(profile, "measurements.environment.profile", {"id", "version"})
    runtime = mapping(measurements.get("runtime"), "measurements.runtime")
    exact_keys(
        runtime,
        "measurements.runtime",
        {"playwrightVersion", "nodeVersion", "platform", "architecture"},
    )
    browser = mapping(measurements.get("browser"), "measurements.browser")
    exact_keys(
        browser,
        "measurements.browser",
        {"name", "version", "headless", "viewport", "deviceScaleFactor"},
    )
    if not isinstance(browser["version"], str) or not browser["version"]:
        raise ValueError("measurements.browser.version must be a non-empty string")
    if not isinstance(browser["headless"], bool):
        raise ValueError("measurements.browser.headless must be a boolean")
    window = mapping(measurements.get("measurementWindow"), "measurements.measurementWindow")
    exact_keys(window, "measurements.measurementWindow", {"start", "end"})
    _, start = timestamp(window["start"], "measurements.measurementWindow.start")
    _, end = timestamp(window["end"], "measurements.measurementWindow.end")
    if start >= end:
        raise ValueError("Measurement window start must precede end")
    return measurements


def assemble(
    profile: dict[str, Any],
    qualification_value: object,
    measurement_value: object,
) -> dict[str, Any]:
    qualification = qualification_data(qualification_value)
    measurements = measurement_data(measurement_value)
    metadata = mapping(profile["metadata"], "profile.metadata")
    spec = mapping(profile["spec"], "profile.spec")
    browser_spec = mapping(spec["browser"], "profile.spec.browser")
    qualification_profile = mapping(qualification["profile"], "qualification.profile")
    environment = mapping(measurements["environment"], "measurements.environment")
    measurement_profile = mapping(environment["profile"], "measurements.environment.profile")
    expected_fingerprint = browser_host.fingerprint(profile)
    if qualification_profile != metadata or measurement_profile != metadata:
        raise ValueError("Browser profile identity does not match across inputs")
    if (
        qualification["fingerprint"] != expected_fingerprint
        or environment["fingerprint"] != expected_fingerprint
    ):
        raise ValueError("Browser profile fingerprint does not match across inputs")
    observed = mapping(qualification["observed"], "qualification.observed")
    os_data = mapping(observed["os"], "qualification.observed.os")
    runtime = mapping(measurements["runtime"], "measurements.runtime")
    browser = mapping(measurements["browser"], "measurements.browser")
    if runtime["platform"] != "linux" or os_data["family"] != "linux":
        raise ValueError("Browser environment artifact requires native Linux execution")
    if runtime["architecture"] != os_data["architecture"]:
        raise ValueError("Browser runtime architecture does not match the qualified host")
    if (
        browser["name"] != browser_spec["name"]
        or browser["headless"] is not browser_spec["headless"]
    ):
        raise ValueError("Actual browser execution does not match the browser profile")
    captured_at, qualification_time = timestamp(
        qualification["capturedAt"], "qualification.capturedAt"
    )
    window = mapping(measurements["measurementWindow"], "measurements.measurementWindow")
    _, measurement_start = timestamp(window["start"], "measurements.measurementWindow.start")
    if qualification_time > measurement_start:
        raise ValueError("Browser host qualification must precede measurement execution")
    cpu = mapping(observed["cpu"], "qualification.observed.cpu")
    memory = mapping(observed["memory"], "qualification.observed.memory")
    gpu = mapping(observed["gpu"], "qualification.observed.gpu")
    display = mapping(observed["display"], "qualification.observed.display")
    power = mapping(observed["power"], "qualification.observed.power")
    return {
        "schemaVersion": 1,
        "kind": "BrowserEnvironment",
        "runId": measurements["runId"],
        "profile": dict(metadata),
        "fingerprint": expected_fingerprint,
        "host": {
            "virtualization": observed["virtualization"],
            "os": {**os_data, "build": str(os_data["build"])},
            "cpu": {
                "model": cpu["model"],
                "logicalCores": cpu["logicalCores"],
                "physicalCores": cpu["physicalCores"],
                "availableLogicalCores": cpu["availableLogicalCores"],
            },
            "memory": {
                "totalBytes": memory["totalBytes"],
                "availableBytes": memory["availableBytes"],
            },
            "gpu": {
                "model": gpu["model"],
                "driverVersion": gpu["driverVersion"],
                "hardwareAcceleration": gpu["hardwareAcceleration"],
            },
            "display": dict(display),
            "power": dict(power),
        },
        "browser": {
            "name": browser["name"],
            "channel": browser_spec["channel"],
            "version": browser["version"],
            "headless": browser["headless"],
        },
        "calibration": {
            "status": qualification["status"],
            "reasons": list(qualification["reasons"]),
            "idleCpuPercent": cpu["idleCpuPercent"],
            "availableMemoryBytes": memory["availableBytes"],
            "clockSynchronized": observed["clockSynchronized"],
            "softwareRendering": gpu["softwareRendering"],
        },
        "capturedAt": captured_at,
    }


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["assemble"])
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=browser_host.PROFILE)
    args = parser.parse_args()
    try:
        artifact = assemble(
            browser_host.load_profile(args.profile),
            read_json(args.qualification),
            read_json(args.measurements),
        )
        content = (json.dumps(artifact, indent=2) + "\n").encode("utf-8")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as output:
            output.write(content)
        print(
            json.dumps(
                {
                    "path": str(args.output),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "sizeBytes": len(content),
                }
            )
        )
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        yaml.YAMLError,
        json.JSONDecodeError,
    ) as error:
        print(f"Browser environment error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
