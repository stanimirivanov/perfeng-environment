# ADR-0071: Refuse qualification creation after completed preparation migration

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-31
- Milestone: M06 — Production Readiness
- Extends: [ADR 0070](0070-refuse-complete-preparation-job-create.md), phase-boundary fault scope

## TL;DR

- Add one qualification-create denial after the earlier migration-create denial, before fresh complete first/repeat runs.
- One reversible CI-only suspension follows the unchanged migration phase; production templates remain unchanged.
- Prove successful migration and retained state before narrowing only Jobs create on the existing grants Role.
- Require real qualification admission/quiescence and exact native Forbidden, without a qualification Job or grants execution.
- Only proved refusal/retention permits guarded restoration; retain the failed parent and successful migration through final gates.
- Add one current-schema migration Job within the existing initial-job budget; heavy checks stay on CI and live proof is post-merge.

## Decision and execution window

Use the existing trusted-main initial adopted/paused fixture only, after the
complete parent's earlier null-current and migration-create refusal cases and
before its unchanged successful first/repeat parents. Reuse the pinned upgrade
request, caller, mutex, total deadline, reviewed native phases and credentials.

Create one CI-only WorkflowTemplate with a distinct fixed name and one suspend
step immediately after migration. Removing exactly that step/template and
restoring metadata must reproduce the published parent. Validate the actual
stored template, caller, arguments, mutex/deadline and execution boundaries;
no phase overrides, retries, exit cleanup or alternative SQL are allowed.
Retain the installed CI template's UID/spec and check it again at final retention.

Wait boundedly for the actual Running suspend node. Before authority changes,
require the unchanged migration's full input/admission/quiescence gates, successful
fixed native Job, selected schema evidence and unchanged surrounding state.
There must be no qualification/grant execution, Job or output. Bind the barrier
to that Workflow and require migration completion before suspension. A timeout,
terminal parent or unverified state stops without narrowing or resume.

## Qualification refusal and restoration

Capture perf-platform/release-runtime-grants and reuse the existing create-only
Role guard. Test UID, resourceVersion and full rules before replacing create/get/
watch with get/watch. Verify effective denial of create and preservation of get/
watch with full ServiceAccount impersonation groups, then resume only this parent.

Require real qualification preflight/selection/intent, retained admission and
fresh quiescence, followed by its exact native Job-create Forbidden for
system:serviceaccount:argo-workflows:release-runtime-grants in perf-platform.
Bind the bounded private diagnostic to the actual qualify resource worker's
node annotation, Pod UID, account and main exit 1. Generic failure, timeout,
image startup failure or a different identity/resource is insufficient.

Require no qualification Job/Pod, no grants phase execution/Job/Pod and no
successful preparation output. Preserve the completed migration Job/Pod and
its successful phase evidence. Argo may extend children/outboundNodes graph
edges after suspension; phase ownership still follows boundaryID, while all
other completed migration node fields and worker identities remain checked.
Keep raw Workflow files unchanged; projection exists only in memory after the
adapter is validated.

Verify schema ledger/checksums/timestamps/OIDs, ACLs, rows/sentinel, roles/passwords,
receipt/pointer, Secrets/config, SQL/S3 data and workload/storage identities.
Only the exactly validated Role narrowing is normalized in memory for authority
identity comparison. Raw narrowed observations remain private and unmodified.
Use preparation-qualify-denied paths so earlier denial evidence is untouched.

Only full refusal/retention proof permits restoration planning. Revalidate it
inside the evidence CLI, reread the current Role, require original identity and
exact narrowed rules, then test UID/resourceVersion/rules before restoring the
captured reviewed rules. Verify effective create/get/watch restoration before
fresh first/repeat. No fallback, EXIT repair, retry of failed nodes, workload
recovery or ledger edits are authorized by this case.

## Retention, inventory and verification

Retain five complete Workflow outcomes: null-current, migration-create denial,
qualification-create denial, first and repeat. The new failed parent retains one
successful migration Job. The successful cohort still supplies six Jobs: total
seven, with exactly three migration, two qualification and two grant Jobs.

Before recovery/final success, reread the failed parent/workers, completed
migration Job/Pod, absent later Jobs/Pods, current CI template and restored Role/
effective rights. Revalidate all source/state/probe evidence. Legacy partitioning
may exclude only these reverified three migration and two-per-other-kind Jobs;
retain full private inventories and reject missing/replaced/unknown cohort state.
Do not expand a label-based ignore list.

Focused tests cover reversible adaptation, premature/later execution, wrong
worker/Forbidden cause, changed migration/state/authority, restoration gating,
raw record retention, strict inventory accounting and actual Bash stop paths.
Production declarations do not change. Heavy make verify/make validate run on PR
CI because the host is busy. Full controller/API proof remains post-merge under
the existing trusted-main guard; offline tests do not claim it passed.

No new CI jobs/scenarios/refs/requests/credentials/rights/budget, production Python,
extra recovery or local execution. Partial SQL writes, ambiguous commits, general
upgrades, global operator exclusion, backup, non-local/multi-user safety, cutover
and host-script retirement remain separate.
