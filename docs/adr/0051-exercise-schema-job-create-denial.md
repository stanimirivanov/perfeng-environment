# ADR-0051: Prove complete schema-parent Job-create refusal

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-28
- Milestone: M06 — Production Readiness
- Extends: [ADR 0050](0050-exercise-paused-schema-migration-repeats.md)

## TL;DR

- Add one denied-authority case in the existing initial job's paused window.
- Remove only Jobs create from the fixture migration Role; preserve get/watch and reads.
- Require the real successful prefix and exact native create Forbidden with no Job or SQL change.
- Restore captured rules only after refusal and retention proof, with UID/version/rules guards.
- Keep production templates, CI permissions/budgets and local infrastructure unchanged.

## Decision and scope

The owner approved this bounded disposable fault. After none-intent refusal and
before the two valid repeat runs, capture the `perf-platform/release-schema-migration`
Role. Require its published rules and stable identity, then remove exactly the
Jobs create verb using JSON Patch tests for UID, resourceVersion and full rules.
Keep all get/watch and observation rights. No new credential is issued or token
forwarded. The existing fixture administrator checks effective create/get/watch
access using ServiceAccount impersonation with its normal authenticated and
ServiceAccount groups. Bound propagation checks to 30 seconds; unknown responses,
warnings, transport failures and unexpected exit codes stop acceptance.

Submit the unchanged complete parent with the existing forward-only upgrade
request. Require successful real preflight, exact selection, intent and fresh
quiescence in order. Every worker uses the migration identity. Only the native
migrate worker may fail, with exit 1 and Failed/Error phase; the Workflow must
fail without shutdown. Its own bounded main-container log must prove the API's
Forbidden for that identity creating Jobs in batch/perf-platform and, when named,
the exact Workflow-UID Job. Generic authorization signals, preliminary GET
failures, missing workers, startup errors and timeouts cannot qualify.

## Retention and restoration

Require no native Job/Job Pod, no successful parent checkpoint/job-name output,
and no migration inventory growth. Reuse the full paused-state observations and
private SQL snapshots: schema ledger/OIDs/checksums/timestamps, roles/password
hashes, rows/sequences, sentinel, receipt/pointer provenance, Secrets/config/Git,
foundation/workload/storage identities and SQL/S3 data remain unchanged. The
Deployment stays at observed zero replicas with no matching Pods.

Only verified refusal and all retention gates permit a fresh Role read and
restoration patch. Revalidate its UID/metadata and exact narrowed rules; test
UID, current resourceVersion and complete rules atomically before replacing
only rules with the captured original. Verify restored rules and effective
create/get/watch before either repeat. Recheck live restored authority and Role
identity/rules at final suite retention. Retain all four Workflow identities and
the existing two successful Jobs; recompute refusal/repeat retention evidence
before the existing explicit checked recovery. No baseline advances here.

Failure stops without Role repair, retries, EXIT restoration or workload
recovery. Disposable cluster cleanup remains the existing owner boundary. This
test does not narrow the production migration authority or make broad native
Job creation suitable for untrusted multi-user use.

## Verification, rollout and remaining gates

Focused offline tests cover wrong identity/verb/resource/namespace/name, invalid
prefix and outputs, new Job/Pod/state changes, Role guards, access errors and
fail-stop restoration/recovery order. Lint/type/docs/shell checks run locally;
heavy full/cross-platform checks run on PR CI. Full-parent live proof remains
post-merge in trusted-main, within its existing job/scenario/ref/credential and
60-minute budget. No local execution or new production Python is added.

Rollback removes this denial case and its evidence helpers, retaining the
existing none-intent and repeat acceptance. Complete-parent fresh installation,
partial/ambiguous SQL failure, upgrades/admission/recovery, operational cutover,
production multi-user security and measured host-script retirement remain open.
