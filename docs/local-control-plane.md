# Local control-plane release

## TL;DR

- The deployment lock pins the control-plane source, image digest, chart commit
  runtime bundle and content-addressed runtime ConfigMaps as one reviewed
  selection.
- Provision PostgreSQL, storage, registry access, migrations and immutable
  runtime inputs in the order below; Argo CD does not own those resources yet.
- The preferred workload reconciler is the control-plane child Application in
  [the local GitOps environment](local-gitops.md).
- The superseded Helm deploy command has been removed. Migration-bearing releases
  need [coordinated cutover/recovery](kubernetes-runtime-deployment.md), not an image-only Git revert.
- Smoke and persistence checks validate the live workload without depending on
  Helm release history.
- Diagnose failures in dependency order with the
  [local troubleshooting runbook](troubleshooting.md).

`deployments/local-control-plane.json` is the reviewed deployment lock for the
first local control-plane integration. It selects three independent immutable
identities:

- the published Linux AMD64 control-plane image digest and its source commit;
- the exact `perfeng-k8s` commit and control-plane Helm chart version;
- the runtime-registry bundle and contracts version at that same deployment
  commit.

Validate the selection against the adjacent deployment repository without
checking out or modifying its working tree:

```sh
uv run --locked python scripts/control_plane_release.py validate
```

Validation reads tracked files directly from the locked Git commits. Newer or
dirty sibling working trees therefore cannot silently change the selected
source, chart or bundle. CI checks out the exact private revisions using the
read-only `PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN` secret and performs the
same validation.

This lock is deployment input, not proof that an image is registry-addressable
or that a cluster is healthy. It contains no credentials and does not create
Kubernetes resources.

## Local infrastructure prerequisites

The current M04 migration-bearing selection follows the
[Kubernetes runtime deployment guide](kubernetes-runtime-deployment.md). It
supersedes automatic activation: explicitly pause before migrations, prepare
external inputs and execution pull credentials, then manually sync after merge.

