# ADR-0049: Stage a pinned schema-migration parent

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-28
- Milestone: M06 — Production Readiness
- Extends: [ADR 0014](0014-separate-release-and-run-orchestration.md)

## TL;DR

- Stage preflight, exact lock, reviewed migration intent, fresh quiescence and native Job execution.
- Pin the current released migrator through 0018; use existing tools with no new production Python.
- Separate migration authority from read-only and maintenance identities.
- Retain Jobs/evidence and stop on ambiguous or failed operations without automatic recovery.
- Keep active-root/host paths unchanged; live acceptance and retirement remain deferred.

## Decision and authorization

The owner approved the staged migration parent and its separate SQL/Job authority
on 2026-10-28. `perfeng-release-schema-migration-v1` remains outside the active
local root. This PR authorizes implementation and offline/PR-CI verification,
not a local or disposable schema mutation. Live acceptance needs a separately
reviewed execution path before operational cutover or host-script retirement.

The parent accepts the same four pinned Git inputs. Reuse v3 preflight and exact
lock selection checks against the current raw deployment-lock digest. The native
Job pins the same released image; changing that selection requires a reviewed
new workflow version rather than an arbitrary image parameter.

An immutable ConfigMap contains a small JSON Schema permitting manual,
forward-only migration ID `0018`, the highest migration in the selected binary.
Fetch the reviewed request at its pinned commit, recheck its exact SHA-256 bytes,
and validate this intent using the already pinned JSON Schema image. Reject
`none`, automated activation, missing fields and other IDs. This adds no Python
module or handwritten semantic predicate. It uses the existing Git credential.

After these gates, call the unchanged read-only quiescence verifier with the
same preflight result, then create/wait for the fixed native Job while holding
the shared release mutex. All steps are sequential and stop on failure, without
workflow retries or failure-tolerant continuation. Parent deadline is 1,200
seconds; intent is bounded to 60 seconds, quiescence retains its 180-second bound,
and the Job worker is bounded to 240 seconds with a 180-second Job deadline and
backoffLimit zero. Native controller behavior is not an exactly-once guarantee.

## Authority and persisted-state boundary

`release-schema-migration` has the existing checkpoint's named Application and
Deployment reads, Pod list, named kube-system Namespace read, named Git Secret
read and Argo task-result create/patch. Add only create/get/watch Jobs in
`perf-platform`. Install its named Application-read Role/Binding explicitly in
bootstrap; argocd remains outside the staged Project destinations. Do not
broaden existing identities or grant workload/ledger mutation, Secret writes,
Job deletion, exec or runtime SQL grant authority.

RBAC cannot constrain Job bodies, images or Secret mounts. A principal allowed
to submit arbitrary Workflows under this identity can create different Jobs and
mount namespace Secrets. Fixed templates are not admission or multi-user
security. This is trusted-operator local-pilot authority; it is not ready for
untrusted submission or non-local deployment.

The Job runs only `/usr/local/bin/migrate`, mounts the fixed database
`migration-url` and pull Secret, uses a non-root hardened container with bounded
resources, and disables Pod API-token mounting. Retain current control-plane
node placement/toleration. It has a deterministic name containing Workflow UID,
request/selection annotations, no TTL or Workflow owner reference, and no
cleanup hook. Successful parent outputs contain the fresh checkpoint and Job
name only. Job Complete does not establish runtime grants, schema parity,
activation, adoption or promotion.

## Operator admission, failure and recovery

Explicit trusted-operator submission is required. Before any future authorized
execution, schedule downtime, stop submissions, drain Runs, retain a suitable
backup and exclude host/manual/controller competitors. A checkpoint is an
observation, not a lease, backup/drain proof or SQL authorization. The mutex
coordinates only cooperating Workflows. This staged parent does not inspect the
current-release ledger or implement expectedCurrent/promotion admission;
retained-environment upgrades need that separate admission/recovery review.

No pause/resume, manual sync, runtime grants, pointer write, backup/restore or
rollback is performed. Keep the supported host path and avoid competing
orchestrators. On failure or response loss, retain the named Job and inspect
its UID/spec/provenance and SQL migration ledger privately. Do not retry nodes,
recreate records, delete Jobs or infer that a failed response means no SQL
commit. A separately authorized fresh submission requires fresh gates and a
review of the retained migration state. Git rollback cannot reverse SQL changes.
Removing Git selection does not withdraw installed RBAC with pruning disabled;
withdrawal requires explicit owner authorization, retaining evidence and data.

## Verification and remaining gates

Focused schema, byte-hash/refusal, exact-lock, composition, native Job and RBAC
checks plus offline Kustomize/Project validation run locally. Heavy full suites,
pinned Argo lint/render and cross-platform checks run on PR CI. Existing CI-only
migration parity is unchanged and does not prove this new complete parent.
Future live acceptance must cover first/repeat schema identity, failure/ambiguous
create, denied authority, data/Secret retention and fresh quiescence before any
supported use. Production verifiers remain two modules/708 lines; all original
host deployment entrypoints remain and no retirement credit is claimed.
