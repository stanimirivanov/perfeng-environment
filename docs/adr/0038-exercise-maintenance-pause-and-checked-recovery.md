# ADR-0038: Exercise complete maintenance pause and checked CI recovery

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-26
- Milestone: M06 — Production Readiness
- Extends: [ADR 0037](0037-stage-guarded-maintenance-pause.md)

## TL;DR

- Exercise the unchanged staged pause parent only in the existing trusted-main `initial` leg.
- Preserve original adoption evidence, persistent resources, Secrets, data and both ledger records.
- Permit exactly the verified control-plane Pod replacement after explicit test-only child sync.
- Advance later tests to a checked recovery snapshot; never rewrite the original baseline.

## Decision

The owner approved complete pause acceptance after adoption retention and before the existing receipt identity faults. Install the two unchanged staged templates and their approved authority in that owned disposable cluster. Submit the original pinned request, verify actual selected readiness and adopted-record planning before the three native guarded writes, and require the existing checkpoint output plus fresh stopped-state observations. Root auto-sync remains disabled; all six non-control-plane children retain skip-reconcile and their original identities/specs/operation history.

The existing harness requires the original control-plane Pod throughout retention. A real pause removes it, so this CI-only phase explicitly changes that boundary: after successful pause and retention checks, manually sync only the selected control-plane child with the existing pinned-source helper. Verify HTTP/native health and unchanged workload specs, Deployment/Service UIDs, foundation Pod identities/specs, storage, database canary/schema/ACL, S3 bytes, configuration, Secrets and complete ledger objects. Require a different control-plane Pod name/UID under the same ReplicaSet, with the same labels and Pod spec except the generated ServiceAccount projected-volume name and its mount references. Its projected contents and all other spec fields remain equal.

Keep original snapshots immutable. Only after these checks pass may later adoption tests reference the recovered Pod and recovered retention snapshot. A fresh unchanged adoption parent must still reuse both original records, and the original missing/replaced-receipt tests continue afterward. All other matrix legs keep their original strict Pod baseline. No global Pod exclusion or ledger normalization is introduced.

## Security and operations

Recovery is an explicit fixture step after proved successful pause, never an EXIT trap, automatic rollback or production resume feature. A failed/partial pause or failed retention check exits without advancing the baseline or issuing recovery. Failure during recovery retains evidence and does not authorize subsequent tests. Checkpoint results remain observations, not migration authorization.

Secret bytes and full raw retention snapshots stay in the private fixture. Published evidence contains the Workflow, worker identity/status projection, validated checkpoint, existing sanitized HTTP/native observations and fixed outcome text. No credential is a workflow argument or log diagnostic. The existing job, six scenarios, temporary-ref ownership, credentials, permissions and 60-minute budget remain unchanged. Production manifests, published v3 verifiers and the two-module budget are unchanged.

## Verification and remaining gates

Offline tests exercise sequence/identity/retention refusal and the real shell phase's failure ordering. Repository validation and pinned workflow lint remain required. Complete controller/API acceptance runs post-merge under the existing main-only guard; local execution stays deferred. Root/child are already auto-sync-disabled in this fixture, so this proves they remain disabled through native writes, not an enabled-to-disabled transition. Other pause fault cases, operational recovery, multi-user admission, cutover and host-script retirement remain separate gates.
