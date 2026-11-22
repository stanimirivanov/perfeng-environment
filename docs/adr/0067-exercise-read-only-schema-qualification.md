# ADR-0067: Exercise read-only qualification on the adopted paused application

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-30
- Milestone: M06 — Production Readiness
- Extends: [ADR 0066](0066-stage-read-only-schema-qualification.md), live acceptance only

## TL;DR

- Exercise the unchanged qualification parent twice in trusted-main initial only.
- Run after schema acceptance and grants ledger-read setup, before adopted grants and existing recovery.
- Retain two distinct read-only native Jobs/Workflows and freshly verify their evidence before recovery and final success.
- Preserve SQL, ACLs, release records, credentials, data and workload/storage identities; failure blocks later operations.
- Add no CI jobs/scenarios, inputs/refs, credentials, authority, budget, production changes or local execution.

## Decision

The owner approved bounded complete-parent first/repeat acceptance for the staged
read-only schema qualification. Install only its three published declarations
in the existing owned initial cluster, reuse the pinned upgrade request and
release-runtime-grants ledger-read authority, and submit the unchanged parent.
No request publication, production WorkflowTemplate or SQL policy changes.

Run after schema acceptance, while the real adopted application remains paused,
and before the adopted grants parent. The existing five other matrix scenarios
are unchanged. Two additional native SQL Jobs are read-only qualification Jobs,
not new GitHub jobs or schema/grant mutations. Reuse existing deadlines and the
approved 60-minute CI budget; do not raise limits or silently skip acceptance.

## Evidence and recovery boundary

Require real ordered preflight, exact selection, intent, retained-baseline
admission, fresh quiescence and final identity binding before each fixed Job.
Verify exact native worker identity/exit, selected Job/Pod spec, owner references,
request provenance, success output and distinct retained Job/Workflow UIDs.
Check live installed template specs, immutable SQL/inventory bytes and metadata,
intent ConfigMap and ledger Role/Binding against published sources and captured
identities. Reuse shared admission and paused-state evidence predicates instead
of introducing production policy clones.

Capture the paused baseline before submission and after each run. Require the
selected schema ledger/checksums/timestamps/OIDs, ACLs, rows/sequences/sentinel,
roles/passwords, release records, Secrets/config, SQL/S3 data and workload/storage
identities to remain unchanged. The qualification Job has its own exact inventory;
missing, replaced or unexpected retained Jobs fail acceptance.

Before existing recovery, reread both parents, native workers, Jobs/Pods, sources
and authority, and independently snapshot paused state again. After existing
checked recovery and remaining acceptance, reread the same execution evidence,
sources/authority and persistent SQL/data state before final success. Final
retention allows the already approved control-plane recovery; it does not demand
that the application remain paused after recovery or authorize another Pod
replacement. Existing maintenance/adoption checks retain workload recovery proof.

Wait, source, execution, state or retention failure stops without retries,
automatic/EXIT repair, grants or recovery. Keep raw observations and diagnostics
private. Publish only bounded UID/digest/decision summaries after verification.
Removing this CI wiring does not delete retained evidence or installed versions.

## Verification and limits

Focused offline tests exercise successful first/repeat/retention evidence and
reject reordered admission, wrong worker identity, altered SQL/source/authority,
failed native execution, replacement/missing Jobs and changed ledger/ACL/data.
Actual Bash coordinator tests prove failure stops before the next case/recovery.
Shared grant evidence regressions preserve existing v1/v2 behavior. Heavy checks
remain on PR CI. PostgreSQL/controller complete-parent proof runs only after
merge under the existing trusted-main guard; implementing the wiring is not a
claim that that live proof has passed.

SQL refusal fault injection, complete DDL equivalence, preparation-flow
composition, ambiguous commit recovery, backup, concurrent-operator exclusion,
general upgrades, multi-user operation, local execution, cutover and host-script
retirement remain separate. No retirement credit or new production Python.
