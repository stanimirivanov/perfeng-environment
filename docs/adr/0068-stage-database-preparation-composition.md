# ADR-0068: Stage declarative database preparation composition

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-30
- Milestone: M06 — Production Readiness
- Extends: [ADR 0067](0067-exercise-read-only-schema-qualification.md), deferred preparation composition

## TL;DR

- Stage one native Steps parent: schema-migration v2 → schema-qualification v1 → runtime-grants v2.
- Reference unchanged published entrypoints; retain all admission and fresh quiescence gates.
- Reuse four pinned inputs, release-runtime-grants caller authority, one mutex and a total 1,800-second deadline.
- Return three retained Job names and the grants phase's pre-Job checkpoint; leave paused.
- Add no RBAC, credentials, production Python, live execution/CI selection, repair or retirement credit.

## Decision and composition

The owner approved `perfeng-release-database-prepare-v1`, a staged declarative
composition of the three existing database parents. Singleton serial step groups
reference their published `migrate`, `qualify` and `grant` entrypoints through
templateRef. No containers, SQL, policy predicates or orchestration scripts are
copied into the new parent. No child Workflow objects are created.

The four pinned workflow parameters remain request-revision, request-path,
request-sha256 and selection-revision. Each referenced parent reads those same
outer Workflow parameters, performs its complete preflight/selection/forward-only
intent and retained-baseline admission, then freshly verifies quiescence and
identity before its fixed Job. Repeated checks are intentional: do not reuse an
earlier phase's checkpoint as admission for a later SQL operation.

The outer Workflow alone sets release-runtime-grants, the shared
perfeng-local-external-state mutex and a total 1,800-second deadline. Referencing
an entrypoint does not instantiate that WorkflowTemplate's spec-level identity,
mutex or Workflow deadline. Native template/Job deadlines remain unchanged; the
outer deadline bounds the entire sequence rather than multiplying the three
standalone Workflow budgets. The mutex covers cooperating Workflows, not other
operators or a global database lease.

All three Jobs retain their distinct operation-specific names derived from the
same outer Workflow UID. This avoids duplicate creates within a successful
sequence. Keep default stop-on-failure: no when/continueOn, retries, exit hooks,
parallel groups, optional phases or automatic recovery. Outputs are the three
Job names and the grants phase's checkpoint. That checkpoint was observed before
the grants Job; it is not post-grant readiness, a new release receipt or a lease.

## Authority and compatibility

The owner explicitly approved extending release-runtime-grants' reviewed use to
the migration phase. Its existing observation and Job-create/get/watch rules
match the schema identity's rules; its explicit Application and ledger-get
bootstrap remain prerequisites. Referenced template identities do not switch the
caller back to release-schema-migration. No Role, Binding, Secret or permission
changes are required.

Broad Job creation already permits arbitrary Job bodies and namespace Secret
mounts. Fixed manifests and owner approval constrain trusted operators, not a
multi-user admission boundary. Keep the existing Git-read credential, fixed SQL
owner URL and migration image pull Secret; do not mount an administrator
credential or add dynamic SQL/image/credential parameters.

This supports only the retained, adopted, already-paused local lock/0018 pilot.
Initial bootstrap/null expectedCurrent is rejected by existing v2 admission. A
changed release or migration inventory requires reviewed immutable versions;
composition does not establish general upgrade safety or complete DDL equivalence.
Standalone parents and host tasks remain unchanged and supported. This composes
the database subphase only: external-state provisioning, maintenance pause,
runtime package selection, readiness, promotion and activation stay separate.

## Failure, rollout and recovery

Failure prevents subsequent phases and a successful preparation result. Earlier
SQL may already have committed: timeout/termination or missing output is not
proof of rollback. Preserve all Workflows/Jobs, SQL, ledger records, credentials
and paused workloads. Do not retry a node, remove evidence, run grants manually,
resume or repair on EXIT. Observe actual retained state and follow a separately
reviewed recovery procedure; ambiguous commits remain open work.

Add only the new WorkflowTemplate to staged release-workflows Kustomize. Install
the existing prerequisites explicitly under the staged AppProject; do not select
it in the active local root or existing CI execution paths. No local/live
submission is authorized by this staging PR. Withdrawing staged Git selection
does not delete installed versions, authority or retained evidence.

## Verification and limits

Focused offline tests check exact serial refs and absence of failure bypasses,
unchanged phase admission chains, fixed input/caller/mutex/deadline contract,
required output bindings, distinct same-Workflow Job names, existing authority
coverage and staging isolation. These are structural assertions, not a custom
Argo scheduler or proof of engine failure semantics. Render the staged Kustomize
bundle, validate its AppProject and run pinned Argo offline lint with all
referenced templates available. Heavy checks remain on PR CI.

Complete-parent controller/SQL first/repeat and failure/partial-state acceptance
requires a separate approved live slice. Existing standalone acceptance does not
prove the new composition. No CI job/scenario/ref/right/credential/budget change,
new production Python, host cutover or measured script retirement. Backup,
concurrent-operator exclusion, multi-user/non-local operation, general upgrades,
readiness and promotion remain separate.
