# Staged explicit release adoption ledger

## TL;DR

- Use `perfeng-release-adopt-existing-v2` only for a deliberately reviewed adoption of an existing local selection.
- Native Argo operations create immutable receipt bytes and the initial current pointer in `perfeng-release-ledger`; exact retries preserve the original record.
- Conflicting, foreign, changed or dangling state is refused. This entrypoint cannot advance, patch or delete a current pointer.
- The result is an adopted baseline, not executed promotion, full platform acceptance, migration authorization or backup/recovery proof.
- Keep existing operator paths; live disposable-kind acceptance and retirement gates remain outstanding.

## Scope and prerequisites

[ADR 0017](decisions/0017-record-explicit-local-release-adoption.md) records the owner's ConfigMap choice. The [v1 schema](../deployments/release-adoption-v1.schema.json) defines the receipt payload; its `$defs.pointer` and `$defs.result` define the current reference and final output. The original release request and its exact-byte identity are unchanged.

Adoption is an explicit trusted-operator action after review of current state, acceptance evidence and any missing/lost-ledger history. A schema field, request commit or successful readiness check does not prove that authorization. Do not submit this entrypoint as a first-install shortcut, automatic retry of promotion, migration step or lost-history repair.

Apply the separately reviewed bootstrap resources, including `ledger-namespace.yaml`, `ledger-access.yaml` and `adoption-application-access.yaml`, then explicitly sync the staged release-workflows Application. The active local root is unchanged. The Project adds only the ledger destination; its kinds and the exclusion of `argocd` remain unchanged. Namespace and runtime history have a separate retained lifecycle. Do not include generated receipt or pointer objects in Git, Helm or a Kustomize root.

## Submission and observed revision

