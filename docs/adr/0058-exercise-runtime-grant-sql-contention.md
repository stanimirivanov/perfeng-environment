# ADR-0058: Prove runtime-grant SQL contention and fresh execution

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-29
- Milestone: M06 — Production Readiness
- Extends: [ADR 0057](0057-refuse-runtime-grant-job-create.md)

## TL;DR

- Hold the published advisory key in one owned bounded SQL session before first grants.
- Submit the unchanged owner parent after verified permission restoration in the existing initial job.
- Require exact backend/Pod/key/blocker attribution, server lock timeout and native failure without ACL/state drift.
- Permit only normal holder disconnect after proof; require both sessions/locks gone before fresh first/repeat parents.
- Retain the failed Job/Workflow through final gates; heavy checks stay on CI and live proof remains post-merge.

## Decision and fixture scope

The owner approved this bounded SQL fault. Reuse the installed schema, pinned
request, owner-only parent/Job, migration-url and approved authorities. Keep the
existing Job-create denial and restore its permissions first. Then one fixture
psql session holds advisory key 70657266656 in perfeng, using the existing owned
PostgreSQL Pod/admin transport and a cluster-UID-bound application name.

A private FIFO feeds only the fixed lock query into a bounded 1260-second
kubectl/psql process. Wait for exact granted ownership before submitting the
unchanged complete grant parent. A bounded read-only observer captures catalog
samples until parent completion. No SQL/server setting changes: the published
transaction retains lock_timeout=5s and statement_timeout=15s.

## Causal refusal and retention

Require actual successful preflight, exact selection, intent and fresh quiescence,
then only the native grant worker fails. The fixed Job must have one failed Pod,
exit 1/Error and BackoffLimitExceeded, without successful parent output. Deadline,
image/connection failures and generic failed Jobs cannot qualify.

Tie the owner-role backend to the grant Pod IP and container lifetime, the exact
pg_advisory_xact_lock statement/key and the sole owned blocker PID. Require at
least one actual advisory wait before the server error and Pod exit. Preserve
holder/backend start identities and database OID; unfamiliar sessions, PID reuse,
keys or blockers stop acceptance. The existing PostgreSQL log prefix identifies
the user/database/application. Its bounded timestamped log must contain exactly
one matching lock-timeout error and statement during this Pod's lifetime. Keep
raw catalog/server/worker logs private; do not infer the cause from worker failure.

While still holding the lock, replay complete-parent/native evidence and prove
unchanged ACL bytes, schema ledger/checksums/timestamps/OIDs, rows/sentinel,
roles/passwords, Secrets and paused foundation/workload/storage identities. Job
inventory may grow by exactly this one failed Job. No successful grant is inferred.

## Release, fresh execution and exclusions

Only verified refusal and retention permit normal quit/disconnect of the owned
holder. Never signal a database backend, release on EXIT, repair permissions or
advance the baseline. Bound cleanup polling to known original sessions; require
both holder and grant backend plus all key locks gone before the existing fresh
first/repeat parents. Failed proof stops without continuation or automatic repair.

Retain three Jobs (one failed, two successful) and four Workflows (permission
denied, SQL contention, first, repeat). Phase/final retention revalidates their
identities/declarations, source versions, restored authority, repeat ACLs and
fresh absence of contention sessions/locks before bounded outcomes are published.

Focused offline evidence, shell gating and FIFO transport tests verify the fault;
Linux FIFO proof and heavy full checks run on PR CI. Full-parent live proof stays
post-merge main-only. No new CI jobs/scenarios/refs/credentials/rights/budget,
production/settings changes or local execution. Other scenarios and the existing
DELETE fault/correction remain unchanged. Partial writes, ambiguous commits,
admission, cutover and retirement stay open; the verifier budget remains two modules/708 lines.
