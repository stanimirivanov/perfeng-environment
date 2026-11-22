# ADR-0062: Stage retained-baseline admission before schema migration

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-30
- Milestone: M06 — Production Readiness
- Supersedes: [ADR 0049](0049-stage-pinned-schema-migration.md), deferred admission boundary only

## TL;DR

- Stage schema-migration v2 alongside unchanged v1 bootstrap execution.
- Bind expectedCurrent to the owned pointer, immutable receipt and exact accepted request bytes.
- Require the same fixed local selection and initial adopted baseline, then fresh quiescence and root/child UID binding.
- Reuse the native migrator Job; add only explicit ledger get authority, native predicates and bounded shell byte checks.
- Keep the active root, live execution, runtime-grants parent, promotion and host cutover unchanged.

## Decision and boundaries

The owner approved retained-baseline admission in the staged production workflow.
Earlier adopted/paused tests checked expectedCurrent only in CI. Add
`perfeng-release-schema-migration-v2` without changing published v1 or its native
Job. V1 remains the explicitly reviewed bootstrap capability; v2 does not make
v1 unavailable or prevent a trusted operator from selecting it.

V2 retains four pinned Git inputs, v3 preflight, exact local lock and v1's
manual/forward-only/0018 intent. Re-read the pinned candidate bytes, check their
digest and require a non-null lowercase SHA-256 expectedCurrent. Read the fixed
current pointer and its digest-derived receipt name. Missing reads fail; there
is no ignore-not-found, default object, retry policy or conditional bypass.

Require exact ownership labels, namespaces, nonempty UIDs/resourceVersions,
no deletion/owners/finalizers or Argo tracking, exact pointer fields and matching
cluster/digest/receipt UID. Require immutable receipt content and syntactically
valid original workflow UID/request commit provenance. Decode canonical base64,
bound accepted bytes and require their SHA-256 to equal expectedCurrent.

The accepted request must have the candidate's exact non-promotion fields, manual
initial activation and either none or forward-only/0018 migration intent. The
receipt must have the exact adoption-record fields, accepted digest/environment/
selection, selected image and valid deployment revision. This deliberately
supports reapplication on the fixed current selection, not a different release
selection, arbitrary schema upgrade or promotion-chain traversal.

After byte verification, run unchanged fresh v3 quiescence. Re-read the receipt
and require unchanged content/UID/provenance, ignoring only resourceVersion and
managedFields. Re-read the pointer, require unchanged UID/data and ownership,
and bind receipt root/child UIDs to the fresh checkpoint. Only then invoke the
unchanged native migrator. Success still returns its Job name and checkpoint;
it does not advance a ledger record or attest schema checksums.

## Authority, concurrency and failure

Add a standalone schema-migration ledger Role/Binding beside its existing
explicit Application-read bootstrap. It grants only ConfigMap get in the ledger
namespace, without list/create/patch/delete. RBAC cannot restrict dynamic receipt
names to a prefix, so it permits get on any ConfigMap there. Installing it also
extends the existing identity used by v1; templates are trusted-operator policy,
not an admission controller or a multi-user security boundary.

Do not select this bootstrap grant in the active root or install it implicitly.
No live execution, GitHub authorization change, credential, CI scenario, Job
instance or budget increase is included. The v2 parent deadline is 1,800 seconds;
native ledger reads and byte checks are each bounded to 60 seconds, with ten-second
API request timeouts. This changes no CI job budget.

Native jq predicates run within Argo resource reads. The existing digest-pinned
validator image handles candidate validation and exact byte hashing in bounded
scratch volumes, without a new image/tool dependency, Python module or controller.
Raw candidate/ledger bytes contain no credentials under the release contract.
Failures leave maintenance in place and retain evidence; never retry nodes,
replace Jobs/records, repair credentials or resume workloads automatically.

These observations are not a transaction or lease across the ledger and SQL.
The shared mutex coordinates only cooperating Workflows; exclude other operators,
host commands and controller/manual writes, schedule downtime, drain Runs and
retain a suitable backup. A later ledger change remains possible after reads.
No ambiguous-commit or general retained-upgrade recovery is established.

## Verification, rollout and remaining work

Focused offline tests execute actual native filter text and shell byte checks,
including missing/foreign/replaced records, invalid provenance, changed bytes,
incorrect selection/cluster/root/child binding and fail-closed sequencing.
Existing bootstrap tests remain required. Heavy repository checks, rendering and
pinned Argo lint run on PR CI; live acceptance requires a separately approved slice.

Stage v2 in the release-workflows Kustomize root only. The standalone ledger
grant requires explicit installation before authorized use. Removing staged Git
selection does not remove installed RBAC/immutable objects while pruning is off;
withdrawal needs explicit authority. Supported host tasks, all 13 deployment
entrypoints and the two-module/708-line production verifier budget are unchanged.
Grant-parent admission, live proof, operational cutover, non-local security and
measured script retirement remain open.
