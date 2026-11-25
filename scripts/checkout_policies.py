"""Confirm the checkout and benchmark policies selected by the approved lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from collections.abc import Sequence
from pathlib import Path

from . import checkout_operator, cluster, control_plane_release, policy_publish
from .checkout_checkpoint import private_bytes

TESTS = ("checkout-api", "pod-startup-k8s")


def selected_policies(
    release: control_plane_release.DeploymentLock, k8s_root: Path
) -> dict[str, policy_publish.PolicySource]:
    """Read both distinct smoke policies from the immutable deployment revision."""
    read = control_plane_release.revision_reader(
        k8s_root, control_plane_release.DEPLOYMENT_REPOSITORY, release.deployment_revision
    )
    registry_bytes = read(f"{release.bundle_path}/registry.json")
    registry = json.loads(registry_bytes, object_pairs_hook=policy_publish.unique_object)
    policies = {}
    for test_id in TESTS:
        matches = [
            entry
            for entry in registry["entries"]
            if entry["testId"] == test_id and entry["profile"] == "smoke"
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one reviewed smoke policy for {test_id}")
        entry = matches[0]
        path = control_plane_release.portable_path(entry["policy"], "policy path")
        content = read(f"{release.bundle_path}/{path}")
        document = json.loads(
            content,
            object_pairs_hook=policy_publish.unique_object,
            parse_constant=policy_publish.reject_non_finite,
        )
        identity = policy_publish.policy_identity(document)
        policies[test_id] = policy_publish.PolicySource(
            content,
            path,
            entry["contractsVersion"],
            hashlib.sha256(registry_bytes).hexdigest(),
            *identity,
        )
    if len({source.key for source in policies.values()}) != len(TESTS):
        raise ValueError("Checkout and benchmark must select distinct policy objects")
    return policies


def main(arguments: Sequence[str] | None = None, *, root: Path = control_plane_release.ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-sha256", required=True)
    parser.add_argument("--cluster-uid", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--publish-missing", choices=TESTS, action="append", default=[])
    args = parser.parse_args(arguments)
    try:
        check = checkout_operator.verify_substrate if args.execute else checkout_operator.select
        selection = check(root, args.lock_sha256, args.cluster_uid)
        sources = selected_policies(selection.release, root.parent / "perfeng-k8s")
        directory = args.evidence_dir.absolute()
        if any(
            (parent / ".git").is_file() or (parent / ".git/HEAD").is_file()
            for parent in (directory, *directory.parents)
        ):
            raise ValueError("Keep the private policy evidence directory outside Git")
        info = directory.stat()
        if (
            directory.resolve(strict=True) != directory
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("Use an owner-only private evidence directory")
        for test_id, source in sources.items():
            details = policy_publish.receipt(source)
            print(f"{test_id} {json.dumps(details['policy'])}")
            delivered = directory / f"{test_id}.published.json"
            receipt = directory / f"{test_id}.receipt.json"
            print(f"Owner GET: {delivered}")
            print(f"Receipt: {receipt}")
            if test_id in args.publish_missing:
                print("Owner-confirmed missing key: conditional create and verified read-back")
                if args.execute:
                    if delivered.exists() or delivered.is_symlink():
                        raise ValueError(
                            f"{test_id} has both a delivered object and a missing claim"
                        )
                    with cluster.command_deadline(600):
                        policy_publish.execute(source, receipt, root)
            elif args.execute:
                content = private_bytes(delivered, len(source.content))
                if content != source.content:
                    raise ValueError(f"Owner GET does not match reviewed {test_id} bytes")
                details["verification"] = "owner-delivered-exact-bytes"
                with receipt.open("x", encoding="utf-8") as output:
                    json.dump(details, output, indent=2)
                    output.write("\n")
        if args.execute:
            print("Both selected policies are confirmed.")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Policy confirmation stopped: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
