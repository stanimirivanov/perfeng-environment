# ADR-0064: Stage runtime grants with shared retained-baseline admission

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-30
- Milestone: M06 — Production Readiness
- Extends: [ADR 0055](0055-stage-native-runtime-grants.md), deferred admission boundary
- Reuses: [ADR 0062](0062-stage-retained-schema-admission.md)

## TL;DR

- Stage runtime-grants v2 beside unchanged v1 bootstrap/fault capability.
- Reference the published schema-v2 admission leaves; copy no predicates or scripts.
- Require pinned inputs, exact selection, manual/forward intent and fresh quiescence before the unchanged owner-only grant Job.
- Add only an explicit standalone ledger-get grant for the existing runtime-grants identity.
- Keep the active root, CI selection, SQL policy and supported host commands unchanged; live v2 acceptance remains separate.

## Decision and flow

The owner approved staged retained-baseline admission for runtime grants. Add
`perfeng-release-runtime-grants-v2` with the same four pinned Git inputs, existing
grants ServiceAccount, environment mutex and checkpoint/Job-name outputs. Preserve
published v1 for bootstrap and existing fault acceptance. No new caller-supplied
SQL, image, role, Secret key, Job manifest or admission policy is accepted.

```text
pinned preflight -> exact local lock -> shared manual/forward-only intent
  -> schema-v2 candidate -> owned pointer -> immutable receipt -> accepted bytes
  -> fresh quiescence -> unchanged receipt -> current pointer/root/child binding
  -> unchanged native owner-only grant Job; leave paused
```

Use `templateRef` to schema-v2's candidate, pointer, receipt, accepted-bytes,
receipt-stable and admission templates. Reference only these leaves, never its
migrate entrypoint or native migration Job. They execute in the grants Workflow's
existing identity; their source WorkflowTemplate's top-level ServiceAccount and
mutex do not introduce a second Workflow or nested lock. No shared leaf overrides
the ServiceAccount or acquires another mutex. Preserve their exact argument wiring.

The reused policy verifies non-null expectedCurrent against the owned current
pointer, immutable receipt UID/content/provenance and exact accepted request byte
hash. It binds root/child UIDs to fresh quiescence and rechecks receipt/pointer
stability. It supports only the fixed current local selection/0018 and initial
adopted baseline, not a different release selection or general upgrade chain.
All admitted runs use the existing fixed runtime-grants Job and immutable SQL.
Schema migration, qualification, role creation and Kubernetes runtime grants are
not part of this workflow.

## Authority, failure and operations

Add `runtime-grants-ledger-access.yaml` as a standalone Role/Binding beside the
existing Application-read bootstrap. It grants ConfigMap get only in
perfeng-release-ledger to release-runtime-grants. Dynamic receipt names mean get
is permitted on any ConfigMap in that namespace; no list/create/patch/delete is
granted. Leave this file outside the bootstrap Kustomization and active local
root. Explicit installation and execution need separate authority. Installing it
also extends the same identity used by v1; v1 remains available to trusted users.

Existing Job-create RBAC can mount arbitrary namespace Secrets; fixed templates
and this gate are trusted-operator policy, not multi-user admission enforcement.
Use only the existing owner URL and pull Secret in the unchanged grant Job,
without an administrator mount. Prior independent schema/role/credential
qualification, backup, drain and exclusion of other operators remain necessary.
The mutex and read observations are not an atomic ledger/SQL lease or proof that
another operator cannot intervene after admission.

The parent deadline is 1,800 seconds, matching schema v2's chain. Its reused
admission leaves retain their 60-second bounds and ten-second native API request
timeouts. Native grant Job/worker limits and SQL timeouts remain unchanged. No
CI job budget is changed. No retry, conditional bypass, hook, promotion, resume
or automatic repair is added. A failure leaves maintenance/evidence in place;
ambiguous grant commits require inspection before separately authorized retry.
Do not use v1 to bypass refused v2 admission.

## Verification, rollout and remaining work

Select only the new parent in the staged release-workflows Kustomization. Test
the full native reference/argument chain, exact authority, identity/mutex/output
boundaries and actual shared refusal predicates. Keep existing v1 grant and schema
admission tests. Focused staged Kustomize/AppProject validation and pinned Argo
lint verify composition; heavy full/cross-platform suites stay on existing PR CI.
Complete-parent live grants-v2 acceptance is deferred to a separately approved
slice; no local/live operation or CI scenario/selection change is authorized here.

Removing staged Git selection does not prune installed immutable objects or RBAC;
withdrawal requires explicit authority. No credential, request file, CI job/ref/
scenario, production Python, host-command or script-retirement change is included.
All 13 host entrypoints and the two-module/708-line production verifier budget
remain unchanged. General upgrades, ambiguous commits, non-local security,
operational cutover and measured retirement remain separate gates.
