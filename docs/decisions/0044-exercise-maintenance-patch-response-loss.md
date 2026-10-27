# ADR-0044: Exercise committed maintenance Deployment PATCH response loss

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-27
- Milestone: M06 — Production Readiness
- Extends: [ADR 0043](0043-exercise-maintenance-write-denial.md)

## TL;DR

- Commit exactly one real guarded Deployment scale-to-zero PATCH and drop its response.
- Preserve the published parent/native payload through reversible CI-only copies.
- Require failed native worker, no checkpoint, retained state and direct API inspection.
- Require a fresh unchanged checkpoint before explicit checked recovery.
- Permit one additional same-ReplicaSet Pod replacement; advance only a checked baseline.

## Decision and authorization

The owner approved Deployment PATCH response loss and its bounded recovery in the
existing trusted-main initial job. This extends ADR 0043's excluded ambiguous
maintenance response and recovery scope. Reuse the existing six scenarios, jobs,
temporary refs, credentials and 60-minute budget. Production templates, native
patches, immutable verifiers, RBAC and local infrastructure remain unchanged.
Root/child PATCH response-loss faults remain out of scope.

Run after the checked Deployment interruption/lingering-Pod recovery and before
receipt identity faults. A CI-only parent inserts one Suspend immediately after
the real Deployment guard, then redirects only its Deployment patch step to a
local native-template copy. That copy appends a dedicated kubeconfig and mounts
its read-only fixture ConfigMap. Preserve action, JSON merge strategy, flags,
full UID/resourceVersion/spec tests, manifest, arguments, authority, mutex,
workflow deadline and native worker deadline. Reversing exactly the suspension,
reference and transport changes must recover both published source definitions;
verify installed spec before submission and after failure. PR CI generates and
lints this copy with the existing pinned Argo tool, without cluster credentials.

## Scoped transport and credential handling

Add a separate PATCH proxy; preserve the existing ledger proxy's POST behavior.
Reuse its tested TLS/certificate/socket infrastructure and unprivileged pinned
Python Pod/Service shape under distinct names. The proxy has no ServiceAccount
token mount and no Git Secret, artifact credentials or runner environment. Its
only volumes are fixture configuration, ephemeral TLS Secret and API CA.
Runner GitHub credentials remain in the existing runner cleanup scope; they are
not copied into Pod configuration. This avoids losing temporary-ref cleanup
credentials on an early fault failure.

Both TLS hops verify certificates. Static discovery does not access upstream
state. The only forwarded read is the exact named Deployment, authenticated as
the maintenance ServiceAccount, before the first mutation; its UID/version/spec
must equal the planned guard. The only forwarded mutation is a JSON PATCH at that
exact route, with the exact planned payload and known kubectl query semantics.
Reject unrelated credentials (including adoption/GitHub identities), methods,
routes, payloads, duplicate JSON fields, transfer encoding, oversized bodies,
unsupported query parameters and repeat mutations. The real API authenticates
JWT signature, expiry and audience. A subject claim check alone grants nothing.
The proxy never logs authorization headers, request payloads or raw API bodies.

Mark the mutation attempted before forwarding, with a bounded upstream call and
no retry. Require HTTP 200, same Deployment UID, changed resourceVersion,
generation incremented exactly once, unchanged metadata except managedFields/
generation/resourceVersion, and full original spec except replicas zero. Only
then close the downstream connection without forwarding even its successful
status line. Emit one fixed event carrying the UID/version and patch digest,
with zero successful responses forwarded. API refusal, TLS/transport failure,
wrong committed state or handler failure never qualifies as response-loss proof.

## Evidence and recovery

Require real preflight/readiness, both retained adoption records with no creates,
successful root/child native patches and the actual Deployment guard before proxy
setup. The fresh target must still match that guard's UID/version/spec. After
resume, require the same parent UID and a sole failed native Deployment worker
(exit 64), successful earlier workers, completed barrier and no checkpoint/result.
An arbitrary failed Workflow is insufficient: the single committed-response event
must bind the exact guarded patch and direct post-failure Deployment observation.

Independently wait for observed zero replicas and matching-Pod absence using the
existing bounded raw PodList/settlement checks. Preserve original evidence,
ledger bytes/UIDs/provenance, foundation Pods/storage, SQL/S3 data, Secrets,
configuration, Git credential observations and resource identities. Require the
same paused retention policy as existing interruption acceptance. Controller
bookkeeping may refresh resourceVersions; it cannot change spec or ownership.
These observations are evidence at a point in time, not a maintenance lease.

Only verified response loss, settled paused retention and a fresh unchanged
read-only checkpoint may reach explicit manual child sync. Bind checkpoint UID,
chronology, request, cluster, workers and result through the existing checker.
Permit exactly one additional same-ReplicaSet server Pod replacement with the
existing spec/token-volume comparison. After fresh native/HTTP/root health and
retention, require unchanged adoption records/result using a candidate baseline.
Advance the caller's fixed baseline only after those checks return successfully.
Whitelist this new baseline with its validated recovery report for later receipt
refusals; preserve every earlier snapshot. Root auto-sync remains disabled and
six other children remain skipped.

No blind parent retry, retry of successful nodes, automatic resume, EXIT repair,
force deletion, ledger repair or credential change is introduced. Failed setup
or ambiguous/unproven state stops without recovery; owned disposable-cluster
teardown remains the cleanup boundary. This grants no local execution authority.

## Verification, rollout and limits

Use focused local format/lint/type, evidence regressions, shell syntax and diff
checks. Heavy TLS/real-client and cross-platform verification runs on PR CI as
requested. The full parent/controller proof remains post-merge trusted-main;
there is no manual dispatch. Publish only validated fixed barrier/outcome,
checkpoint and recovery reports. Keep raw API/Workflow/worker/proxy logs private.

Rollback removes this fixture call and restores the previous fixed baseline
whitelist, reducing coverage only. Root/child response faults, operational
cutover/recovery, multi-user security and measured script retirement remain
separate gates; this acceptance does not claim production readiness.
