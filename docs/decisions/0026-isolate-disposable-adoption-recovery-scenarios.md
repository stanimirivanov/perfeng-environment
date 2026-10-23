# ADR-0026: Isolate disposable adoption recovery scenarios

- Status: Proposed
- Date: 2026-10-23
- Milestone: M06 — Production Readiness

## TL;DR

- The owner approved `initial` and `receipt-only` matrix legs in the existing trusted-main acceptance job.
- Preserve initial acceptance with a separate owned recovery cluster, qualified Git ref and artifact name.
- Use a native ConfigMap quota and guarded quota increase; never edit/delete ledger records or change published workflows.

## Context and decision

Complete adoption and concurrent exact retries require an accepted baseline.
Receipt-created/pointer-missing recovery needs an empty ledger. Deleting history
to reset one fixture would violate retention; replacing initial adoption would
remove existing acceptance coverage. The owner selected two isolated scenarios
instead of manual-only recovery. This extends ADR 0020's CI/ref ownership scope,
without changing ADR 0017's ledger meaning or ADR 0025's bounded root policy.

Use the existing trusted-main job, pinned sources, permissions and staging steps
on independent GitHub-hosted runners. Keep each leg's 30-minute deadline and
disable matrix fail-fast. Both retain the reviewed event SHA with different kind
UIDs. Reject unknown scenarios before external/cluster writes. PR CI cannot select
receipt-only acceptance.

Initial publication retains `refs/heads/ci/preflight/<run>-<attempt>`; recovery
appends `-receipt-only`. Artifact names identify the scenario. Preserve intent
journaling and expected-SHA conditional deletion, refusing cross-scenario journals
and moved refs. The token remains repository-scoped, not ref-scoped; suffix checks
do not attenuate its rights. Existing source-token rights, exact repository URLs
and cluster Secret handling remain unchanged, with the same approved exposure in
each matrix instance. This consumes an additional hosted CI run but adds no
controller or image.

## Deterministic failure and recovery

Require only the root CA ConfigMap in the fresh ledger namespace. Create the
CI-only `ci-adoption-receipt-only` ResourceQuota with `count/configmaps: "2"` and
wait for initialized hard/used values. The unchanged parent must create/read the
immutable receipt and complete pointer planning, then fail its native pointer
create for quota exhaustion. Require exact diagnostic/exit evidence, no adoption
result, a stored receipt with failed-Workflow provenance and an absent pointer.

Raise only this fixture quota to three after checking its original UID, current
resourceVersion, labels, specification and initialized usage. Use atomic JSON
tests for UID/resourceVersion/old limits. After the new limit initializes, submit
a fresh unchanged parent: skip receipt creation, reuse its full original object
and create only the pointer before final verification. Never resume successful
nodes or fabricate readiness/root/ledger status.

Both scenarios run existing repeat/refusal/correction/concurrent cases with
workload/storage/credential/data retention, root manual policy and six child
skips. Delete only the original disposable Workflow, including the failed origin
in recovery, and require completed ledger retention. Raw state/logs stay private;
upload safe evidence and quota identity/usage projections. Quota configuration
is test fault injection, not production admission or a lost API response.

## Verification, rollback and limits

Run focused scenario/ref/quota/parent tests, repository validation, Workflow lint
and shell syntax checks. Live recovery runs only after merge in trusted-main CI;
no local or retained environment is operated.

Rollback removes the recovery matrix leg and fixture. Owned-cluster teardown
removes quota and ledger state; history deletion is never an implicit repair.
Racing initial creates, ambiguous responses, advancement/promotion, retained
adoption, full-tree health, production admission/export/restore and script
retirement remain separate gates.
