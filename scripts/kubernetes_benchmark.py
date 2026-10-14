"""Preview or explicitly collect read-only local Kubernetes benchmark qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from scripts import kubernetes_benchmark_capture as capture
from scripts import kubernetes_benchmark_contract as contract
from scripts import kubernetes_benchmark_profile as cohort


def check(id: str, status: str, reason: str) -> dict[str, str]:
    """Construct one safe declared check, without including vendor output."""
    return {"id": id, "status": status, "reason": reason}


def evaluate(
    profile: cohort.Profile, observed: capture.Capture, root: Path = contract.ROOT
) -> tuple[dict[str, Any], bytes]:
    """Evaluate only the integration profile; preserve unknown observations and failure precedence."""
    identity, descriptor = cohort.environment(profile, observed.uid, observed.inventory)
    samples = []
    readiness: list[str] = []
    pressure: list[str] = []
    missing_usage = not observed.inventory
    high_usage = False
    start = observed.start
    for node in observed.inventory:
        conditions = node["conditions"]
        readiness.append(conditions["ready"])
        pressure.extend(
            conditions[key] for key in ("memoryPressure", "diskPressure", "pidPressure")
        )
        usage = observed.measured.get(node["name"])
        if usage is None:
            missing_usage = True
            usage = {
                "source": "unavailable",
                "reason": "Metrics API missing, denied, invalid or stale; review metrics availability and clock, not node capacity.",
            }
        else:
            start = min(start, datetime.fromisoformat(usage["windowStart"].replace("Z", "+00:00")))
            allocation = node["allocatable"]
            high_usage |= (
                usage["cpuMilliCores"] > allocation["cpuMilliCores"] * profile.cpu_ratio
                or usage["memoryBytes"] > allocation["memoryBytes"] * profile.memory_ratio
            )
        samples.append(
            {
                "nodeUid": node["uid"],
                "observedAt": capture.stamp(observed.end),
                "conditions": dict(conditions),
                "usage": dict(usage),
            }
        )
    ready = (
        "FAIL"
        if "FALSE" in readiness
        else "UNKNOWN"
        if not readiness or "UNKNOWN" in readiness
        else "PASS"
    )
    pressured = (
        "FAIL"
        if "TRUE" in pressure
        else "UNKNOWN"
        if not pressure or "UNKNOWN" in pressure
        else "PASS"
    )
    interference = (
        "FAIL"
        if high_usage or (observed.active is not None and not observed.active <= profile.namespaces)
        else "UNKNOWN"
        if missing_usage or observed.active is None
        else "PASS"
    )
    unreviewed = sorted(observed.active - profile.namespaces) if observed.active is not None else []
    interference_reason = "Fresh metrics meet integration usage budgets and active namespaces match the reviewed cohort."
    if unreviewed:
        interference_reason = (
            "Active namespaces outside the reviewed cohort: "
            + ", ".join(unreviewed[:8])
            + "; inspect/finish the workloads explicitly."
        )
    elif high_usage:
        interference_reason = "Observed node CPU or memory usage exceeds the selected integration budget; inspect load and recapture."
    elif missing_usage or observed.active is None:
        interference_reason = (
            "Required fresh node usage is unavailable; inspect the Metrics API and provider timestamps. "
            if missing_usage
            else ""
        ) + (
            "Active namespace inventory is unavailable; inspect Pod-list permissions/API connectivity."
            if observed.active is None
            else ""
        )
    checks = [
        check(
            "cluster-identity",
            "PASS",
            "Verified local Docker endpoint, named kind credentials, ready API and actual kube-system UID.",
        ),
        check(
            "node-readiness",
            ready,
            {
                "PASS": "All observed nodes report Ready=TRUE.",
                "FAIL": "At least one observed node is not Ready; inspect node conditions.",
                "UNKNOWN": "Node inventory or Ready conditions are unavailable; inspect the dedicated cluster.",
            }[ready],
        ),
        check(
            "placement",
            "UNKNOWN" if observed.placement is None else "PASS" if observed.placement else "FAIL",
            "Exact reviewed placement is present and uncordoned."
            if observed.placement
            else "Node placement is unavailable or differs from reviewed names/labels/cordon state; inspect the selected profile.",
        ),
        check(
            "capacity",
            "UNKNOWN" if observed.capacity is None else "PASS" if observed.capacity else "FAIL",
            "Node image/version, Linux amd64 and allocatable resources satisfy the integration profile."
            if observed.capacity
            else "Capacity/image observations are unavailable or fail the reviewed image/version/architecture/resource budgets; inspect Docker node images and the profile.",
        ),
        check(
            "resource-pressure",
            pressured,
            {
                "PASS": "No sampled MemoryPressure, DiskPressure or PIDPressure is reported.",
                "FAIL": "A sampled node reports resource pressure; inspect node conditions.",
                "UNKNOWN": "Pressure conditions/inventory are unavailable; missing conditions remain UNKNOWN.",
            }[pressured],
        ),
        check(
            "interference",
            interference,
            interference_reason,
        ),
    ]
    status = (
        "REJECTED"
        if any(item["status"] == "FAIL" for item in checks)
        else "UNKNOWN"
        if any(item["status"] == "UNKNOWN" for item in checks)
        else "ACCEPTED"
    )
    evidence = {
        "schemaVersion": 1,
        "kind": "KubernetesBenchmarkEvidence",
        "scope": {"kind": "preflight"},
        "profile": profile.identity(),
        "environment": identity,
        "target": {"clusterId": "perfeng-local", "kubeSystemUid": observed.uid},
        "captureWindow": {"start": capture.stamp(start), "end": capture.stamp(observed.end)},
        "status": status,
        "checks": checks,
        "nodes": [
            {key: value for key, value in node.items() if key != "conditions"}
            for node in observed.inventory
        ],
        "samples": samples,
    }
    contract.validate(evidence, root)
    return evidence, descriptor


def destination(path: Path, root: Path = contract.ROOT) -> Path:
    """Allow new receipts only in ordinary ignored results; reject existing or redirected paths."""
    results = (root / "results").absolute()
    output = path.absolute()
    if (
        output.parent != results
        or results.resolve() != results
        or results.is_symlink()
        or results.is_junction()
        or output.exists()
        or output.is_symlink()
        or output.is_junction()
    ):
        raise ValueError("Use a new filename directly under the ordinary results directory")
    if output.suffix != ".json":
        raise ValueError("Qualification receipt must use a .json suffix")
    return output


def publish(path: Path, content: bytes) -> None:
    """Atomically create a complete receipt without replacing existing files, including race winners."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".qualification-", delete=False
    ) as output:
        temporary = Path(output.name)
        try:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            output.close()
            temporary.unlink()
            raise
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    """Keep validation/preview offline; --execute permits reads and two new immutable local files only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "check"))
    parser.add_argument("--profile", type=Path, default=cohort.PROFILE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--contracts-root", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    stage = "offline profile/schema validation"
    try:
        profile = cohort.load(args.profile)
        contract.schema()
        if args.contracts_root is not None:
            if args.action != "validate":
                raise ValueError("Source verification is an offline validation action")
            contract.validate_source(args.contracts_root)
        if args.action == "validate" or not args.execute:
            if args.execute or (args.action == "validate" and args.output is not None):
                raise ValueError("Validation cannot collect observations or write receipts")
            print(
                json.dumps(
                    {
                        "profile": profile.identity(),
                        "preview": args.action == "check",
                        "cluster": "perfeng-local",
                        "context": "kind-perfeng-local",
                        "outputDirectory": "results",
                    }
                )
            )
            if args.action == "check":
                print(
                    "Preview only: no processes, network, receipts or state changes. Add --execute."
                )
            return 0
        stage = "new results destination validation"
        path = destination(
            args.output or contract.ROOT / "results" / f"m04-qualification-{uuid4().hex}.json",
            contract.ROOT,
        )
        descriptor_path = destination(
            path.with_name(path.stem + "-environment.json"), contract.ROOT
        )
        stage = "guarded local cluster capture"
        observed = capture.collect(contract.ROOT, profile)
        if cohort.load(args.profile) != profile:
            raise ValueError(
                "Qualification policy or infrastructure selection changed during capture"
            )
        print("[4/4] Evaluating integration checks and retaining exact evidence...", flush=True)
        stage = "profile evaluation and evidence validation"
        evidence, descriptor = evaluate(profile, observed)
        content = (json.dumps(evidence, indent=2) + "\n").encode("utf-8")
        destination(path, contract.ROOT)
        destination(descriptor_path, contract.ROOT)
        stage = "create-only descriptor/receipt publication"
        publish(descriptor_path, descriptor)
        publish(path, content)
        for item in evidence["checks"]:
            print(f"{item['status']} {item['id']}: {item['reason']}")
        print(
            json.dumps(
                {
                    "path": str(path),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "sizeBytes": len(content),
                    "status": evidence["status"],
                    "environmentPath": str(descriptor_path),
                    "environmentSha256": evidence["environment"]["sha256"],
                    "purpose": "integration",
                }
            )
        )
        return 0 if evidence["status"] == "ACCEPTED" else 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
        print(
            f"Kubernetes qualification error during {stage}: review the profile/contract checksum, task status and a new ordinary results filename. External payloads withheld; no automatic repair or replacement.",
            flush=True,
        )
        return 2
    except KeyboardInterrupt:
        print("Qualification interrupted; cluster, credentials and completed evidence retained.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
