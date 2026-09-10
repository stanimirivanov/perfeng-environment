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
Kubernetes resources. Credential provisioning, database migration, deployment,
live endpoint checks and an API-to-analysis smoke run remain subsequent steps.

Changing an image, source revision, chart, bundle or contracts version requires
reviewing a new lock change. Never replace a digest with a tag or a Git commit
with a branch. The source revision records provenance; deployment must also
verify the pulled image label before accepting a promotion.
