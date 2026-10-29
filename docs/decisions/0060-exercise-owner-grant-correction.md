# ADR-0060: Correct the existing DELETE drift through the owner-only grant parent

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-29
- Milestone: M06 — Production Readiness
- Extends: [ADR 0059](0059-exercise-runtime-grant-rollback.md)
- Supersedes: [ADR 0056](0056-exercise-owner-runtime-grants.md)'s administrator-correction boundary for the initial scenario only

## TL;DR

- Keep the existing deliberate administrator DELETE grant and exact runtime probe refusal.
- Replace only the initial scenario's administrator correction with a fresh unchanged owner parent.
- Before submission, require exactly the added runtime DELETE on runs and no other ACL drift.
- Require real ordered gates, the fixed owner-only Job, successful runtime TCP and original ACL bytes.
- Retain the additional successful execution; other scenarios and heavy-check routing stay unchanged.

## Decision and boundary

The owner approved bounded owner-parent correction. Earlier decisions kept the
administrator correction unchanged everywhere. Supersede that exclusion only
for the existing trusted-main initial job. After its deliberate DELETE grant and
verified runtime-policy refusal, submit the unchanged complete grant parent with
the same pinned request and migration-owner credentials. Keep the other five
scenarios' administrator correction and every earlier fault/cleanup gate.

Before submission, compare the fresh ACL snapshot with first success. Only the
runtime runs entry may differ, with exactly one added DELETE bit, the same
migration-owner grantor, existing SELECT/INSERT/UPDATE and no grant options.
Remove only that bit for comparison; all schema/table entries, principals and
other flags must match the accepted snapshot. PostgreSQL documents the
[ACL encoding](https://www.postgresql.org/docs/17/ddl-priv.html) and that
[superuser grants are recorded as the object owner's grants](https://www.postgresql.org/docs/17/sql-grant.html).
Unexpected or absent drift refuses before parent submission.

## Verification and retained state

Require the actual ordered preflight, exact selection, intent and fresh quiescence
under the approved identity, then the fixed successful native owner Job. Preserve
schema ledger/checksums/timestamps/OIDs, rows/sentinel, roles/passwords, Secrets
and paused foundation/storage identities. The corrected ACL bytes must equal
first success. Keep the existing successful corrected runtime TCP probe and
phase-wide retention checks; no administrator fallback or activation on failure.

Retain five Jobs (two SQL-failed and three successful) and six parent Workflows.
Phase/final gates re-read their identities/declarations and source versions,
replay correction evidence, and compare corrected ACL bytes again with first/
repeat success. Earlier authority restoration, contention-session cleanup and
rollback-fixture absence remain mandatory. Raw resource/data/credential evidence
stays private; bounded outcomes retain only validated identifiers.

## Scope and limits

Reuse existing source/evidence functions without a new script or production
definition. Focused offline tests cover exact DELETE-only admission, other drift,
owner/native/gate failures, six-scenario routing, no fallback and retention.
Heavy full checks stay on PR CI; actual owner correction proof remains post-merge
main-only against the paused bootstrap fixture.

No new fault, CI jobs/scenarios/refs/credentials/rights/budget, production change,
automatic repair or local execution. No host command is retired; the verifier
budget remains two modules/708 lines. Ambiguous commits, admission, operational
cutover and measured host retirement remain separate gates.
