"""Re-run the seven reviewed cutover gates with primary-service networking unavailable."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from scripts.contracts_inspect import ROOT, json_data

CONSUMERS = ("environment", "analysis", "k6", "k8s", "playwright", "control-plane", "console")
SCHEMA = ROOT / "contracts/distribution-campaign.schema.json"
COMMON = {"CONTRACTS_SHADOW_ASSETS", "CONTRACTS_BOOTSTRAP", "CONTRACTS_VERIFIER"}
GATES = {
    "environment": (
        ("contracts-update-check", {"CONTRACTS_UPDATE_ASSETS", "CONTRACTS_VERIFIER"}),
        ("contracts-restore-check", {"CONTRACTS_RETAINED_ASSETS", "CONTRACTS_VERIFIER"}),
    ),
    "analysis": (
        ("contracts-update-check", COMMON),
        (
            "contracts-package-check",
            {"CONTRACTS_CURRENT_WHEEL", "CONTRACTS_ROLLBACK_WHEEL", "CONTRACTS_ROLLBACK_SHA256"},
        ),
    ),
    "k6": (
        ("prepare", COMMON | {"CONTRACTS_STAGING_TARGET"}),
        ("contracts-package-check", {"CONTRACTS_ROLLBACK_CHECKOUT", "IMAGE"}),
    ),
    "k8s": (
        ("contracts-update-check", COMMON | {"CONTRACTS_STAGING_TARGET"}),
        (
            "contracts-restore-check",
            COMMON | {"CONTRACTS_STAGING_TARGET", "CONTRACTS_ROLLBACK_CHECKOUT"},
        ),
    ),
    "playwright": (
        (
            "contracts-update-check",
            {
                "CONTRACTS_SHADOW_ASSETS",
                "CONTRACTS_BOOTSTRAP_ROOT",
                "CONTRACTS_VERIFIER",
                "CONTRACTS_STAGING_TARGET",
            },
        ),
        (
            "contracts-package-check",
            {
                "CONTRACTS_CURRENT_PACKAGE",
                "CONTRACTS_ROLLBACK_PACKAGE",
                "CONTRACTS_ROLLBACK_SHA256",
            },
        ),
    ),
    "control-plane": (
        ("contracts-update-check", COMMON | {"CONTRACTS_STAGING_TARGET"}),
        (
            "contracts-restore-check",
            COMMON | {"CONTRACTS_STAGING_TARGET", "CONTRACTS_ROLLBACK_SNAPSHOT"},
        ),
    ),
    "console": (
        (
            "contracts-update-check",
            COMMON | {"CONTRACTS_CONTROL_PLANE_RECEIPT", "CONTRACTS_STAGING_TARGET"},
        ),
        (
            "contracts-restore-check",
            COMMON
            | {
                "CONTRACTS_CONTROL_PLANE_RECEIPT",
                "CONTRACTS_STAGING_TARGET",
                "CONTRACTS_ROLLBACK_SNAPSHOT",
            },
        ),
    ),
}
# Analysis preview already materializes and validates a private signed projection.
EXPECTED_STATUS = {
    "environment": ("unchanged", "passed"),
    "analysis": ("preview", "passed"),
    "k6": ("prepared", "passed"),
    "k8s": ("passed", "passed"),
    "playwright": ("prepared", "passed"),
    "control-plane": ("prepared", "prepared"),
    "console": ("prepared", "prepared"),
}
OFFLINE = """
import hashlib, json, os, socket, subprocess, sys
from pathlib import Path
namespace = os.readlink("/proc/self/ns/net")
if namespace == sys.argv[1] or socket.if_nameindex() != [(1, "lo")]:
    raise SystemExit("A fresh loopback-only network namespace is required")
if Path("/proc/net/route").read_text().splitlines()[1:]:
    raise SystemExit("Outage namespace has a network route")
run = subprocess.run(json.loads(sys.argv[2]), check=True, stdout=subprocess.PIPE, timeout=300)
if len(run.stdout) > 4 * 1024 * 1024:
    raise SystemExit("Gate receipt exceeds size limit")
