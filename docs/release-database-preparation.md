# Staged database preparation

## TL;DR

- `perfeng-release-database-prepare-v1` composes migration v2 → read-only qualification v1 → runtime grants v2.
- Reuse the four pinned inputs and unchanged complete entrypoints; every phase rechecks admission and quiescence.
- One outer release-runtime-grants identity, shared mutex and total 1,800-second deadline govern the sequence.
- Return three retained Job names and the last phase's pre-Job checkpoint; leave the application paused.
- Outside the active root; bounded trusted-main acceptance runs post-merge, with local execution and host cutover separate.

## Scope and prerequisites

[ADR 0068](decisions/0068-stage-database-preparation-composition.md) authorizes
staging this database subphase. It does not replace the full kubernetes:prepare
task, provision external state, pause workloads, select runtime packages or
activate a release. The supported host path and three standalone parents remain.

Require the fixed selected local release, reviewed manual/forward-only/0018
request with expectedCurrent bound to the adopted receipt, healthy foundations,
retained current/receipt identities and an already-paused root/child/Deployment
with no matching Pods. This is the v2 retained-baseline path, not fresh bootstrap
or admission of an arbitrary new schema/release. Keep drain, backup and operator
exclusion as independent requirements; the workflow mutex is not a global lease.

Explicitly install the staged definitions and existing release-runtime-grants
ServiceAccount/observation/Job authority, Application access and standalone
ledger-get Role/Binding. Existing preflight Git-read Secret, verifier/intent/SQL
ConfigMaps, database migration-url and selected migration image pull Secret must
already be present. Referenced WorkflowTemplate specs do not install these
prerequisites or switch caller identity. No new RBAC or credentials are added.

## Native flow and outputs

```text
four pinned inputs + one outer mutex
  -> schema-migration-v2 / migrate
  -> schema-qualification-v1 / qualify
  -> runtime-grants-v2 / grant
  -> three retained Job names + grants pre-Job checkpoint; remain paused
```

Each arrow waits for successful completion. Each phase retains its own complete
preflight, exact-lock/intent, accepted-byte/current/receipt admission and fresh
quiescence/final identity checks. Nothing caches a prior admission result across
phases. templateRef invokes the unchanged Steps entrypoints within one Workflow;
it does not submit nested Workflows or reacquire their spec-level mutexes.

The outer caller is release-runtime-grants even during migration. The owner
approved that expanded reviewed use; broad existing Job creation already permits
arbitrary Job manifests/Secret mounts, so this remains trusted-operator authority.
All phases share the same four Workflow arguments. Native operation-specific Job
prefixes keep their names distinct despite sharing the outer Workflow UID.

The total deadline is 1,800 seconds, not three independent Workflow budgets.
Existing template/Job limits remain in force. No retries, continueOn, optional
phases, exit cleanup or automatic recovery is configured.

Successful output parameters are migration-job-name, qualification-job-name,
grants-job-name and checkpoint. All are required. The checkpoint is the grants
phase's observation before its Job, not a post-grant/runtime TCP check, promotion
receipt or reusable authorization. Qualification checks selected ledger/ownership;
it does not establish complete DDL equivalence or grant/HTTP readiness.

## Failure and rollout boundary

Failure stops later phases, leaves the application paused and preserves native
execution evidence. Migration or grants may have committed before an error or
lost response. Do not assume global rollback, retry nodes, delete/recreate Jobs,
edit the ledger, rotate credentials or resume to make the sequence pass. Inspect
actual retained state under explicit authority and obtain a reviewed recovery
decision. No successful preparation output authorizes activation.

Only staged release-workflows Kustomize selects this new declaration. Its
AppProject boundary and active local root remain unchanged. Only the separately
approved trusted-main initial fixture invokes it as described below; there is no
local execution selection. Removing staging selection does not prune installed
versions or evidence. Operational withdrawal needs explicit approval.

## Verification and remaining work

