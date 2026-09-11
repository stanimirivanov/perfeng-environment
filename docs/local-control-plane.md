# Local control-plane release

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
read-only `PERFENG_REPOSITORIES_READ_TOKEN` secret and performs the same
validation.

This lock is deployment input, not proof that an image is registry-addressable
or that a cluster is healthy. It contains no credentials and does not create
Kubernetes resources.

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

The identity command requires the base PostgreSQL service and its bootstrap
Secret. Deploy and verify that foundation first, as described in
[local PostgreSQL](local-postgres.md):

```sh
uv run --locked python scripts/postgres.py deploy
uv run --locked python scripts/postgres.py deploy --execute
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

## Runtime inputs

After migration health passes, assemble the external inputs required by the
control-plane chart:

```sh
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
updated or rotated implicitly.

Service deployment, live endpoint checks and an API-to-analysis smoke run remain
subsequent steps.

Changing an image, source revision, chart, bundle or contracts version requires
reviewing a new lock change. Never replace a digest with a tag or a Git commit
with a branch. The source revision records provenance; deployment must also
verify the pulled image label before accepting a promotion.
