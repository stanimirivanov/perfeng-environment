# Restricted local artifact uploader

The k6 upload container uses perfeng-s3-uploader-auth, not the bootstrap
administrator. Its static IAM policy permits only s3:GetObject and s3:PutObject
for arn:aws:s3:::perfeng-artifacts/runs/*. There are no legacy Read/Write grants:
SeaweedFS's legacy Write action also covers deletion. Object listing, deletion,
bucket administration and objects outside this resource scope are not granted.
The k6 container itself still receives no storage credentials.

## Upgrade an existing local installation

Run from perfeng-environment after reviewing the previews:

~~~powershell
uv run --locked python scripts/object_store.py deploy
uv run --locked python scripts/storage_access.py
uv run --locked python scripts/object_store.py deploy --execute
uv run --locked python scripts/storage_access.py --execute
uv run --locked python scripts/k6_run.py --execute
~~~

Deployment is additive:

1. Validate and retain the existing immutable perfeng-s3-auth bootstrap Secret.
2. Create a separate immutable perfeng-s3-uploader-auth Secret containing only
   the new uploader access key and secret key.
3. Create immutable perfeng-s3-server-auth-v2 containing s3.json, which combines
   the unchanged bootstrap identity with the uploader and its attached policy.
4. Upgrade chart 0.2.0 to mount that server Secret. This restarts SeaweedFS;
   schedule the upgrade when no captures are running. Its PVC is reused.

Credentials are sent through private stdin, not command arguments, host files,
Git, chart values or Helm release data. Helm owns neither Secret. Redeployment
reuses both identities; it does not rotate them. A partial migration with an
existing uploader but no server Secret can be retried. Unknown, mutable,
inconsistent or orphaned credentials are refused. Restore matching Secrets
instead of deleting them to force regeneration.

The new k6 preflight validates the Secret pair and the StatefulSet's configured
Secret reference. It refuses missing/stale setup without falling back to admin
credentials. This metadata check is not a substitute for the live policy probe.

## Authorization verification

The preview makes no cluster calls. Execution creates a bounded client Job
using only the uploader Secret. It writes a unique runs/access-check/ probe,
reads it back and verifies its SHA-256. It requires explicit access-denied
responses for deletion of that same disposable probe, PUT/GET outside runs/,
object listing, bucket-policy reads and a GET in another bucket. It rereads the
probe to check it remains intact. Network errors and missing-object responses
do not count as successful denial tests.

The deletion attempt targets only the newly created probe, never a run artifact
or bucket. If authorization is broken, the probe might be deleted or an
outside-prefix probe might be written; either fails verification. No automatic
cleanup hides that evidence. Successful probes remain; client Jobs expire after
10 minutes. Failed checks report the Job name for inspection.

The original object_store health/smoke/verify commands intentionally retain
bootstrap access for provisioning and independent persistence diagnostics.
Embedded IAM management endpoints remain disabled; policies are loaded from
the static server file.

## Boundaries and recovery

This is a shared local uploader identity, not per-run or per-tenant isolation.
It can read every run and can replace an existing run object if used without
the uploader's conditional PUT. IAM PutObject is not append-only storage;
Object Lock, versioning and stronger overwrite prevention remain future work.
No claim of a complete S3 authorization audit is made.

Existing retained Jobs are not rewritten or stripped of old Secret references.
Their Pod specifications can still reference bootstrap credentials. Restrict
Kubernetes Secret-read, Pod-exec and Job/Pod creation access; those privileges
can bypass this container-level separation. Namespace isolation, TLS, encryption,
rotation, credential revocation and production hardening remain separate work.

If rollout fails, preserve the three Secrets and PVC and inspect the deployment.
The prior chart revision can be restored through a deliberate Helm rollback;
the unchanged original bootstrap Secret remains available for it. Do not
automatically roll back after an authorization failure: diagnose and restore a
known configuration before resuming captures. New k6 commands intentionally
refuse an older server configuration.

Implementation references:
[SeaweedFS 4.45 IAM configuration](https://github.com/seaweedfs/seaweedfs/blob/4.45/weed/pb/iam.proto)
and [authorization routing](https://github.com/seaweedfs/seaweedfs/blob/4.45/weed/s3api/auth_credentials.go).
