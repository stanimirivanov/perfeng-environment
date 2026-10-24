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

ADR 0028 adds two owner-approved CI inputs in the same owned temporary commit/ref.
Equivalent JSON with changed bytes must not reuse the accepted retry identity;
an upgrade envelope naming the accepted digest must not use adoption as promotion.
Both fresh parents must complete readiness then refuse precisely at plan before
any write/result, preserving original ledger objects and absent candidate receipts.
See [request boundary acceptance](release-workflows-integration.md#adoption-request-boundary-acceptance).
These cases run before retention/destructive tests in both scenarios; live proof
awaits post-merge CI. Actual promotion and pointer advancement remain separate.

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

Concurrent exact-retry acceptance adds a bounded native suspended mutex holder
and two simultaneously submitted unchanged parents after the baseline exists.
Both must demonstrably queue without workers/results or state changes, then
complete with distinct preserved UIDs, non-overlapping controller entrypoint
intervals and unchanged ledger bytes/provenance after explicit holder release.
See [complete adoption acceptance](release-workflows-integration.md#complete-existing-release-adoption-parent).
This local mutex test does not establish initial-create conflict recovery,
partial-write/ambiguous-response recovery or exclusion of external operators.
Live proof awaits the post-merge trusted-main run.

ADR 0026 adds isolated initial and receipt-only CI scenarios without deleting
history to reset a fixture. In recovery, native quota exhaustion must stop only
pointer creation after the parent commits a real receipt. After an identity/
version-guarded quota increase, a fresh unchanged parent must preserve the failed
Workflow's full receipt and create only the pointer. Both scenarios retain all
existing retries/refusals/concurrency and data checks, with distinct refs/artifacts.
Deleting the failed origin must leave the completed ledger unchanged. See
[receipt-only acceptance](release-workflows-integration.md#receipt-only-recovery-scenario).
Live proof awaits both post-merge matrix legs. Initial-create races and ambiguous
API-response recovery remain separate; quota rejection does not simulate either.

ADR 0027 authorizes a CI-only destructive extension after those retention checks.
Both disposable scenarios conditionally delete only the accepted receipt using
UID/resourceVersion preconditions. A fresh parent must refuse the dangling
pointer at `plan` after successful readiness. Native recreation preserves the
original payload/provenance but receives a new UID; another fresh parent must
refuse that replacement while preserving the original pointer. No record is
repaired and no recovery success is claimed. See
[receipt identity refusal acceptance](release-workflows-integration.md#receipt-identity-refusal-acceptance).
Live proof awaits both post-merge scenarios. Retained ledger changes still
require a separately reviewed recovery decision.

Offline gates are `make workflow-lint` and `make validate`. On an isolated disposable kind fixture, prove first adoption, exact repeat, receipt-created/pointer-missing recovery, concurrent create conflict and ambiguous response recovery. Test wrong cluster/root revision, lost/replaced receipt UID, foreign ownership, mutable/changed content and root changes before final verification. Prove denial of ConfigMap patch/delete, Secret enumeration, workload writes, Job creation and Application sync. Delete a **disposable Workflow only** and prove receipt/pointer retention. Record the original object UIDs and immutable bytes.

The [native receipt response-loss fixture](release-workflows-integration.md#native-receipt-response-loss)
adds an owner-approved TLS transport fault at the storage boundary: commit one
receipt but drop the successful response, then require failed native execution,
fresh direct read/retry/planner checks and retained identity/provenance. It keeps
coordinator interruption separate and changes no production template or permission.
Actual kind proof awaits CI; complete-parent ambiguous-response recovery remains
outstanding. Synthetic fixture readiness is not an adoption authorization.

No live adoption or deletion is performed by this implementation task. Pointer advancement, broad health parity, external export/restore, operational command cutover, M06 admission and script retirement remain separate gates. The planner counts toward the original Python retirement limits; no orchestration is hidden from that accounting.

The [API authorization refusal fixture](release-workflows-integration.md#adoption-api-authorization-refusals)
adds actual `Forbidden` checks to the native-ledger job and both trusted-main
scenarios before destructive receipt faults. Writes use server dry-run; selected
ledger snapshots must remain identical. Exec remains a review only. This tests
impersonated API authorization, not token authentication; live proof awaits CI.

[Complete-parent receipt response loss](release-workflows-integration.md#complete-parent-receipt-response-loss)
adds ADR 0031's owner-approved third trusted-main scenario. A CI-only adapted
parent must fail at a receipt write whose committed API response is dropped;
fresh direct observations bind its original bytes/UID/provenance. A fresh
unchanged parent must then create only the missing pointer, followed by all
existing retention/refusal checks. No production template or permission changes.
Live proof awaits the post-merge main-only scenario; native-ledger evidence alone
does not satisfy it. Pointer response loss, promotion and retained recovery remain
separate gates.

[Pointer response loss](release-workflows-integration.md#complete-parent-pointer-response-loss)
adds ADR 0032's fourth owner-approved isolated scenario. A committed pointer with
a lost API response must leave both records intact despite the failed origin.
A fresh unchanged parent must reverify and skip both creates, preserving complete
record snapshots, UIDs and original receipt provenance. Existing retention and
refusal checks continue. Production templates/rights remain unchanged; live proof
awaits post-merge trusted-main execution.

[Initial receipt-create conflict acceptance](release-workflows-integration.md#complete-parent-initial-receipt-create-conflict)
adds ADR 0033's fifth approved isolated scenario. A fixture writer wins the exact
receipt create after real planning; the unchanged native worker must refuse with
AlreadyExists. A fresh production retry preserves the winner's full snapshot,
UID and planned origin provenance, skips receipt creation and creates only the
pointer. No proxy or new rights are introduced. Live proof awaits post-merge
trusted-main execution; pointer conflicts, promotion and retained recovery remain
separate gates.

## Verifier package lifecycle

The staged entrypoint uses the [v2 verifier package](release-verifier-package.md).
Request and result data schemas remain v1. Published v1 source is recoverable
from the retirement index; installed v1 objects are retained with pruning off.
Use a fresh Workflow after upgrading; do not resume successful v1 nodes as v2
verification. The active operator path and outstanding live acceptance gates
remain unchanged.
