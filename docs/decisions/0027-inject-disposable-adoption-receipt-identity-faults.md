# ADR-0027: Inject disposable adoption receipt identity faults

- Status: Proposed
- Date: 2026-10-23
- Milestone: M06 — Production Readiness

## TL;DR

- The owner approved deleting/recreating only the adoption receipt in each owned disposable CI cluster, after retention checks.
- Keep the pointer untouched and require fresh complete parents to refuse missing and replaced receipts before writes.
- This narrowly extends ADR 0026's history-preserving fixture; it never authorizes operational ledger repair.

## Context and decision

Native storage and pure-policy tests already cover dangling and replaced receipt
references. Complete-parent acceptance must prove the actual Git adapter,
readiness subtree, native ledger observations and installed planner refuse these
states together. Successful byte comparisons alone do not exercise UID binding
after deletion/recreation. The owner explicitly approved destructive fault
injection in both existing disposable scenarios after their retention proof.

Keep the existing two runners, request refs, permissions and deadlines. After
all successful/recovery/concurrent cases and original Workflow-deletion retention,
verify the ledger still equals its accepted baseline and matches the unchanged
policy. Recheck the owned cluster UID before any destructive step. Generate
private DeleteOptions containing the exact receipt UID and resourceVersion.
Use native kubectl raw DELETE with that body at the fixed ledger ConfigMap URL;
do not use an unconditional name-only deletion. Reject changed/foreign input.

After confirmed absence, submit a fresh unchanged complete parent. Require
successful live readiness/root/ledger reads and precisely the missing/replaced
reference refusal at `plan`, with main exit 2, no result and no downstream writes.
Require the pointer and all remaining ledger state to match the pre-attempt API
snapshot. Workload, storage, credentials, data, manual root policy and six child
skips retain their existing checks.

Create only the same test receipt from its original immutable payload, labels
and provenance, omitting server-assigned fields. Wait for quota usage to fall
before creation in the receipt-only scenario. Native create must not overwrite
an unexpected winner. Require a different receipt UID and an unchanged pointer
still bound to the original UID. A second fresh complete parent must refuse at
the same precise boundary. Preserve that deliberately conflicted state until
owned-cluster teardown; do not patch the pointer or claim successful recovery.

## Authority, evidence and rollback

Only the existing disposable CI administrator deletes/recreates the receipt.
The production ServiceAccount remains denied ledger patch/delete. No new
credential, job, scenario, ref, controller, production template or verifier is
introduced. Raw ledger objects, DeleteOptions, recreation manifests and logs
stay private. Upload Workflow/worker projections, constant outcomes and safe UID
reports; reports are test decisions, never adoption results.

Run focused positive/negative guard, refusal and CLI tests, shell syntax,
`make validate` and `make workflow-lint`. Live proof runs only after merge in
the existing trusted-main scenarios. Rollback removes these final fault cases;
cluster teardown removes their disposable state. A retained environment with
these symptoms still requires an explicitly reviewed recovery decision.

Initial-create races, ambiguous API responses, ledger export/restore, pointer
advancement, retained adoption, production admission and script retirement remain
separate gates. This is refusal acceptance, not recovery or data durability proof.

Implementation reference: [kubectl v0.35 raw DELETE](https://github.com/kubernetes/kubectl/blob/v0.35.0/pkg/cmd/delete/delete.go)
passes the single local body file through the kubeconfig transport, allowing
server-enforced DeleteOptions preconditions.
