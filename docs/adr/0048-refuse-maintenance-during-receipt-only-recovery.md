# ADR-0048: Refuse maintenance during receipt-only adoption recovery

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-28
- Milestone: M06 — Production Readiness
- Extends: [ADR 0047](0047-refuse-maintenance-before-initial-adoption.md)

## TL;DR

- Reuse the existing receipt-only job's valid receipt and naturally missing pointer.
- Submit the unchanged maintenance parent before quota recovery.
- Require exact missing-pointer finish refusal after real readiness and ledger reads.
- Preserve receipt bytes/UID/provenance and running/persistent state before recovery.
- Keep heavy checks on PR CI and full live proof post-merge; no local operations.

## Decision and authorization

The owner approved extending maintenance refusal acceptance into the existing
trusted-main `receipt-only` leg on 2026-10-28. Run after its already verified
pointer-admission failure and quota used-count observation, before generating
or applying the approved quota increase and retrying adoption. Install the same
staged templates/RBAC in that owned disposable cluster through the existing
shared helpers. This extends ADR 0047's initial-only scenario boundary without
adding jobs, scenarios, refs, credential rights, credentials or budget increases.

No record is deleted, injected or repaired. Root stays auto-sync-disabled and six
other children skipped. Production templates/verifiers, local infrastructure,
Pod baselines and existing adoption recovery operations remain unchanged.
The earlier empty-ledger gate retains its original distinct expectations.

## Required proof and recovery gate

Before and after maintenance, compare the sole immutable receipt with the full
previously verified `adoption-receipt-only.records.json`, preserving bytes, UID,
provenance and all other fields. The pointer must remain absent. Validate the
receipt using the unchanged adoption policy and the exact pinned request,
selected readiness and fresh root observations. Planning must require no
receipt creation and exactly the missing pointer creation.

Submit the unchanged complete maintenance parent with the same pinned inputs.
Require successful preflight/readiness and native root/ledger observations,
exact observed baseline inputs with phase `finish`, the sole baseline worker
exit 2 and fixed redacted refusal. Replay the immutable runtime against these
observations, redirecting only private runner I/O; it must refuse specifically
with `stored pointer missing` before publishing any output. An invalid receipt,
missing fresh root, initial empty ledger, API error or earlier readiness failure
cannot satisfy this acceptance.

No later pause guard/PATCH/checkpoint/result may execute. Reuse strict retained
state checks and independently validate both roots and skipped children.
Pods, workload/Deployment identities and specs, Secrets/configuration/Git
observations, cluster UID and foundation/storage/SQL/S3 identities/data must
remain intact. Publish a bounded validated outcome including the receipt UID;
keep raw API/Workflow/worker/log/Secret snapshots private.

Only verified refusal and retention allow the existing quota increase and fresh
adoption recovery. Unexpected success, timeout, unrelated failure, changed
receipt or retained state stops before recovery. There is no automatic repair,
write retry, manual sync, new recovery path or baseline advancement. Existing
owned disposable-cluster/ref cleanup remains responsible for teardown.

## Verification, rollback and limits

Extend focused evidence/CLI tests with valid receipt-only planning/refusal,
wrong mode, altered receipt identity/data/provenance, unexpected pointer,
non-finish phase, changed Pod and original partial-receipt mismatch. Exercise
shell stop-before-continuation boundaries for both supported scenarios; run
focused lint/type/docs/syntax checks locally. Heavy full repository,
cross-platform/render/lint checks stay on PR CI. Actual controller/data proof
stays post-merge in the existing receipt-only trusted-main job.

Rollback removes this receipt-only invocation and scenario support, preserving
the original initial gate and existing recovery procedure. This supplies no
local deployment authorization, production multi-user security, complete-parent
enabled transition, operational cutover or measured script-retirement credit.
