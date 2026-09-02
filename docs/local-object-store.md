# Local S3 artifact storage

SeaweedFS provides the local S3 endpoint for raw artifacts (proposal sections
35-37 and 83). This single-node development service is not production storage
or a claim of full AWS S3 compatibility. It replaces the prototype MinIO
deployment because the upstream MinIO repository is archived and unmaintained.
Platform artifact contracts remain vendor-neutral S3 references.

## Deploy and check

First complete the base cluster up/deploy steps. The script requires the
perf-platform namespace and kind's standard local-path StorageClass with delayed
binding. It verifies the local Docker endpoint and dedicated kubeconfig before
cluster access. Run from this repository in PowerShell or a POSIX shell:

~~~sh
uv run --locked python scripts/validate_cluster.py
uv run --locked python scripts/object_store.py deploy
uv run --locked python scripts/object_store.py deploy --execute
uv run --locked python scripts/object_store.py health --execute
uv run --locked python scripts/object_store.py smoke --execute
~~~

Without --execute, no credentials are generated, no cluster calls are made,
and no resources change.

- deploy: reuse/create credentials, install the chart, wait for storage, and
  create perfeng-artifacts if absent. Creation and authenticated access must succeed.
- health: check rollout, a Bound PVC, and authenticated bucket access. It creates
  a short-lived client Job, but writes no objects and creates no bucket.
- smoke: conditionally upload a fixed payload to a unique smoke/<probe-id>.bin
  key, require anonymous GET denial, download with authentication, and compare
  SHA-256 with the original payload. The object is retained.
- verify: read the exact earlier probe and check the same checksum/access denial
  without uploading or recreating it.

Smoke prints a probe ID and a verification command. After a pod restart, verify
that original ID (replace YOUR_PRINTED_PROBE_ID with its 32 hexadecimal characters):

~~~sh
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local -n perf-platform rollout restart statefulset/seaweedfs
uv run --locked python scripts/object_store.py verify --probe-id YOUR_PRINTED_PROBE_ID --execute
~~~

A missing object or checksum mismatch fails. Rerunning smoke instead would
create another object and would not test persistence.

Client Jobs have a 180-second deadline, no automatic retries, and cleanup 600
seconds after completion/failure. They use pinned AWS CLI, Secret references,
bounded resources, and no service-account token. Failures report the Job name;
inspect its status/logs within 10 minutes. Deployment failures retain resources
and credentials. Progress messages indicate waiting, not guaranteed progress.

## Runtime and security

- SeaweedFS 4.45 image index:
  sha256:fc9f76fa993ad69966ffeb2f65d0318fcae39c6f8e20cf68ef7b3a5cb97769e5.
- AWS CLI 2.36.37 image index:
  sha256:be6228bd99b4b0b9787543952aebfe93c66b5503121b68a724647c523c957a9d.
- HAProxy 3.2.23-alpine index:
  sha256:6343ce34a132a5dceaa24767d739df2bd519f8f7c1079ae39e4821334e8eb42e.
- Version tags were resolved on 2026-09-02; pins are in the chart values.
- Namespace/release: perf-platform / seaweedfs.
- Endpoint: http://seaweedfs.perf-platform.svc.cluster.local:8333.
- Region: us-east-1; path-style addressing; bucket: perfeng-artifacts.
- PVC: data-seaweedfs-0, 5 GiB, ReadWriteOnce, standard local-path storage.
- Placement: control-plane node, away from workload workers but on the same host.

The all-in-one weed server binds all its services to pod loopback, including S3
HTTP on 9000 and S3's internal gRPC listener on 19000. A small HAProxy TCP sidecar
forwards only pod port 8333 to loopback 9000 without modifying HTTP signatures.
The Service exposes only port 8333; no internal gRPC service is pod-network accessible.
WebDAV is not enabled. Embedded S3 IAM, Iceberg/Lance listeners, automatic bucket
creation on upload, recursive nonempty-bucket deletion, and telemetry are disabled.
There is no host port or ingress. TCP probes check availability; client Jobs
perform stronger S3 checks. The server runs non-root with a read-only root
filesystem, dropped capabilities, and no service-account token.

First deployment generates an access key and a 256-bit secret key, creating the
immutable perfeng-s3-auth Secret through private stdin. Credentials are absent
from arguments, chart values, Helm release data, Git, and host files. The server
uses a read-only Secret file; clients use Secret-backed environment variables.
Redeploy reuses credentials. Missing credentials alongside an existing PVC or
StatefulSet cause refusal rather than silent regeneration.

The bootstrap identity has administrative access. The local k6 uploader now uses
a [separate restricted identity](storage-access.md). Chart 0.2.0 mounts the
additive server configuration Secret; the original bootstrap Secret is retained.
Deploy creates/reuses the restricted Secret pair before upgrading the chart. TLS,
encryption at rest, and NetworkPolicy enforcement are not configured. Kubernetes
Secret-read/pod-exec privileges confer storage-admin access. Do not expose this
service externally or store production data. The smoke check is not a complete
authorization audit.

## Persistence, retention, and boundaries

Master state and filer metadata live under /data/master; volume files live under
/data/volumes, all on the retained PVC. Pod restart and Helm redeploy reuse those
paths. StatefulSet deletion/scaling retains the PVC. Helm does not own the Secret.

Client-Job cleanup never removes objects. No lifecycle expiry or automatic bucket
cleanup is configured. Monitor disk usage: local-path PVC requests are not always
enforced quotas. The 16-volume, 128 MiB target-volume configuration is also not a
strict byte quota. Smoke probes deliberately remain for restart verification.

Deleting the namespace, PVC, kind cluster, Docker storage, or host can destroy
data. Before teardown, export important objects through S3 to independent storage,
verify checksums, and preserve credentials separately. Automated backups/restore,
replication, versioning policy, Object Lock, and compliance retention are not
implemented. A conditional probe upload is not general artifact immutability.

The environment owns deployment and local checks. Runner/control-plane integration
must record artifact URI, checksum, size, measurement
window, and producer identity using perfeng-contracts. This step does not run k6
or upload real run artifacts. The prototype MinIO chart/data remains untouched;
existing data requires explicit export/import, not a filesystem move.

References: [SeaweedFS pinned source](https://github.com/seaweedfs/seaweedfs/tree/4.45),
[MinIO maintenance status](https://github.com/minio/minio),
[AWS CLI S3 API](https://docs.aws.amazon.com/cli/latest/reference/s3api/).
