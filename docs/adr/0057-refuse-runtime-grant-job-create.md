# ADR-0057: Refuse runtime grants when native Job-create authority is missing

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-29
- Milestone: M06 — Production Readiness
- Extends: [ADR 0056](0056-exercise-owner-runtime-grants.md)

## TL;DR

- Add one bounded denial before the initial fixture's existing first/repeat grants.
- Remove only Jobs create from release-runtime-grants; preserve reads/get/watch.
- Require the real successful parent prefix and exact native Forbidden without a Job or SQL change.
- Restore captured rules only after refusal/retention proof, using UID/version/rules guards.
- Retain the failed Workflow and recheck restored authority at final retention; heavy checks stay on CI.

## Decision and fault boundary

The owner approved this disposable authorization fault in the existing
trusted-main initial job. After the ungranted runtime probe and staged authority
installation, capture the published `perf-platform/release-runtime-grants` Role.
Remove only create from its Jobs rule using JSON Patch tests for UID,
resourceVersion and complete rules. Preserve all observation, get and watch
rights. Reuse the existing schema-migration Role guard with an explicit reviewed
Role source; its schema defaults and CLI behavior remain unchanged.

Check effective create/get/watch rights using the real grant ServiceAccount and
its authenticated/ServiceAccount groups. Bound propagation to 30 seconds; reject
warnings, transport errors, unknown answers and unexpected exit codes. Submit
the unchanged complete grant parent with the existing pinned request. Require
successful ordered preflight, exact selection, intent and fresh quiescence, then
only the native grant worker may fail with exit 1 and Failed/Error phase.

Read only that worker's bounded main-container log. It must prove the API's
Forbidden for release-runtime-grants creating jobs in batch/perf-platform and,
when named, the exact Workflow-UID Job. A preliminary GET refusal, timeout,
shutdown, missing worker or generic authorization failure cannot qualify.

## Retention and guarded restoration

Require no grant Job/Job Pod, no Job inventory growth and no successful parent
checkpoint/job-name output. Before/after observations must preserve ACL bytes,
schema ledger/checksums/timestamps/OIDs, rows/sentinel, roles/passwords, Secrets,
paused bootstrap resources and ready foundation/storage identities. Keep all raw
resource, SQL and native error evidence private.

Only verified refusal and retention allow a fresh Role read. Revalidate captured
identity/metadata and exact narrowed rules, then restore only the original rules
with UID/current-resourceVersion/full-rules tests. Verify restored rules and
effective create/get/watch before the existing first/repeat parents. Failure
stops without EXIT restoration, repair, administrator fallback, retry or workload
activation. At final retention, require the same failed Workflow, both existing
successful Jobs/Workflows, source identities and restored Role identity/rules/rights.

## Verification and exclusions

Deterministic tests cover exact native refusal, unchanged SQL/state, guarded Role
patches, effective authority errors and fail-stop restoration/continuation.
Run focused tests, lint, types, policy and Bash syntax locally. Heavy full checks
stay on PR CI; complete-parent live proof remains post-merge main-only.

No production templates, CI jobs/scenarios/refs/credentials/rights, budget or local
infrastructure changes. Other five scenarios and the administrator excess-DELETE
fault/correction are unchanged. SQL failure/ambiguity, admission, cutover and host
retirement remain separate; the production verifier budget stays two modules/708 lines.
