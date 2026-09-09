# Publish a reviewed reporting policy locally

Reporting Jobs fetch the exact policy selected by the control plane from
`policies/<id>/<version>/performance-policy.json`. The source bytes remain owned
by a reviewed `perfeng-k8s` runtime bundle. This repository only provides the
local publication mechanism; it does not copy, edit, approve or select policies.

Preview the bundle-owned policy first:

```powershell
uv run --locked python scripts/policy_publish.py `
  ../perfeng-k8s/bundles/local-kind-windows/1.0.0 `
  --policy-id pod-startup-observe `
  --version 1.0.0 `
  --receipt .local/pod-startup-policy-publication.json
```

The preview resolves the policy only through `registry.json`, rejects unsafe or
ambiguous paths, and prints its exact byte count, destination and SHA-256. It
does not inspect Docker or Kubernetes, create a receipt, or contact storage.
Validate the complete bundle using the pinned control-plane validator described
in `perfeng-k8s/CONTRIBUTING.md` before accepting that digest.

For the current local bundle, the reviewed policy SHA-256 is
`1e76a39cab06eead6940dc82946b2ec6446d8345c4561cd2393e20b4dcb74b34`.
Publish only after comparing that value with both the preview and the reviewed
bundle change:

```powershell
uv run --locked python scripts/policy_publish.py `
  ../perfeng-k8s/bundles/local-kind-windows/1.0.0 `
  --policy-id pod-startup-observe `
  --version 1.0.0 `
  --expected-sha256 1e76a39cab06eead6940dc82946b2ec6446d8345c4561cd2393e20b4dcb74b34 `
  --receipt .local/pod-startup-policy-publication.json `
  --execute
```

Execution requires the matching digest. It reserves the receipt path before
cluster access, verifies the dedicated local kind context and existing object
store, then starts a bounded transfer Pod using the bootstrap storage Secret.
That identity is used because the runtime transfer identity intentionally cannot
write policy objects. The policy is sent through private stdin rather than a
command argument, ConfigMap, host mount or Helm value.

The upload uses a conditional create and fails when the key already exists; it
never replaces or deletes a policy. It reads the stored object back and verifies
the exact bytes, length, content type, policy identity/version, digest and format
metadata before writing the receipt. On failure, no receipt is left behind and a
created Pod is retained for inspection. On success, failure to remove the Pod is
reported without invalidating the already verified publication.

The receipt binds the exact policy object to the contracts version, bundle policy
path and SHA-256 of the bundle manifest bytes. It is local evidence, not a policy
approval record or a replacement for the reviewed runtime bundle. Reformatting a
policy changes its digest and requires a new version/review decision; the stable
object key cannot be overwritten.