Use the [preflight submission procedure](release-preflight-workflow.md#disposable-kind-verification) with `--from workflowtemplate/perfeng-release-adopt-existing-v2` and the same four pinned inputs. The request must select `perfeng-local`, manual activation and `expectedCurrent: null`; in this distinct adoption entrypoint, those bytes identify the baseline, not permission to deploy over an existing environment. Requests larger than 64 KiB are refused by the local ledger profile.

The root must report the exact selected Git commit, `Synced`/`Healthy`, the existing owned root source/project/destination and no active operation or condition. Fresh [readiness](release-readiness.md) must match that request/cluster/selection. Its scope remains control-plane/foundation readiness, not full platform acceptance. The root tracks `main`: if a later request commit is merged there before adoption, the root's resolved revision may no longer match the selection. Keep the request at a separately reviewed pinned Git commit/branch and exclude concurrent merges and operator syncs during adoption. The workflow never pins, pauses or syncs the root to make a mismatch pass.

```text
fresh readiness/preflight -> exact root observation + existing ledger reads
  -> pure compatibility plan -> create immutable receipt only if absent
  -> reread receipt + unchanged root -> create initial pointer only if absent
  -> final pointer/receipt/root verification -> adopted-baseline result
```

The parent retains the local release mutex and a 1,800-second deadline. Each native record operation has a 60-second Pod deadline; planner Pods have 180 seconds. The mutex does not exclude host operators or Argo CD. Submit a fresh Workflow for retry; do not reuse successful nodes with `argo retry` or memoize verification. Root versions are compared conservatively: even an incidental root update requires inspection and a fresh attempt.

## Records and authority

`receipt.<32 hex>.<32 hex>` contains immutable `binaryData.request.json` with the exact reviewed bytes and `binaryData.receipt.json` with the adoption payload. Read-back compares the original bytes and payload, not just a name/hash prefix. First Workflow UID and request-delivery revision are annotations; later exact retries preserve them. The adopted payload contains stable selection/ownership identities, while detailed sampled readiness and root resource versions remain in that Workflow's evidence. It does not assert that a forward-only migration in the request was executed.

The mutable `perfeng-local-current` ConfigMap initially contains `requestDigest`, `clusterUID`, `receiptName` and `receiptUID`. UID binding rejects deletion/recreation under the same receipt name. This version uses server-side create-only conflict detection, grants no patch/delete rights and exposes no pointer-advance action. Future advancement must separately implement guarded UID/resourceVersion/expected-current comparison and fresh live-state/authorization checks; do not use unguarded apply, replace, Helm rollback or manual digest editing.

The dedicated identity has readiness/preflight's named reads plus root Application read and get/create on ConfigMaps only in the ledger namespace. Ordinary RBAC cannot restrict creates by name, so that namespace contains no workflow configuration or credentials. The trusted-operator model still permits arbitrary ConfigMap creation there; production admission and quota/retention policies are separate work. Objects have no Workflow owner reference or TTL. ConfigMap immutability prevents payload mutation, not privileged deletion or metadata tampering. Neither the namespace nor records are automatically garbage-collected when a Workflow finishes.

## Partial failure and recovery

| Observed state | Behavior and recovery |
|:--|:--|
| No matching receipt or pointer | After fresh checks, create the receipt and then the initial pointer. |
| Exact owned immutable receipt, no pointer | Reverify live selection; reuse receipt without replay/replacement, then create pointer. |
| Exact receipt and pointer with matching receipt UID | Reverify and return the existing baseline; do not rewrite original provenance. |
| Foreign/mutable receipt, changed bytes, different pointer, dangling/replaced receipt | Stop before writes. Preserve objects and inspect ownership/history through an explicitly reviewed recovery decision. |
| Concurrent create or uncertain API outcome | Native create cannot overwrite the winner. Retain failed Workflow evidence, inspect stored objects and submit a fresh exact attempt. |
| Root changes or final verification fails after a create | Leave created records intact. The failed Workflow is not success evidence; inspect the root and ledger together before any further action. |

Only the final planner phase publishes a successful result; earlier plan phases emit JSON `null`. The final result binds request/selection/cluster and receipt/pointer identities. Reads and creates are not an atomic release transaction, and later administrative changes remain possible. A receipt or pointer is not sufficient authority for migration or promotion. Keep the existing M04 manual cutover and forward-repair recovery boundary, including migration 0018's Git-revert limitation.

Cluster deletion also deletes this ledger. Preserve records outside the cluster through a separately approved export/backup procedure before relying on them for recovery; this PR provides none. A missing ledger after recovery is not evidence of an empty environment. Review explicit re-adoption or restoration instead of guessing prior acceptance.

## Verification and cutover gate

The separate [kind integration fixture](release-workflows-integration.md#native-ledger-persistence)
now exercises the unchanged native storage templates: missing reads, receipt-only
partial state, competing and repeated creates, exact stored bytes/UID binding,
immutable-data rejection, denied authority and retention after fixture Workflow
deletion. A suspension/termination fixture also exercises an interrupted receipt
write and fresh observations. The unchanged pure planner runs on the CI runner
against real ledger records plus synthetic readiness/root status, checking
partial/exact retry and changed-root/dangling/replaced-receipt refusals. It does
not run the planner container, request adapter or complete adoption parent.
These test bytes are not an approved Git request or readiness proof; coordinator
interruption does not simulate loss of the API response. Full adoption,
ambiguous-response recovery and operational cutover remain separate gates.

The separate trusted-main [complete adoption acceptance](release-workflows-integration.md#complete-existing-release-adoption-parent)
now follows real chart/server readiness, foundation Application adoption and
bounded root convergence. It submits the unchanged complete parent with the
existing pinned CI request, verifying first/exact-repeat/corrected results,
native persistence/provenance, a root-read refusal after successful readiness,
and record retention after deletion of the original disposable Workflow. Root
auto-sync remains disabled and six non-control-plane children remain skipped;
no full-tree health or retained adoption is claimed. Actual live acceptance is
pending the post-merge trusted-main run. Complete-parent partial-write/concurrent/
ambiguous-response recovery, promotion and operational cutover remain separate.

Root-drift acceptance extends this parent coverage without changing policy:
after real Namespace-label drift, readiness/root/ledger reads must succeed but
`plan` must refuse before any write with exact exit/diagnostic evidence. The
original records survive refusal and a fresh successful parent after explicit
selected-commit root reconciliation. Only the controlled Namespace difference
is allowed in this test; unrelated comparison/health failures do not qualify.
The root remains manual, six children remain skipped and workload/data retention
is checked throughout. Live proof awaits the post-merge trusted-main run.

Offline gates are `make workflow-lint` and `make validate`. On an isolated disposable kind fixture, prove first adoption, exact repeat, receipt-created/pointer-missing recovery, concurrent create conflict and ambiguous response recovery. Test wrong cluster/root revision, lost/replaced receipt UID, foreign ownership, mutable/changed content and root changes before final verification. Prove denial of ConfigMap patch/delete, Secret enumeration, workload writes, Job creation and Application sync. Delete a **disposable Workflow only** and prove receipt/pointer retention. Record the original object UIDs and immutable bytes.

No live adoption or deletion is performed by this implementation task. Pointer advancement, broad health parity, external export/restore, operational command cutover, M06 admission and script retirement remain separate gates. The planner counts toward the original Python retirement limits; no orchestration is hidden from that accounting.

## Verifier package lifecycle

The staged entrypoint uses the [v2 verifier package](release-verifier-package.md).
Request and result data schemas remain v1. Published v1 source is recoverable
from the retirement index; installed v1 objects are retained with pruning off.
Use a fresh Workflow after upgrading; do not resume successful v1 nodes as v2
verification. The active operator path and outstanding live acceptance gates
remain unchanged.