receipt = json.loads(run.stdout)
print(json.dumps({"namespace": namespace, "receipt": receipt, "rawReceipt": run.stdout.decode(),
 "receiptSha256": hashlib.sha256(run.stdout).hexdigest()}))
"""


def git(root: Path, *args: str) -> str:
    """Read local Git identity without inherited overrides or network fallback."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    return subprocess.check_output(
        ["git", "--no-replace-objects", "-c", "protocol.allow=never", *args],
        cwd=root,
        env=env,
        text=True,
        timeout=15,
    ).strip()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_checkout(root: Path, revision: str) -> None:
    if git(root, "rev-parse", "HEAD") != revision or git(
        root, "status", "--porcelain", "--untracked-files=no"
    ):
        raise ValueError("Expected the reviewed revision with no tracked changes")


def command(name: str, stage: str, values: dict[str, str]) -> list[str]:
    index = ("current", "rollback").index(stage)
    target, required = GATES[name][index]
    if values.keys() != required:
        raise ValueError(f"Unexpected or missing gate variables for {name}/{stage}")
    if any(not value or any(char in value for char in "\n\r\0$`") for value in values.values()):
        raise ValueError("Empty or unsafe Make variable value")
    for key, value in values.items():
        if key not in {"IMAGE", "CONTRACTS_ROLLBACK_SHA256"} and not Path(value).is_absolute():
            raise ValueError(f"Gate input must be absolute: {key}")
    if target == "prepare":
        return [
            "node",
            "scripts/contract-distribution.mjs",
            *[
                values[key]
                for key in (
                    "CONTRACTS_SHADOW_ASSETS",
                    "CONTRACTS_BOOTSTRAP",
                    "CONTRACTS_VERIFIER",
                    "CONTRACTS_STAGING_TARGET",
                )
            ],
        ]
    return [
        "make",
        "--no-print-directory",
        "--silent",
        target,
        *[f"{key}={value}" for key, value in sorted(values.items())],
    ]


def valid_evidence(evidence: dict, parent: str) -> bool:
    try:
        raw = evidence["rawReceipt"].encode()
        return (
            evidence["namespace"] != parent
            and str(evidence["namespace"]).startswith("net:[")
            and hashlib.sha256(raw).hexdigest() == evidence["receiptSha256"]
            and json_data(raw) == evidence["receipt"]
        )
    except (KeyError, AttributeError, ValueError):
        return False


def admit(revisions: dict, observations: dict, parent_namespace: str) -> list[dict[str, str]]:
    """Reject incomplete, stale or failed evidence even when other consumers passed."""
    gaps = []
    for name in CONSUMERS:
        row = observations.get(name, {})
        for stage in ("current", "rollback"):
            evidence = row.get(stage, {})
            receipt = evidence.get("receipt", {})
            if not isinstance(receipt, dict):
                receipt = {}
            selected = receipt.get("selection", receipt)
            if (
                row.get("revision") != revisions[name]
                or not valid_evidence(evidence, parent_namespace)
                or receipt.get("status")
                != EXPECTED_STATUS[name][("current", "rollback").index(stage)]
                or (name == "k8s" and receipt.get("prepared") is not True)
                or not isinstance(selected, dict)
                or selected.get("consumerCommit", revisions[name]) != revisions[name]
                or receipt.get("consumerRevision", revisions[name]) != revisions[name]
                or (
                    name == "console"
                    and receipt.get("controlPlaneCommit", revisions["control-plane"])
                    != revisions["control-plane"]
                )
            ):
                gaps.append(
                    {
                        "consumer": name,
                        "stage": stage,
                        "reason": evidence.get(
                            "error", "Missing, failed, stale or unisolated receipt"
                        ),
                    }
                )
    return gaps


