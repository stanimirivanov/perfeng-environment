"""Preview-first checkout smoke Job and verified raw-artifact upload to local SeaweedFS."""

import argparse
import hashlib
import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import cluster, object_store, storage_access
else:
    import cluster
    import object_store
    import storage_access

IMAGE = (
    "ghcr.io/stanimirivanov/perfeng-k6@sha256:"
    "f40f15780dd4eb8124a327c5d7a051848276494bec77f2c18590520be5c5c962"
)
REVISION = "a916ef3bcbfe9337504bdba6ddddcafe46f32b81"
CONFIG_HASH = "fb8080390fbff61fdec8c4e1dc9d70eb44a92195bb29e6000b74d69e53344ab2"
TARGET = "http://sample-sut.perf-sut.svc.cluster.local:8080"
RUN_PATTERN = r"perf-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}"


def validate_run_id(run_id: str) -> None:
    if not re.fullmatch(RUN_PATTERN, run_id):
        raise ValueError("Invalid run ID")


def runner_script() -> str:
    # Deliberately capture process bounds, not a fabricated measurement window.
    return f"""set -eu
cd /tests
test ! -e /results/status.json
cp workloads/smoke/checkout.json /results/workload.json
cp definitions/smoke/checkout.json /results/workload-definition.json
set -- $(sha256sum /results/workload.json)
test "$1" = "{CONFIG_HASH}"
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
code=0
k6 run --no-usage-report --config /results/workload.json \\
  --summary-export /results/summary.json --out json=/results/points.jsonl \\
  tests/checkout/scenario.js > /results/runner.log 2>&1 || code=$?
finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{{"exitCode":%s,"processStartedAt":"%s","processFinishedAt":"%s"}}\\n' \\
  "$code" "$started" "$finished" > /results/status.json
echo "k6 exited $code; handing available output to the uploader"
"""


def upload_script(run_id: str) -> str:
    validate_run_id(run_id)
    return f"""set -eu
printf '[default]\\ns3 =\\n    addressing_style = path\\n' > /tmp/awsconfig
aws_s3() {{ aws --endpoint-url "{object_store.ENDPOINT}" --cli-connect-timeout 10 --cli-read-timeout 30 s3api "$@"; }}
upload() {{
  file=$1
  key="runs/{run_id}/$2"
  set -- $(sha256sum "$file")
  expected=$1
  aws_s3 put-object --bucket "{object_store.BUCKET}" --key "$key" --body "$file" --if-none-match "*" >/dev/null
  aws_s3 get-object --bucket "{object_store.BUCKET}" --key "$key" /tmp/download >/dev/null
  set -- $(sha256sum /tmp/download)
  test "$1" = "$expected"
}}
test -s /results/status.json
printf '{{"schema":"local-capture/v1","runId":"{run_id}","runnerImage":"{IMAGE}","sourceRevision":"{REVISION}","artifacts":[' > /tmp/receipt.json
separator=""
for name in workload.json workload-definition.json status.json runner.log summary.json points.jsonl; do
  if test -f "/results/$name"; then
    upload "/results/$name" "$name"
    set -- $(sha256sum "/results/$name")
    hash=$1
    size=$(wc -c < "/results/$name" | tr -d ' ')
    printf '%s{{"name":"%s","uri":"s3://{object_store.BUCKET}/runs/{run_id}/%s","sha256":"%s","sizeBytes":%s}}' \\
      "$separator" "$name" "$name" "$hash" "$size" >> /tmp/receipt.json
    separator=","
  fi
done
printf ']}}\\n' >> /tmp/receipt.json
# Last object is the completion marker. Every listed object has been read back.
upload /tmp/receipt.json receipt.json
complete=false
if test -s /results/summary.json && test -s /results/points.jsonl; then complete=true; fi
printf '{{"runId":"{run_id}","artifactsComplete":%s,"status":' "$complete"
cat /results/status.json
printf '}}\\n'
"""


