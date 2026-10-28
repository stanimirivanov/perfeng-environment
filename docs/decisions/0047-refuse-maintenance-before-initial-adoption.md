# ADR-0047: Refuse maintenance before initial adoption

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-28
- Milestone: M06 — Production Readiness
- Extends: [ADR 0038](0038-exercise-maintenance-pause-and-checked-recovery.md)

## TL;DR

- Exercise the unchanged complete maintenance parent before first adoption.
- Reuse the initial job's naturally absent receipt/pointer; delete no records.
- Require real readiness and exact finish-phase refusal before any pause operation.
- Verify empty ledger and unchanged running/persistent state before first adoption.
- Keep heavy checks on CI; full live proof remains post-merge trusted-main.

## Decision and authorization

The owner approved this earlier initial-job acceptance boundary on 2026-10-28.
Install the same staged maintenance templates and RBAC before the existing first
adoption, while both expected ledger records are naturally absent. The existing
happy pause/recovery and all fault cases still follow completed adoption and
retention. This extends ADR 0038's installation/execution timing only; no new
rights, jobs, scenarios, refs, credentials or budget increases are introduced.

Root remains auto-sync-disabled and the six non-control-plane children stay
skipped. No injected fault, ledger deletion, recovery, Pod replacement, local
operation, production template or verifier version change is authorized.
Shared fixture installation/snapshot functions preserve the existing commands
and checks used by later maintenance acceptance. Sourcing them performs no API
writes; installation is explicit and later reapplication remains idempotent.

## Required proof

Submit the unchanged `perfeng-release-maintenance-pause-v1` with the exact pinned
request inputs. Require successful real preflight, selected foundations, server,
HTTP/readiness, root observations and native null receipt/pointer reads. Bind
readiness and full resource snapshots to fresh private API observations. The
baseline must receive exactly those values with phase `finish`, and fail alone
with worker exit 2 under the maintenance identity. Require the fixed redacted
adoption refusal, no result and no subsequent guard/PATCH/checkpoint worker.

Replay the immutable planner and runtime against observed inputs, redirecting
only runner-side private input/output functions. The planner must accept an
initial adoption requiring both creates, while actual finish validation must
refuse the missing receipt before any output. Root-after must be valid and
non-null, excluding a missing root observation as an alternative cause. This
is evidence classification; it neither replaces live native execution nor
introduces a production decision path.

Before and after, require both records absent and unchanged workloads, raw server
Pod identity/spec, Deployment identity/spec, Secrets/configuration/Git credential
observations and cluster UID. Independently validate both captured roots and
all skipped children, plus existing storage/foundation/SQL/S3 retention. Root
bookkeeping tolerance stays bounded by ADR 0036. No baseline advances.

Only verified refusal and retention allow the existing first adoption. Unexpected
success, unrelated failure, timeout, malformed observations or changed state
stop before adoption without retry, repair, sync or recovery. Existing owned
cluster/ref cleanup remains the sole cleanup path. Publish only a bounded
validated outcome; raw Workflow/API/worker/log/Secret evidence stays private.

## Verification and limits

Focused evidence/CLI and shell failure-order checks, including setup, expected
refusal, timeout and both retention boundaries, run locally with lint/type,
documentation policy and shell syntax checks. Heavy repository, cross-platform,
rendering and workflow lint suites stay on PR CI. Actual controller and data
proof stays in the existing post-merge trusted-main initial job. No local
infrastructure execution is authorized.

Rollback removes the early acceptance phase; later pause behavior stays intact.
This proves refusal of a naturally empty ledger, not receipt-only recovery,
complete-parent enabled transitions, production multi-user admission,
operational cutover or measured script retirement.