Focused tests check serial failure-closed structure, references and admission
chains, required outputs, Job naming, caller authority and staging isolation.
Staged rendering/AppProject validation and pinned Argo lint resolve references
offline. These do not simulate or prove controller/SQL execution. Heavy full
checks stay on PR CI. Bounded first/repeat/refusal/retention acceptance is wired
below for post-merge trusted-main execution; deliberate failure/partial-state
injection remains separate.

General upgrades, ambiguous commits, SQL fault qualification, operator exclusion,
backup, readiness, promotion/resume, non-local/multi-user operation, host cutover
and measured script retirement remain separate. No production Python is added.

## Complete-parent trusted-main acceptance

[ADR 0069](decisions/0069-exercise-complete-database-preparation.md) preserves all
standalone coverage and adds initial-request refusal plus two complete parents
in the existing initial adopted/paused window, before recovery. Each successful
parent creates three retained Jobs; six additional native Jobs fit within the
existing 60-minute job budget. Other five scenarios, requests/refs, credentials,
rights and production templates remain unchanged.

The existing adopted runtime TCP probe runs after composition, without an extra
probe Job. Require actual nested phase ownership/order, the outer stored caller/
mutex/deadline, all six Jobs/Pod identities, exact outputs and unchanged SQL/ACL/
ledger/credentials/data/workload/storage state. Raw complete Workflows remain
private and unchanged; in-memory phase views reuse existing predicates only
after boundary validation. Recheck executions, sources/authority, probe and state
before recovery and final success. Safe summaries are
`database-preparation-{retained,final}.outcome.json`.

Legacy suites account for the cohort only after its complete retention proof:
keep the full private `.all.json` inventory, remove exactly the validated cohort
UIDs of that kind from the legacy input (three migration, two qualification and
two grants after ADR 0071), then retain strict legacy checks on
all remaining Jobs. Unknown, missing, replaced or altered Jobs cannot be ignored.
No failed gate permits probe/recovery or automatic repair. Offline tests do not
establish live proof; main-only execution after merge must supply it. The bounded
Job-create denial below extends fault coverage; partial SQL faults, general
upgrade safety and operational cutover remain separate.


## Complete-parent Job-create denial

[ADR 0070](decisions/0070-refuse-complete-preparation-job-create.md) adds one
failed complete parent after null-current refusal and before existing first/
repeat. Temporarily remove only Jobs create from the grants caller's fixture
Role. Require real migration admission/quiescence and the exact native create
Forbidden, with no later qualification/grant workers, SQL Job/Pod or preparation
success output. Keep data, credentials, ledger and workload identities unchanged.

Only proved refusal/retention allows a fresh UID/resourceVersion/rules-guarded
restore to the captured reviewed rules. Verify effective create/get/watch before
fresh runs, and the failed execution plus restored authority again before
recovery/final success. All preparation-denied evidence has a separate prefix;
earlier denial records and the six successful cohort Jobs remain unchanged.
No automatic repair, additional SQL Job, CI job/scenario/ref/right/credential/
budget, production change or local execution. Heavy checks remain on PR CI;
live API/controller proof stays post-merge main-only. This proves refusal before
SQL Job creation, not recovery after partial or ambiguous SQL commits.

## Qualification-create denial after migration

[ADR 0071](decisions/0071-refuse-preparation-qualification-job-create.md) extends
only the trusted-main initial adopted/paused fixture. One reversible CI copy
suspends after the unchanged migration phase. Prove its successful native Job,
full admission and unchanged state before guarded create-only Role narrowing.
Resume only after effective create denial/get-watch preservation; require real
qualification admission/quiescence then the exact native qualify-worker Forbidden.
No qualification Job, grants execution or successful preparation output may exist.

Only verified refusal and retention permit fresh UID/resourceVersion/rules-guarded
Role restoration and effective-right checks before existing fresh first/repeat.
The failed parent, its completed migration and unchanged state remain required
through final gates. Evidence uses preparation-qualify-denied paths, with the
pre-mutation snapshot under preparation-qualify-barrier. No automatic repair or
recovery is introduced. The cohort is now seven Jobs: three migration, two
qualification and two grants; legacy partitioning excludes only verified UIDs.
Full private inventories remain available. No production changes or host cutover;
heavy checks stay on PR CI and live proof remains post-merge main-only.