def job(run_id: str, root: Path = cluster.ROOT) -> dict[str, Any]:
    validate_run_id(run_id)
    # Reuse the pinned AWS client and credential handling, not its smoke payload.
    document = object_store.client_job("health", "0" * 32, run_id, root)
    storage_access.restrict_job(document)
    spec = document["spec"]
    spec.pop("ttlSecondsAfterFinished")
    spec["activeDeadlineSeconds"] = 900
    pod = spec["template"]["spec"]
    pod["nodeSelector"] = {"workload": "performance-generator", "kubernetes.io/arch": "amd64"}
    pod.pop("tolerations")
    pod["enableServiceLinks"] = False
    pod["securityContext"].update(runAsUser=12345, runAsGroup=12345, fsGroup=12345)
    pod["volumes"] = [
        {"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}},
        {"name": "results", "emptyDir": {"sizeLimit": "256Mi"}},
    ]
    uploader = pod["containers"][0]
    uploader["name"] = "upload"
    uploader["command"] = ["/bin/sh", "-c", upload_script(run_id)]
    uploader["volumeMounts"].append({"name": "results", "mountPath": "/results", "readOnly": True})
    pod["initContainers"] = [
        {
            "name": "k6",
            "image": IMAGE,
            "imagePullPolicy": "Always",
            "command": ["/bin/sh", "-c", runner_script()],
            "env": [
                {"name": "BASE_URL", "value": TARGET},
                {"name": "API_VERSION", "value": "v1"},
                {"name": "THINK_TIME", "value": "1"},
            ],
            "securityContext": dict(uploader["securityContext"]),
            "resources": {
                "requests": {"cpu": "500m", "memory": "256Mi", "ephemeral-storage": "256Mi"},
                "limits": {"cpu": "1", "memory": "512Mi", "ephemeral-storage": "768Mi"},
            },
            "volumeMounts": [{"name": "results", "mountPath": "/results"}],
        }
    ]
    return document


def outcome(text: str, run_id: str) -> int:
    """A successful upload is not necessarily a successful or usable test."""
    value = json.loads(text)
    if not isinstance(value, dict) or value.get("runId") != run_id:
        raise ValueError("Uploader returned an invalid run identity")
    status = value.get("status")
    if not isinstance(status, dict):
        raise ValueError("Missing runner status")
    code = status.get("exitCode")
    if type(code) is not int or not 0 <= code <= 255:
        raise ValueError("Missing or invalid k6 exit code")
    if value.get("artifactsComplete") is not True:
        raise ValueError(
            f"Artifacts uploaded, but summary or points are missing/empty (k6 exit {code})"
        )
    return code


def validate_sample(text: str, root: Path = cluster.ROOT) -> None:
    expected = yaml.safe_load((root / "charts/sample-sut/values.yaml").read_text(encoding="utf-8"))
    checksum = hashlib.sha256((root / "charts/sample-sut/files/server.py").read_bytes()).hexdigest()
    try:
        template = json.loads(text)["spec"]["template"]
        containers = template["spec"]["containers"]
        api = next(c for c in containers if c["name"] == "sample-api")
        valid = (
            api["image"] == expected["image"]
            and template["metadata"]["annotations"]["checksum/api"] == checksum
        )
    except (KeyError, TypeError, StopIteration, ValueError):
        valid = False
    if not valid:
        raise ValueError("Sample API deployment is stale; run cluster.py deploy --execute first")


def execute(run_id: str, root: Path = cluster.ROOT) -> int:
    document = job(run_id, root)
    cluster.verify_local_docker()
    cluster.verify_context(root)
    kube = object_store.kubectl(root)
    # All preflights are read-only. Never deploy/repair services as a side effect.
    storage_access.ensure_credentials(root, create=False)
    cluster.run([*kube, "rollout", "status", "statefulset/seaweedfs", "--timeout=60s"])
    cluster.run(
        [
            "kubectl",
            "--kubeconfig",
            str(cluster.kubeconfig(root)),
            "--context",
            cluster.CONTEXT,
            "-n",
            "perf-sut",
            "--request-timeout=30s",
            "rollout",
            "status",
            "deployment/sample-sut",
            "--timeout=60s",
        ]
    )
    validate_sample(
        cluster.run(
            [
                "kubectl",
                "--kubeconfig",
                str(cluster.kubeconfig(root)),
                "--context",
                cluster.CONTEXT,
                "-n",
                "perf-sut",
                "--request-timeout=30s",
                "get",
                "deployment",
                "sample-sut",
                "-o",
                "json",
            ]
        ),
        root,
    )
    pvc = json.loads(cluster.run([*kube, "get", "pvc", "data-seaweedfs-0", "-o", "json"]))
    if pvc.get("status", {}).get("phase") != "Bound":
        raise ValueError("SeaweedFS storage is not Bound")
    print(f"Creating {run_id}: checkout/smoke (120 seconds plus startup/upload).", flush=True)
    cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(document).encode("utf-8"))
    deadline = time.monotonic() + 960
    while time.monotonic() < deadline:
        value = json.loads(cluster.run([*kube, "get", "job", run_id, "-o", "json"]))
        conditions = value.get("status", {}).get("conditions", [])
        if any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions):
            raise ValueError(
                f"Job {run_id} failed: execution/upload infrastructure failure; inspect Pods"
            )
        if any(c.get("type") == "Complete" and c.get("status") == "True" for c in conditions):
            output = cluster.run([*kube, "logs", f"job/{run_id}", "-c", "upload"])
            print(f"Verified capture: s3://{object_store.BUCKET}/runs/{run_id}/receipt.json")
            code = outcome(output, run_id)
            print(f"k6 exit {code}; artifact upload/read-back completed. Job retained: {run_id}")
            return code
        print(f"Waiting for {run_id} (image pull, k6, then upload)...", flush=True)
        time.sleep(5)
    raise ValueError(
        f"Timed out observing {run_id}; inspect it before retrying. No resources deleted"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    run_id = (
        "perf-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        if args.execute
        else "perf-20000101-000000-00000000"
    )
    try:
        if args.execute:
            return execute(run_id)
        print(json.dumps(job(run_id), indent=2))
        print("Preview only. --execute runs the local fixture and uploads retained artifacts.")
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Local k6 run error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
