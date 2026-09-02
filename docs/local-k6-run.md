# Local k6 execution and raw artifact capture

This development-only slice runs checkout/smoke against the in-cluster sample API.
It uses the published Linux AMD64 runner digest recorded in scripts/k6_run.py,
from source revision a916ef3bcbfe9337504bdba6ddddcafe46f32b81.
No registry credentials are needed for this public image.

## Prerequisites and execution

The named local kind cluster, current sample API chart, SeaweedFS, its matching
immutable Secret, and the perfeng-artifacts bucket must already exist.
Deploy/update those explicitly using cluster.py and object_store.py first.
The run command never installs or repairs prerequisites.
It rejects the obsolete echo fixture or a sample API image/source checksum that
does not match the current chart.

~~~powershell
uv run --locked python scripts/k6_run.py
uv run --locked python scripts/k6_run.py --execute
~~~

The first command previews without accessing Docker, Kubernetes or storage.
Execution verifies the dedicated local kubeconfig, creates a fresh named Job,
and reports progress during image pull, the 120-second workload and upload.
The only target is the local sample API; arbitrary URLs and load profiles are
intentionally not accepted. The workload configuration is checked against its
known SHA-256 before running. No developer-shell k6 overrides are inherited.

## Execution and storage outcomes

A non-root k6 init container writes raw output to a bounded shared emptyDir.
It records the real k6 exit status and then permits the upload container to run,
including after threshold failure. An init-container crash/OOM or setup failure
is an infrastructure failure, not a completed performance test.
The uploader starts after load generation has stopped and mounts results read-only.
Neither container receives a Kubernetes service-account token.

Artifacts use a unique runs/RUN_ID/ prefix:

- workload.json and workload-definition.json: packaged configuration and definition.
- summary.json and points.jsonl: unmodified k6 output, when produced.
- runner.log: diagnostic output, including failed runs.
- status.json: k6 exit code and process start/finish timestamps.
- receipt.json: local-capture/v1 inventory with image/source provenance, object
  URIs, SHA-256 checksums and byte counts.

Each PUT is conditional on the key not existing. Each object is downloaded and
its SHA-256 compared before the receipt is written last and itself read back.
The printed receipt URI means upload verification completed, not that k6 passed.
The command returns k6's exit code (including threshold failure 99) when the
summary and points are nonempty. Missing output or infrastructure/upload errors
return nonzero. Successful capture does not attest metric quality.

The receipt is deliberately not a perfeng-contracts RawResult. Process timestamps
are not measurement-window boundaries. Resolved execution configuration,
measurement-phase instrumentation, contract assembly and normalization are
subsequent integration work; no measurement window is inferred here.

## Security, retention and failure handling

Only the trusted upload container gets the existing local bootstrap S3 Secret;
the k6 container has no storage credentials. This bootstrap identity has broad
storage permissions: it is not a production or multi-tenant least-privilege
design. The Job temporarily lives in perf-platform to reference that Secret,
but runs on the performance-generator node. A dedicated upload identity and
namespace isolation are prerequisites for widening this beyond the fixed fixture.

Jobs have no retry and a 15-minute active deadline. No TTL cleanup is configured,
and the command never deletes Jobs, artifacts, credentials or PVCs. Inspect the
printed Job name and its k6/upload container logs using the dedicated kubeconfig.
Pod deletion or eviction loses unuploaded emptyDir data; this is not a durable
local spool. Partial uploads without a receipt are incomplete captures. Do not
interpret them as successful runs. Retry creates a new run, not a replay.
There is no automatic recovery/re-upload workflow in this slice.

Uploaded data survives ordinary Job/Pod deletion through SeaweedFS's PVC, but
cluster deletion still destroys local storage. No backup or production durability
is promised. Logs and points may contain sensitive target metadata; avoid real
credentials and private datasets, and apply a reviewed retention policy later.
SeaweedFS uses the existing local HTTP endpoint without TLS.

References: [Kubernetes init containers](https://kubernetes.io/docs/concepts/workloads/pods/init-containers/)
and [AWS CLI conditional PUT](https://docs.aws.amazon.com/cli/latest/reference/s3api/put-object.html).