The source-workspace bootstrap does not create runtime infrastructure. On a
fresh cluster, follow the complete
[staged GitOps bootstrap](local-gitops.md#fresh-cluster-staged-bootstrap). It
creates external credentials before Argo CD reconciles PostgreSQL and SeaweedFS,
then initializes storage before this control-plane provisioning sequence.
Publish the checkout reporting policy from the exact locked runtime bundle using
[policy publication](policy-publication.md).
Removing Docker containers or volumes removes this entire local environment;
recreate these prerequisites before deploying the control plane.

## Private image access

The control-plane package is private. Create a personal access token (classic)
for `stanimirivanov` with `read:packages` package access. You can use the same
token value stored in the GitHub Actions `PERFENG_REPOSITORIES_READ_TOKEN`
secret, provided it has that scope. GitHub does not expose repository secrets to
your local shell, so set the value explicitly before running the command:

```powershell
$env:PERFENG_REPOSITORIES_READ_TOKEN = Read-Host "GHCR package-read token" -MaskInput
```

This sets the token only for the current PowerShell session. Then run:

```sh
uv run --locked python scripts/control_plane_registry.py deploy
uv run --locked python scripts/control_plane_registry.py deploy --execute
uv run --locked python scripts/control_plane_registry.py health --execute
```

The preview is offline and does not read credentials. Execution reads
`PERFENG_REPOSITORIES_READ_TOKEN`; if it is unset, execution falls back to a
hidden prompt and explicitly says that typed or pasted characters will not be
displayed. It exchanges the token directly with GHCR in memory and verifies
access to the exact locked manifest digest before creating the immutable
`perfeng-control-plane-registry` pull Secret in `perf-platform`. The credential
is sent to Kubernetes through private stdin; it is never put in arguments, a
local file, Helm values, release history or terminal output.

Remove the token from the current shell after deployment:

```powershell
Remove-Item Env:PERFENG_REPOSITORIES_READ_TOKEN
```

An existing Secret must have the exact owned shape and remains subject to a live
locked-image access check. It is reused without prompting or rotation. Missing
credentials during `health`, foreign/mutable credentials, authentication
failure and digest mismatch all fail closed. Replace or rotate this immutable
Secret only through an explicit recovery procedure.

GitHub documents that private Container registry downloads require a personal
access token (classic) with `read:packages`; the contents-only repository token
used by CI is a separate credential. See [Working with the Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## Database identities

The identity command requires the GitOps-managed PostgreSQL service and its
external bootstrap Secret. Bootstrap and verify that foundation first, as
described in [local PostgreSQL](local-postgres.md):

```sh
uv run --locked python scripts/postgres.py health --execute
```

Then provision and verify the two control-plane database identities:

```sh
uv run --locked python scripts/control_plane_database.py deploy
uv run --locked python scripts/control_plane_database.py deploy --execute
uv run --locked python scripts/control_plane_database.py health --execute
```

The environment creates distinct `perfeng_migration` and `perfeng_runtime`
logins. Neither is a superuser and neither can create databases or roles. Only
the migration login receives `CREATE` on the local `perfeng` database; public
database creation is revoked. Their separate URLs are stored in the immutable
`perfeng-control-plane-database` Secret and never written to arguments, files,
Helm values or output. Secret creation precedes role creation so an interrupted
first deployment is recoverable without inventing replacement credentials.
Existing credentials are validated and are never rotated implicitly.

## Schema migration and runtime grants

After database identities and registry access pass health checks, run the pinned
control-plane migration image and establish the runtime grants:

```sh
uv run --locked python scripts/control_plane_maintenance.py pause --execute
uv run --locked python scripts/control_plane_migration.py deploy
uv run --locked python scripts/control_plane_migration.py deploy --execute
uv run --locked python scripts/control_plane_migration.py health --execute
```

The retained Job is bound to the locked image digest and source revision and can
only read the `migration-url` Secret key. It runs without a service-account token
and with a restricted security context. A repeated deploy adopts only an exact
matching successful Job; it never replaces a failed or foreign Job implicitly.
After migration, the environment revokes public/runtime defaults and grants the
runtime login only the table operations required by the API and trusted worker.
Health verifies the Job and every required/forbidden privilege without writing.

Migration deploy refuses until both root/child Applications are paused, the owned
control-plane Deployment is stopped and no matching Pods remain. The runtime
reservation grant is SELECT/INSERT/UPDATE, never DELETE. The pinned image also
contains native schema history; this selection does not enable native acquisition
or its credentials/grants.

## Runtime inputs

After migration health passes, assemble the external inputs required by the
control-plane chart:

```sh
uv run --locked python scripts/control_plane_runtime.py validate
uv run --locked python scripts/control_plane_runtime.py deploy
uv run --locked python scripts/control_plane_runtime.py deploy --execute
uv run --locked python scripts/control_plane_runtime.py health --execute
```

This creates immutable runtime configuration, runtime credentials, static API
authentication and reviewed-registry resources. Registry bytes are read from the
exact locked `perfeng-k8s` commit, not its working tree. Database and restricted
S3 credentials are copied from their validated environment-owned Secrets. The
mounted authentication document contains only a SHA-256 token digest; the raw
local-operator token is retained as a separate, non-mounted Secret key for the
later smoke client. Existing resources must match every owned byte and are never
updated or rotated implicitly. Their creation-revision annotation remains
historical provenance when a later chart-only deployment commit selects the
same runtime bytes; advancing the chart does not rotate credentials.

The two non-secret ConfigMap names end with the first 12 hexadecimal characters
of the SHA-256 digest of their canonical JSON data. The deployment lock records
those derived names, and `validate` fails when either name differs from the
reviewed runtime or registry bytes. Runtime and authentication Secrets retain
stable names because credential rotation is a separate explicit operation.

When a reviewed change produces new runtime bytes, deploy the new runtime inputs
before merging the Application reference. The new immutable ConfigMaps coexist
with the current selection, so pre-staging does not affect the running workload.
For the current migration-bearing selection, automatic child sync is disabled;
after preparation and merge, resume the root then manually sync the child as
described in the [cutover guide](kubernetes-runtime-deployment.md).
Old ConfigMaps are deliberately retained while pruning is disabled;
they support rollback and require a separate reviewed retention policy before
cleanup.

## Service deployment and operational acceptance

After all immutable runtime inputs pass health checks, use the control-plane
child Application described in [the local GitOps guide](local-gitops.md). The
Application renders the chart from the exact locked `perfeng-k8s` commit and
supplies only non-secret resource names and the immutable image identity.
Credentials remain in Kubernetes Secrets and do not enter Application values.

After preparation, merge and explicit manual activation, verify reconciliation
and the live workload. The separate benchmark authority and execution pull
Secret are also required; see [the cutover guide](kubernetes-runtime-deployment.md).

```sh
uv run --locked python scripts/gitops.py validate
uv run --locked python scripts/gitops.py health --execute
```

Acceptance requires the root and control-plane Applications to be `Synced` and
`Healthy`, one fully available Deployment, and one ready Pod using the locked
image reference, exact runtime resource mounts and dedicated control-plane node.
It also validates the ClusterIP Service and requests `/livez`, `/readyz` and
`/metrics` through the authenticated Kubernetes API service proxy without
exposing a host port. Health is read-only and does not repair or replace a
divergent release.

Run the focused workload check when diagnosing the control plane independently
of the other GitOps-managed workloads:

```sh
uv run --locked python scripts/control_plane_health.py
uv run --locked python scripts/control_plane_health.py --execute
```

The check validates prerequisite identities, execution pull access, static benchmark
authority, live Kubernetes resources and operational endpoints without Helm history.
Do not revert to a pre-cleanup worker after migration 0018. Pause and review forward
repair or a coordinated backup recovery; a Git revert alone cannot undo schema
semantics. Do not create a competing Helm owner.

## Authenticated k6-to-analysis smoke run

After deployment health passes, submit one complete checkout smoke Run:

```sh
uv run --locked python scripts/control_plane_smoke.py
uv run --locked python scripts/control_plane_smoke.py --execute
```

The command validates the control plane and sample API without repairing them,
then creates a restricted client Pod. The raw operator token is supplied only by
Secret reference and never enters Pod arguments, files or output. The submitted
request is assembled from the exact locked registry and policy bytes and binds
the sample API to clean tracked source at the current environment commit.

The client follows the Run through execution, raw collection, normalization and
reporting. Success requires `COMPLETED` and the exact five registered raw, k6,
normalized and report artifact formats. An optional tool exit code is retained
as diagnostic data and does not replace the lifecycle verdict. The client Pod,
execution Job, analysis Job, reporting Job, database record and S3 objects are
retained for diagnosis. A retry creates a new Run and does not overwrite prior
evidence.

Use the printed Run ID to prove that API metadata and artifact registrations
survive a control-plane restart:

```sh
uv run --locked python scripts/control_plane_smoke.py --verify-run <run-id>
uv run --locked python scripts/control_plane_smoke.py --verify-run <run-id> --execute
```

The executable form first validates the deployment, deletes its current Pod
without changing desired Deployment state, validates the controller-created
replacement, then retrieves the selected completed Run and its exact artifact
set through a new restricted client Pod. PostgreSQL and SeaweedFS are not
restarted by this check. Avoiding a temporary replica change keeps this
acceptance compatible with GitOps self-healing.

Changing an image, source revision, chart, bundle or contracts version requires
reviewing a new lock change. Never replace a digest with a tag or a Git commit
with a branch. The source revision records provenance; deployment must also
verify the pulled image label before accepting a promotion.
