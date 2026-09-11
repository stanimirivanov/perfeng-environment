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
for `stanimirivanov` with only `read:packages` package access, then run:

```sh
uv run --locked python scripts/control_plane_registry.py deploy
uv run --locked python scripts/control_plane_registry.py deploy --execute
uv run --locked python scripts/control_plane_registry.py health --execute
```

The preview is offline and does not prompt. Execution reads the token through a
hidden prompt, exchanges it directly with GHCR in memory and verifies access to
the exact locked manifest digest before creating the immutable
`perfeng-control-plane-registry` pull Secret in `perf-platform`. The credential
is sent to Kubernetes through private stdin; it is never put in arguments, a
local file, Helm values, release history or terminal output.

An existing Secret must have the exact owned shape and remains subject to a live
locked-image access check. It is reused without prompting or rotation. Missing
credentials during `health`, foreign/mutable credentials, authentication
failure and digest mismatch all fail closed. Replace or rotate this immutable
Secret only through an explicit recovery procedure.

GitHub documents that private Container registry downloads require a personal
access token (classic) with `read:packages`; the contents-only repository token
used by CI is a separate credential. See [Working with the Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

Database identity provisioning, migrations, service deployment, live endpoint
checks and an API-to-analysis smoke run remain subsequent steps.

Changing an image, source revision, chart, bundle or contracts version requires
reviewing a new lock change. Never replace a digest with a tag or a Git commit
with a branch. The source revision records provenance; deployment must also
verify the pulled image label before accepting a promotion.