def campaign(request: dict, manifest_digest: str) -> dict:
    schema = json_data(SCHEMA.read_bytes())
    Draft202012Validator({"$ref": "#/$defs/request", "$defs": schema["$defs"]}).validate(request)
    if Path(request["consumers"]["environment"]["root"]).resolve() != ROOT:
        raise ValueError("Campaign harness differs from the reviewed environment checkout")
    parent = os.readlink("/proc/self/ns/net")
    revisions = {name: entry["revision"] for name, entry in request["consumers"].items()}
    observations = {}
    for name in CONSUMERS:
        entry = request["consumers"][name]
        root = Path(entry["root"])
        row = {"revision": entry["revision"]}
        for stage in ("current", "rollback"):
            try:
                check_checkout(root, entry["revision"])
                for path, expected in entry["inputs"].items():
                    if digest(Path(path)) != expected:
                        raise ValueError("Retained input digest differs from the reviewed manifest")
                values = entry[stage]
                # Packages, receipts and verifier executables are independently hashed inputs.
                for key, value in values.items():
                    if (
                        key.endswith(("_WHEEL", "_PACKAGE", "_RECEIPT", "_VERIFIER"))
                        and value not in entry["inputs"]
                    ):
                        raise ValueError(f"Unpinned retained input: {key}")
                if "CONTRACTS_STAGING_TARGET" in values and any(
                    Path(values["CONTRACTS_STAGING_TARGET"])
                    .resolve()
                    .is_relative_to(Path(consumer["root"]).resolve())
                    for consumer in request["consumers"].values()
                ):
                    raise ValueError("Staging targets must remain outside consumer checkouts")
                for key in ("CONTRACTS_BOOTSTRAP", "CONTRACTS_BOOTSTRAP_ROOT"):
                    if key in values and Path(values[key]).resolve() != ROOT:
                        raise ValueError("Gate bootstrap differs from the reviewed environment")
                args = command(name, stage, values)
                env = {
                    **os.environ,
                    "PATH": ":".join(entry["binPath"]) + ":" + os.environ["PATH"],
                    "GOTOOLCHAIN": "local",
                    "GOPROXY": "off",
                    "UV_OFFLINE": "1",
                }
                output = subprocess.run(
                    [
                        "unshare",
                        "-Urn",
                        sys.executable,
                        "-IB",
                        "-c",
                        OFFLINE,
                        parent,
                        json.dumps(args),
                    ],
                    cwd=root,
                    env=env,
                    check=True,
                    capture_output=True,
                    timeout=330,
                )
                check_checkout(root, entry["revision"])
                result = json_data(output.stdout)
                if not isinstance(result, dict) or not isinstance(result.get("receipt"), dict):
                    raise ValueError("Gate did not produce a receipt object")
                row[stage] = result
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                reason = str(error)
                if isinstance(error, subprocess.CalledProcessError):
                    reason = (
                        error.stderr.decode(errors="replace")[-2000:] if error.stderr else reason
                    )
                row[stage] = {"error": reason}
        observations[name] = row
    gaps = admit(revisions, observations, parent)
    receipt = {
        "schemaVersion": 1,
        "status": "failed" if gaps else "passed",
        "campaignRevision": git(ROOT, "rev-parse", "HEAD"),
        "coverage": observations,
        "unavailableChecks": gaps,
        "inputManifestSha256": manifest_digest,
        "scope": "distribution recovery at the same selected schema revisions",
        "preconditions": "Preprovisioned dependencies, signed retained assets and local images",
    }
    Draft202012Validator(schema).validate(receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        manifest_bytes = args.manifest.read_bytes()
        request = json_data(manifest_bytes)
        if not args.execute:
            schema = json_data(SCHEMA.read_bytes())
            Draft202012Validator({"$ref": "#/$defs/request", "$defs": schema["$defs"]}).validate(
                request
            )
            print(
                json.dumps(
                    {
                        "status": "preview",
                        "consumers": {
                            name: {
                                stage: command(name, stage, request["consumers"][name][stage])
                                for stage in ("current", "rollback")
                            }
                            for name in CONSUMERS
                        },
                    },
                    indent=2,
                )
            )
            return 0
        result = campaign(request, hashlib.sha256(manifest_bytes).hexdigest())
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "passed" else 1
    except (OSError, ValueError, ValidationError, subprocess.SubprocessError) as error:
        parser.exit(1, f"contracts-campaign: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
