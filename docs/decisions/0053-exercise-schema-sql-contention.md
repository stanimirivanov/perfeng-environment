# ADR-0053: Prove schema-parent SQL contention and fresh execution

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-29
- Milestone: M06 — Production Readiness
- Extends: [ADR 0052](0052-exercise-complete-schema-first-install.md)

## TL;DR

- Hold the selected migrator's exact advisory lock in one owned, bounded SQL session.
- Run the unchanged complete parent before first installation in the existing initial leg.
- Require exact backend/lock/Pod attribution and native failure without schema or retained-state changes.
- Only proved failure permits normal disconnect of the owned holder; prove cleanup before fresh installation.
- Retain the failed Job/Workflow alongside successful install/repeat evidence before recovery.
- Keep heavy checks on CI and live proof post-merge; partial writes and ambiguous commits remain open.

## Decision and authorization

The owner approved bounded SQL lock contention and fresh execution. Extend only
the existing trusted-main initial job, after approved first-install setup and
before its successful parent. Use the same pinned request, templates, migration
identity and image. No production definition, role, credential, ledger, database
reset, CI job/scenario/ref/right/budget or local infrastructure change is added.

The [selected migrator](https://github.com/stanimirivanov/perfeng-control-plane/blob/2a74a20f69773fd9d43072e41668087612cdbf9e/internal/postgres/migrate.go)
acquires transaction advisory lock `70657266656` before any schema DDL and uses
the selected source's 15-second database-operation timeout. Hold that key with
one session-level fixture lock using the existing owned PostgreSQL Pod/admin
transport. Session locks end at disconnect; the database key and granted/waiting
rows are independently observable in [pg_locks](https://www.postgresql.org/docs/17/view-pg-locks.html)
and [advisory-lock functions](https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS).
No server timeout or persisted SQL setting is changed.

## Owned holder and database observations

Use a private FIFO under the existing umask and a bounded 1260-second kubectl
exec/psql process. Its non-secret application name is bound to the cluster UID.
Send only the fixed `SELECT pg_advisory_lock(...)`; keep the session idle while
holding it. No GitHub credential, new token or API-token recipient is involved.
Capture its backend PID/start time, user/database/application and exact granted
64-bit key. Allow only absent/starting owned-session observations while waiting
for acquisition; unexpected holders, keys, identities or malformed data stop.

A bounded read-only observer starts before submission and captures complete
catalog samples at one-second intervals until the parent stops. Require the actual selected migration
backend, matching the native Pod IP, migration role/application, exact
`pg_advisory_xact_lock` query, Lock/advisory wait and sole blocking holder PID.
Bind backend start and observation to Job/Pod execution, preserve database OID
and holder PID/start identity, and require at least one wait before Pod exit.
Later observations may still show that same failed backend waiting after the
client exits; they cannot substitute for a pre-exit wait observation.

## Failure and release gate

Use the shared unchanged-parent/native Job verifier. Require real successful
preflight, selection, intent and fresh quiescence before only the migrate worker
fails. Require the declared Job/Pod security, image, fixed command and credentials,
one failed Pod with exit 1/Error and Job BackoffLimitExceeded, not a native Job
deadline. Require the fixed connection/migration/failure messages and no success
message. Generic failed Jobs, wrong backend/Pod/key/identity, startup/transport
errors and missing evidence cannot qualify.

While still holding the lock, prove absent schema/zero application relations,
unchanged roles/password hashes, credentials/config, foundation/workload/storage
identities, cluster UID and installed template/policy specs/UIDs. No successful
parent output may exist. A fresh catalog observation must still prove the exact
idle holder after terminal parent failure. Only then send `\q` through its owned
FIFO and require normal process exit. Never call backend termination/cancellation
functions, release arbitrary SQL sessions, repair on EXIT or retry nodes.

Read-only cleanup observations may wait only for those same known backend
identities. Require holder, waiter and exact-key locks to disappear within the
bounded gate, with the schema still absent, before the existing fresh unchanged
first-install parent runs. Failed proof stops for existing owned disposable
cleanup; expiration of the holder's safety deadline cannot qualify as a normal
verified release.

## Retention, verification and limits

Preserve the original failed native Job/Workflow and recompute its failure and
release evidence at final retention. The paused baseline now contains exactly
the failed contention Job and successful install Job. Refusal cases add zero;
successful repeats add one each. Match failed-job identity/spec/provenance and
terminal failure separately from successful-job retention. Require four native
Jobs and six parent Workflows overall before existing checked recovery.

Raw SQL/session/IP/log/credential observations remain private and bounded.
Shared artifacts contain fixed startup projections and validated outcome records.
Offline tests exercise positive/negative attribution, PID reuse, session cleanup,
failed/successful Job retention and actual shell release gates. Linux PR CI also
tests the real FIFO with an inert transport and isolated owned process group;
Windows skips that Linux-only transport test. Full heavy checks stay on PR CI;
live PostgreSQL/parent proof remains trusted-main post-merge, without a local run.

Rollback removes this fault phase and restores the previous successful-only
installation/paused baseline. No deployment script is retired or new production
Python added. This proves contention failure before DDL and fresh execution;
partial writes, ambiguous commit outcomes, retained-release upgrades/admission/
recovery, production multi-user security and operational cutover remain open.
