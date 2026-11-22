# ADR-0061: Exercise owner grants on the real adopted, paused application

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-29
- Milestone: M06 — Production Readiness
- Extends: [ADR 0060](0060-exercise-owner-grant-correction.md)

## TL;DR

- Add one unchanged grant parent after adopted schema-migration acceptance and before existing checked recovery.
- Reuse the pinned forward-only upgrade request; bind expectedCurrent to the accepted receipt/pointer in CI.
- Require real ordered gates, a distinct native Job, runtime TCP success and unchanged ACLs/data/identities.
- Retain the new parent/Job and probe; final retention must require the adopted phase explicitly.
- Keep heavy checks on PR CI and live proof post-merge; no new recovery, fault or production admission.

## Decision and execution scope

The owner approved extending grant acceptance beyond bootstrap placeholders.
In the existing trusted-main initial job, after successful schema-parent
acceptance on the real adopted/paused application, submit one fresh unchanged
runtime-grants parent. Use the existing forward-only/0018 upgrade request and
credentials. Keep the service paused until all grant and probe gates pass; only
then may the existing independently approved manual recovery run.

Before submission, require the current pointer's digest to equal expectedCurrent
and the hash of the accepted raw request. Verify receipt name/UID, immutable
request bytes and receipt digest/environment/selection/root/child identity. The
candidate may differ from that accepted request only by the reviewed promotion
fields. Require the actual server image and paused real Deployment, excluding
the bootstrap placeholder. These are CI acceptance preconditions; production
templates gain no new upgrade-admission or ledger-read authority.

## Native proof and state preservation

Reuse the existing native grant runner with explicit adopted parameters and
separate adopted-state validation. Require real preflight, exact selection,
intent and fresh quiescence in order, the fixed owner-only Job and its unchanged
source identities. Follow with the existing runtime-only TCP probe, checking
its published wrapper/Job declaration, actual success/exit and SQL-policy marker.

Reuse the maintenance/schema snapshots and independently compare their stable
PostgreSQL/S3 files against the paused baseline. Require unchanged ACL bytes,
schema ledger/checksums/timestamps/OIDs, rows/sequences/sentinel, roles/passwords,
Secrets, Git/config, ledger provenance, SQL/S3 data and workload/storage identities.
No server/foundation Pod replacement, activation, data repair or additional
recovery is permitted. Any failed boundary stops before recovery/baseline advance.
Keep raw resource/data/credential/probe evidence private.

## Retention and verification

Bootstrap grant retention remains strict at five native Jobs/six parent
Workflows. Final retention explicitly requires six Jobs/seven parent Workflows,
the extra runtime probe and unchanged source versions. Missing adopted evidence
must fail, rather than being skipped based on file presence. Re-read probe and
parent/Job identities and replay the historic paused acceptance before publishing
the bounded adopted outcome; recovery's existing Pod-replacement rules stay intact.
The adopted suite also re-reads parent/Job and probe identities before it permits recovery.

Focused offline tests cover candidate/current/receipt mismatches, pause and
persistent-state drift, exact native/probe execution, strict phase counts and
actual shell failure/continuation paths. Heavy full checks stay on PR CI; live
proof remains post-merge under the existing main-only guard.

No new fault, CI jobs/scenarios/refs/credentials/rights/budget, production change
or local execution. Earlier bootstrap/fault gates and the other five scenarios
remain unchanged. Host commands and the two-module/708-line production verifier
budget are unchanged. Ambiguous commits, production admission, operational
cutover and measured retirement remain separate.
