# ADR-0070: Refuse complete preparation when migration Job creation is denied

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-30
- Milestone: M06 — Production Readiness
- Extends: [ADR 0069](0069-exercise-complete-database-preparation.md), deliberate fault scope

## TL;DR

- Add one failed complete parent before existing first/repeat preparation runs in trusted-main initial only.
- Narrow only Jobs create on perf-platform/release-runtime-grants; preserve get/watch and observation rights.
- Require actual migration admission/quiescence success and exact native Forbidden, with no SQL Job/Pod or later phase.
- Only verified refusal and retained state permit guarded restoration, effective-permission checks and fresh runs.
- Keep failed execution and restored-authority proof through final retention; preserve earlier denial evidence separately.
- Add no SQL Jobs, CI jobs/scenarios/inputs/rights/budget, production changes, local execution or recovery operations.

## Decision and fault window

The owner approved a bounded Job-create denial against the unchanged complete
database-preparation parent. Reuse the existing trusted-main initial adopted/
paused window and pinned upgrade request, after the existing null-current case
and before first/repeat. Change only the fixture grants Role's Jobs verbs from
create/get/watch to get/watch. The same caller runs all phases, so denial must
occur at the first migration Job create, before qualification or grants execute.

Reuse the existing Role guard, not a second patch implementation. Capture the
reviewed Role, require its source rules/identity and produce JSON Patch tests for
UID, resourceVersion and complete rules before replacing only rules. Poll effective
create/get/watch using the same full ServiceAccount impersonation groups: create
must be denied while get/watch remain allowed. Wrong transport, warnings or
inspection errors cannot qualify as the intended boundary.

Use a distinct preparation-denied prefix for Role snapshots/patches, effective-
permission diagnostics and failed execution. Do not overwrite the earlier
grant-denied or schema-parent-denied evidence. The existing access helper keeps
its original default prefix and accepts only the two reviewed grant prefixes.

## Exact failure and retained state

Run real preflight, selection, intent, candidate/pointer/receipt/accepted-byte
admission, fresh quiescence, receipt stability and final identity checks. Require
the native resource worker's main exit 1 and exact Jobs-create Forbidden for
system:serviceaccount:argo-workflows:release-runtime-grants, the planned migration
Job name, batch API group and perf-platform namespace. Bind the private diagnostic
to that failed worker's actual node annotation, Pod UID, account and main exit.
Timeout, image failure, generic Failed/Error or another identity's log is not proof.

Require the failed complete parent's raw nested boundary/order proof, no later
phase workers, no named SQL Jobs/Pods, unchanged whole native inventory and no
successful preparation output. Preserve schema ledger/checksums/timestamps/OIDs,
ACLs, rows/sentinel, roles/passwords, receipt/pointer, Secrets/config, SQL/S3 data
and workload/storage identities. Validate all source/authority identities; only
the exact guarded create-only Role narrowing is normalized in memory for the
source identity comparison. Preserve the raw narrowed authority snapshot.

## Restoration and final gates

Only successful refusal/retention verification permits restoration planning.
The evidence CLI rechecks the denial before generating a restore patch; a saved
success filename alone is insufficient. Capture the current Role again, require
the original UID/metadata and exactly narrowed rules, then test current UID,
resourceVersion and rules before restoring the captured reviewed rules. A stale
patch or changed Role stops. No EXIT repair, fallback or unconditional restoration.

Require effective create/get/watch restoration and the actual returned Role
before existing fresh first/repeat runs. Keep those successful cases and all
standalone coverage unchanged. Before recovery/final publication, reread the
failed complete Workflow/workers and named Job/Pod absence, verify the earlier
denial/state evidence and current Role/effective rights, then run existing
successful-parent/probe/source/state/inventory retention. The denial adds no Job
to the six-Job cohort and changes no legacy inventory exclusion rule.

Failure at any boundary blocks later runs, probe, recovery or final success.
Owned disposable-cluster cleanup remains unchanged. No SQL mutation is attempted
by the denied native operation; do not generalize this proof to partial writes,
timeouts or ambiguous commits after a successful Job create.

## Verification and limits

Focused offline tests cover refusal identity/diagnostic binding, altered authority/
state, replaced Role/worker, exact guard patches, refusal-gated restore planning,
historical evidence isolation, final failed-parent/restored-authority retention
and actual Bash stop paths. Preserve standalone schema/grant defaults and their
refusal/access regressions. Heavy checks stay on PR CI; complete live controller/
API proof runs post-merge under the existing trusted-main guard.

No new SQL Jobs, GitHub jobs/scenarios/refs/requests, credentials, rights, budget,
production templates/Python, Pod replacements, recovery or local execution.
Partial SQL writes, ambiguous commits, general upgrades, operator exclusion,
backup, non-local/multi-user safety, cutover and host-script retirement remain
separate. Implementing CI wiring does not claim that live proof has already passed.
