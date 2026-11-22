# ADR-0009: Select the image-backed target behind the loopback gateway

- Status: Proposed
- Date: 2026-10-12
- Milestone: M03 — Native Browser Decision Loop
- Deciders: Repository owner

## TL;DR

- Pin the published candidate and reusable chart in a dedicated target lock/Application.
- Use the existing namespace and owned pull credential without copying or rotating it.
- Cut over the existing HTTPS target to an exact verified Pod through a fourth loopback session.
- Require image/provenance/content checks and never fall back to checkout files.
- Preserve host qualification, worker identity and native runtime promotion as independent gates.

## Context

The controlled fixture image and Kubernetes chart have been published/merged.
File-serving behind the gateway cannot honestly prove that the selected candidate
image was tested. The chart must now be selected declaratively and its running
identity checked before accepting browser target connectivity.

## Decision

Select the candidate provenance and `perfeng-k8s` commit in
`deployments/local-native-target.json`. Add a child Application under the existing
root, in `perf-platform`, with automatic sync, no pruning and initially no
self-healing. Keep the older control-plane image/chart/runtime selection unchanged.

Reuse `perfeng-control-plane-registry`, an owned immutable GHCR credential in the
same namespace, after explicitly checking candidate-package pull access. Retain
its identity, purpose label and bytes. This reuse adds a kubelet pull consumer,
not application credential access or a worker authentication identity. The
health path never creates, copies, prompts for or rotates that Secret.

The target forward checks exact desired state, current ready resources and the
Pod's UID controller chain, then forwards that exact Pod on loopback port 18081.
Caddy's existing port-10443 origin becomes proxy-only. Expected fixture bytes
remain pinned source/verification input, not HTTP fallback content. Verify the
registry manifest bytes and resolve OCI/Docker platform descriptors before
comparing the running image ID; published index and platform digests may differ.

## Alternatives considered

- Keep checkout-file serving: useful before publication, but not image-backed evidence.
- New target namespace and copied pull credentials: adds identity/lifecycle work
  without improving this credential-free integration fixture's boundary.
- Service-only forwarding: could select an unverified replacement or extra Pod.
- Enable self-healing immediately: deferred until live acceptance and rollback
  are reviewed; automatic initial synchronization remains declarative.
- Bundle/worker promotion now: blocked by real qualification evidence and
  intentionally excluded from target connectivity.

## Consequences

The browser route gains honest image identity without a new reconciler or trust
mechanism. It needs a fourth bounded foreground session and read-only registry
access for identity checks. Health verification is point-in-time, not a guarantee
against later Pod/target changes during a measurement. ClusterIP is not network
authorization; no network-policy enforcement or performance guarantee is implied.

## Compatibility and operations

The target HTTPS origin and content checksum remain stable. Gateway serving now
requires a deployed verified target; missing deployment/tunnel fails instead of
serving files. Existing API/S3 routes, signing endpoint, component releases,
profiles, credentials, persistent resources and native acquisition remain unchanged.

## Rollout and rollback

Merge desired state before root reconciliation, verify the candidate, restart the
gateway with the retained CA, and prove exact image-backed HTTPS content. Restore
the previous reviewed gateway/Application selection to roll back. No automatic
pruning, credentials/trust rotation, evidence replacement or baseline relabeling.
Old file-serving is pre-promotion connectivity only. Live acceptance stays explicit.

## Validation

Run `make validate`, `make release-selection-check` and real offline Caddy
adaptation. Cover provenance drift, mutable inputs, registry scope, OCI/Docker
resolution, resource/controller ownership, Pod replacement, exact content and
offline previews. After merge, run target health and trusted HTTPS checks; M03
requires the separate qualified browser decision loop.
