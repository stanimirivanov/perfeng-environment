# Local image-backed browser target

## TL;DR

- Argo CD owns `perfeng-local-native-search`, using a pinned `perfeng-k8s` chart and published candidate digest.
- Reuse the owned GHCR pull Secret in `perf-platform`; do not copy, rotate or print credentials.
- After merge, verify the candidate with `task native:target-health`, then start `task native:target` in its own terminal.
- Restart the gateway to apply its proxy-only target route; the HTTPS origin remains `https://127.0.0.1:10443/`.
- `task native:check` requires both exact candidate bytes and the selected ready image/Pod; no file-serving fallback exists.
- This is target connectivity acceptance, not accepted Linux qualification, native acquisition or M03 completion.

## Selection and ownership

[The target lock](../deployments/local-native-target.json) selects:

- candidate `ghcr.io/stanimirivanov/perfeng-native-search@sha256:3f6179c9f3784be884761e7c6ceb4229c48631b8290d5cc65d0b488eef3b4386`;
- published source `753022c24bbf2f30fd3a620e0788f9b53ae16dd7` and target checksum
  `ff03e1d7a7544557a3f035218b6b3e3bcce7e434996be5dd3b895af10cbc0fb4`;
- `perfeng-k8s` revision `a20633e150db4e940aba4355a32a7931b9a7f45a`, chart `charts/native-search` version 1.0.0; and
- release `native-search` in `perf-platform`, referencing `perfeng-control-plane-registry`.

The [Application](../gitops/environments/local/native-search-application.yaml)
is included in the existing root composition. It syncs automatically without
pruning or self-healing during initial acceptance. No new namespace, source
authorization, worker identity, Secret, runtime bundle or control-plane selection
is introduced. [ADR 0009](decisions/0009-select-image-backed-native-target.md)
records the cutover and pull-identity reuse.

Before adopting this new name, inspect any existing `native-search` resources;
do not allow a foreign release to be overwritten. Argo CD remains the sole
workload reconciler. Do not run a competing Helm install or enable self-healing
until live drift/rollback behavior has been reviewed.

## Offline review and CI

Run the ordinary gate and the pinned integration tier:

```sh
make validate
make release-selection-check
uv run --locked python scripts/native_target_release.py validate
```

Pinned validation uses Git objects, not a sibling's current working files. It
verifies chart identity, the release record, exact source bytes at the publisher
revision and their checksum, then strictly lints/renders a temporary copy of
the selected chart. It never contacts a registry or cluster. CI checks out the
new chart source separately from the old control-plane selection and retains
this repository's Git history for the published fixture source.

Keep the selected Git commits available locally. Missing history is not reason
to substitute a branch or a different revision. The local checkout's expected
fixture bytes must also match the locked content fingerprint; these files are
verification expectations and build source, never a fallback HTTP server.

## After-merge deployment and read-only health

Complete the existing local GitOps prerequisites first. The root reads `main`,
so uncommitted local changes cannot deploy the Application. After review/merge:

```sh
uv run --locked python scripts/gitops.py bootstrap --execute
uv run --locked python scripts/gitops.py health --execute
task native:target-health
```

Bootstrap applies only the owned root Application; Argo CD creates the workload.
GitOps health additionally waits for the new Application. `native:target-health`
performs the stronger read-only candidate proof: selected Application source,
Synced/Healthy state at its exact revision, owned immutable pull Secret, access
to the candidate's exact GHCR manifest, current one-replica Deployment, Service,
ready Pod and Pod→ReplicaSet→Deployment UID ownership.

The GHCR token exchange requests pull scope for the candidate package, not the
control-plane package. The existing Secret name/purpose/bytes stay unchanged.
The candidate Pod never mounts that credential. If package access is missing,
review GHCR package permissions for the existing identity; no prompt, Secret
creation or automatic rotation occurs in this check. Repository-content and
worker authentication tokens remain separate.

OCI indexes and Docker schema-2 manifest lists identify a platform-specific
manifest. The check hashes the returned registry bytes against the selected
digest, resolves exactly one Linux AMD64 descriptor and compares its digest
with the running Pod's pullable image ID. A runtime may report the exact selected
index or its resolved platform manifest; either must be a canonical reference to
this repository. An unrelated child or image config ID is not accepted. It does
not compare an index digest blindly with a child manifest.
See the [OCI index specification](https://github.com/opencontainers/image-spec/blob/main/image-index.md)
and [Distribution manifest specification](https://distribution.github.io/distribution/spec/manifest-v2-2/).
Other runtime image-ID representations fail closed pending explicit adapter review.

## Start the reviewed route

Follow the [connectivity guide](native-worker-connectivity.md) for the patched
Caddy build and previously reviewed CA trust. Stop an old gateway with Ctrl+C
before restarting it; retain its CA and private state. Start these four bounded
foreground sessions in separate terminals:

```sh
task native:api
task native:s3
task native:target
task native:gateway
```

The target forward validates the Service and controller ownership, then binds
the exact verified Pod to `127.0.0.1:18081`, not a Service-selected replacement.
The gateway proxies port 10443 to that listener with no path/Host rewriting,
redirect or file fallback. No NodePort, LAN exposure or new trust-store write.
When the Pod changes, stop/restart the target forward; all sessions expire after
one hour. Do not change the target while a performance attempt is measuring it.

Run `task native:check`. It verifies selected ready target identity before and
after credential-free TLS probes, exact root/HTML/JavaScript bytes, `/healthz`,
API readiness and anonymous S3 denial. A replaced Pod or mismatched image/content
fails acceptance. This point-in-time check does not guarantee future stability.

## Failure and recovery

- Application missing: confirm merge to `main`, root reconciliation and private
  repository access; use existing GitOps diagnostics, not direct Helm deployment.
- ImagePullBackOff/GHCR denial: check package visibility/access for the owned pull
  identity. Never paste Secret contents or implicitly rotate it.
- Pending/unready: inspect safe events, Linux AMD64 capacity, `/tmp` bounds and
  the selected chart's resource/probe policy; do not weaken probes or limits.
- Wrong image, image-ID representation or owner: inspect non-secret Pod identity
  and controller UIDs. Do not whitelist whatever digest happens to be running.
- Gateway 502: verify target health and its fourth foreground forwarding session.
  A missing tunnel must not be masked by checkout files.
- Target checksum mismatch: verify the pinned release/source and upstream. Do
  not change the expected hash merely to accept an unknown response.
- Pod replaced: stop its old forward, re-run target health and start a new forward.

Rollback stops target use and restores the previous reviewed GitOps/gateway
selection together. Pruning is disabled: the candidate resources may remain,
and removing them needs an explicit ownership-aware cleanup decision. Keep CA
state, pull credentials, Runs, objects and rejected qualification evidence.
Returning to file-serving is pre-promotion connectivity only, not image-backed
acceptance. No resource deletion or trust change is part of this workflow.

## Remaining M03 gates

The target can be deployed independently of Linux qualification. The actual
laptop still requires an accepted fresh report under its explicit profile.
Native bundle promotion, the newer control-plane selection, worker credential,
signed PUT/internal read-back, browser normalization/baseline decision,
cancellation and manual recovery remain separate acceptance gates. Do not use
target readiness or these connectivity checks as proof that M03 is complete.
