# Deployment script retirement baseline

## TL;DR

- On 2026-10-16, `scripts/` contained 50 top-level Python files (63 including
  packages). That count is the repository inventory, not the retirement target.
- `task bootstrap` and `task kubernetes:prepare` invoke 13 **unique** deployment
  entrypoint files, 3,545 physical lines in total. The two tasks issue 16
  Python invocations; repeated use of a file counts once.
- The migration gate is to remove at least 11 of those 13 entrypoint files and
  leave at most 708 physical lines of deployment-specific Python, including any
  replacement helpers. This is greater than 80% on both measures.
- Product Run acceptance, policy publication, diagnostics and native worker
  tooling are not hidden in this denominator. The existing operating path stays
  supported until replacement and recovery are proven.

## Scope and counting rule

This baseline is taken from [`Taskfile.yml`](../Taskfile.yml) on 2026-10-16,
after the issue branch was created from `main`. It covers the `bootstrap` and
`kubernetes:prepare` task bodies because those are the supported cluster
foundation and M04 release-preparation sequences. Each directly invoked
`scripts/*.py` path is counted once, even when a task invokes it twice or both
tasks use it. Physical lines are `Get-Content <file>` line counts, including
comments and blanks. The table below records the 13 paths and makes the
3,545-line sum reproducible without a new measurement program.

The count excludes the 37 other top-level files and 13 package files. They
include product acceptance, native connectivity, standalone release and
infrastructure validation, and diagnostics. This exclusion does **not** imply
that every excluded file is healthy or permanent. Shared imports such as
`gitops_status.py`, `control_plane_health.py`, `infrastructure_release.py`,
`benchmark_authority.py` and `workspace.py` are not attributed wholesale to
this cohort because they serve other commands. Later PRs must inspect and
retire their deployment-only functions instead of relocating wrapper logic
there to satisfy a file-count target.

The exit measurement uses the same 13-file baseline. A file counts as retired
only when it is deleted from this repository and no supported deployment task
calls an equivalent Python replacement. Count any new deployment-specific
Python entrypoint or helper in the residual file and line totals. Moving
orchestration into a package or a sibling repository does not count as
retirement. Retained stateful rollback or semantic diagnostics can justify an
exception, but they remain in the residual count while their file exists.
Deletion is a target for later PRs, not permission to remove the current
recovery path before its replacement is accepted.

PR 6 selects the versioned non-secret ConfigMaps in the control-plane Argo CD
Application. The 13-file baseline and `control_plane_runtime.py` count do not
change: the M04 host preparation path remains supported until the adopted
Application and recovery path pass disposable-kind acceptance. No script is
counted as retired solely because the declarative source is selected.

PR 7 stages the bucket, database-role and execution-registry preparation Jobs.
The same 13 files and 3,545 lines remain in the retirement baseline because
the host commands are still supported. Count no script as retired until the
first-run, repeat and failed-step recovery evidence permits a reviewed cutover.

PR 8 stages native resource readiness waits and bounded operational probes in
`perfeng-release-readiness-v1`. The host status/health path is still supported;
no baseline file is retired. The new
`gitops/release-workflows/scripts/verify-readiness.py` is a 240-line deployment
verification helper and must be included in the residual file/line accounting.
It performs semantic checks and bounded HTTP probes, with no Kubernetes calls,
subprocess orchestration or custom waiting loops. The fixed 13-file/3,545-line
denominator remains unchanged; the added helper is not hidden from the 708-line
residual ceiling or the residual-file limit.

## Deployment entrypoints and implementation

| File | Task invocation(s) | Current responsibility | Target boundary | Physical lines |
|:--|:--|:--|:--|--:|
| `infrastructure_runtime.py` | `cluster ensure`, `argocd bootstrap` | Validate/extract immutable `k8s-infrastructure` provider and invoke its lifecycle. | Direct pinned substrate bootstrap before in-cluster workflows. | 168 |
| `cluster.py` | `bootstrap` | Validate dedicated context, apply owned namespaces, protect cluster operations. | Substrate identity plus reviewed namespace manifest/bootstrap contract. | 169 |
| `postgres.py` | `bootstrap` | Create/reuse database Secret; retain explicit Helm rollback and health. | External identity provider and release preflight; preserve separate stateful rollback until replaced. | 188 |
| `object_store.py` | `bootstrap`, `initialize` | Create/reuse S3 Secret, initialize bucket through a Job, probe storage, retain Helm rollback. | External identity plus finite workflow for bucket readiness; preserve separate rollback/diagnostic path. | 363 |
| `control_plane_registry.py` | `deploy` in both tasks | Verify selected image access and create/reuse owned GHCR pull Secret. | Immutable artifact validation and external pull identity. | 283 |
| `gitops.py` | `bootstrap` | Validate Applications, provision repository access and apply root Application; also serves health outside these tasks. | Pinned Argo CD bootstrap, declarative Application policy and standard health plus product probes. | 536 |
| `storage_access.py` | default `--execute` | Establish restricted S3 uploader/server identities and verify access with a Job. | External identity plus a bounded release workflow authorization check. | 304 |
| `control_plane_release.py` | `validate` | Verify deployment lock and exact sibling Git/chart/image selections. | Versioned immutable release-input contract and policy validation. | 302 |
| `control_plane_maintenance.py` | `pause` | Guard Application identity, pause sync, scale the owned Deployment to zero and prove quiescence. | Environment-serialized workflow gate before migration; keep M04 manual cutover until proven. | 217 |
| `control_plane_database.py` | `deploy` | Create/reuse database roles and credential Secret, verify login and privileges. | External credential provider and finite database-identity step. | 253 |
| `control_plane_migration.py` | `deploy` | Construct/verify pinned migration Job, wait for completion and grant runtime rights. | One-shot release workflow with immutable Job identity and explicit recovery. | 293 |
| `control_plane_runtime.py` | `deploy` | Assemble content-addressed runtime/registry ConfigMaps and runtime Secret. | Helm/Kustomize for non-secret bytes; external secret provider and release validation for credentials. | 350 |
| `execution_registry.py` | `deploy` | Validate selected execution images and create/reuse separate pull Secret. | Immutable artifact validation and external pull identity. | 119 |
| **Total** | **16 invocations; 13 unique files** | | | **3,545** |

The target-boundary column identifies ownership, not permission to change live
behavior in this PR. A later design may keep up to two of these files for
stateful rollback or diagnostics and still pass the file gate, provided the
deployment task no longer depends on custom sequencing. Any retained Python
must also fit the line gate and have a named owner and recovery reason.

## Operator handoffs outside the two tasks

The [fresh-cluster guide](local-gitops.md#fresh-cluster-staged-bootstrap) and
[M04 cutover guide](kubernetes-runtime-deployment.md) contain more than the
Taskfile commands. These handoffs must receive explicit owners and evidence
before the operator guide can be shortened:

1. Select and review the immutable release lock, cluster incarnation and
   pinned sibling artifacts. A changed `kube-system` UID requires renewed
   qualification and bundle promotion.
2. Supply read-only private Git/GHCR credentials without putting secret values
   in Git or process arguments. Bootstrap the cluster, Argo CD and root
   Application before an in-cluster release workflow can run.
3. After the stateful foundation, publish the reviewed policy through
   `policy_publish.py` with explicit digest approval. This is durable product
   data publication, outside the counted deployment wrappers.
4. Before the M04 migration, stop submissions, drain Runs, retain a suitable
   backup, pause root/child Applications, stop the old worker, and verify zero
   replicas and Pods. The current `task kubernetes:prepare` starts at the
   explicit pause; it does not make the human scheduling or backup decision.
5. Run the pinned migration and external input preparation while sync remains
   paused. Failed preparation leaves the service paused for inspection; no
   automatic Job replacement or schema rollback is supported.
6. Merge the reviewed selection, resume the root, wait for benchmark authority,
   and manually sync the control-plane child without Prune, Force or Replace.
   Migration 0018 means a Git revert alone is not a safe rollback.
7. Run `task status`, `task smoke` and M04 qualification/decision-loop
   acceptance. These are diagnostic or product acceptance commands, not
   proof that a release workflow may own performance Runs.

`task status` also invokes `workspace.py` and the counted
`infrastructure_runtime.py` and `gitops.py`; `task smoke` invokes
`control_plane_smoke.py`; `task destroy` invokes the counted
`infrastructure_runtime.py`. Those actions are deliberately outside the two
task invocation total. They remain in the functional parity checklist for
any file that is deleted. `postgres.py` and `object_store.py` also contain
dormant Helm rollback actions protected by [ADR 0002](decisions/0002-stateful-gitops-ownership.md).

PR 9 stages the read-only [M04 maintenance checkpoint](release-maintenance-checkpoint.md).
`gitops/release-workflows/scripts/verify-quiescence.py` adds 266 physical Python
lines and one deployment helper. Together with the 240-line readiness verifier,
the two new helpers account for 506 residual lines before any retained baseline
code. Both files count toward the at-most-two-file/708-line exit limit; this PR
retires no baseline file and does not satisfy that limit while old paths remain.
The checkpoint makes bounded observations without subprocesses, custom waiting
or maintenance writes. Argo owns sequencing, deadlines and serialization.

The staged [adoption ledger](release-adoption.md) adds a 251-line pure planner
at `gitops/release-workflows/scripts/plan-adoption.py`. It performs no Kubernetes
calls, subprocess operations or waiting; Argo native resources own persistence.
The three new deployment helpers now total **757 lines across three files**,
which exceeds both the 708-line and two-file residual exit limits even before
retained baseline scripts. This is staged implementation, not retirement credit.
Consolidation or retirement of active verification implementations is required
before cutover; do not increase the original denominator or hide embedded code.

## Current staged verifier accounting

The [v2 package](release-verifier-package.md) supersedes the three historical
helpers above and both embedded preflight/selection Python bodies. The active
staged inventory is now **two files, 708 physical lines**: 438 lines of pure
policy and 270 of bounded I/O. The inventory gate includes every staged `.py`
and refuses embedded Python. Superseded source paths have exact Git revision,
content hashes and active replacements in the
[retirement index](../deployments/release-workflow-retirements.json); installed
v1 resources remain retained without pruning.

This package correction removes no original host entrypoint. All 13 baseline
files and their supported task paths still exist; they must be replaced and
accepted before claiming the original greater-than-80% retirement. These two
modules already consume the full residual budget, so additional retained
host code or deployment helpers require further reduction or an explicitly
reviewed exception. The historical 757-line subtotal above excluded 87 embedded preflight lines
and eight embedded selection lines; the full before measurement was 852 lines.

## Retirement and verification gate

The [disposable-kind verifier integration](release-workflows-integration.md)
adds only CI test orchestration and fixtures. It changes no production Python
or supported host task: the active staged package remains two files/708 lines,
all 13 original host entrypoints remain, and no retirement credit is claimed.
Its ledger extension also reuses existing native templates without introducing
production Python or changing the 708-line/two-file residual accounting.

| Measure | Baseline | Exit criterion |
|:--|--:|:--|
| Unique deployment Python entrypoint files | 13 | At least 11 deleted; at most 2 residual deployment Python files, including any replacements. |
| Physical lines in the deployment Python cohort | 3,545 | At most 708 residual lines, including new deployment-specific Python helpers; strictly more than 80% removed. |
| Existing supported workflows | `bootstrap`, `kubernetes:prepare`, status, smoke, destroy and explicit recovery | Equivalent guarded paths or documented retained exceptions, with failure/retry and state-preservation evidence. |

For each later PR, reviewers should compare the Taskfile and operating guide
with this table, record deleted and added deployment Python paths/lines, and
check that shared helpers did not absorb the same orchestration. Count a
deleted file only after its old preview, failure and recovery behavior has an
accepted owner. The final claim requires live disposable-kind evidence as well
as `make verify`; this documentation PR has no live acceptance.

The ownership rule is [ADR 0014](decisions/0014-separate-release-and-run-orchestration.md):
Argo CD reconciles workloads; Argo Workflows owns finite release steps; the
Perfeng control plane owns Runs. Tekton or Dagger may produce immutable inputs
upstream but do not participate in the local release path by default.

The staged [schema-migration parent](release-schema-migration.md) now declares
pinned preflight/lock/intent/quiescence gates and a native selected migrator Job.
It adds no production Python and retires none of the 13 host entrypoints:
`control_plane_migration.py` still supplies the supported migration/grant/recovery
path until complete-parent live acceptance and operational cutover are reviewed.
The two-module/708-line verifier budget remains unchanged. CI-only image parity
does not establish this new parent or authorize retained-environment upgrades.

The complete schema-parent repeat acceptance adds only CI fixtures and evidence
checks while reusing the original paused current schema and existing recovery.
It changes no production verifier or supported host task and adds no retirement
credit. Complete-parent first installation, failure/recovery, live cutover and
measured deletion remain separate gates; two modules/708 lines remain unchanged.

Bounded complete-parent Job-create denial ([ADR 0051](decisions/0051-exercise-schema-job-create-denial.md))
adds CI-only Role guards and refusal/retention evidence. It changes no production
Python or supported task, and retires no host entrypoint. Two modules/708 lines
remain unchanged; operational cutover and measured deletion remain pending.


Complete-parent first-install acceptance ([ADR 0052](decisions/0052-exercise-complete-schema-first-install.md))
replaces only a CI migrator invocation and moves fixture credential creation.
Its evidence helpers are CI-only; production definitions and supported host tasks
remain unchanged. No retirement credit is claimed: 13 entrypoints remain, with
the same two-module/708-line production verifier budget. SQL failure/recovery,
operational cutover and measured deletion remain separate gates.


SQL contention acceptance ([ADR 0053](decisions/0053-exercise-schema-sql-contention.md))
adds only bounded CI fault/observation evidence around the unchanged schema parent.
It retires no host entrypoint and changes no production verifier, command or
manifest. The 13-entrypoint baseline and two-module/708-line verifier budget
remain unchanged; partial/ambiguous SQL recovery, cutover and measured deletion
remain separate gates.


Attempted-DDL rollback acceptance ([ADR 0054](decisions/0054-exercise-schema-ddl-rollback.md))
adds only CI fixture SQL/evidence around the unchanged native schema parent.
Guarded fixture cleanup does not retire a host command or authorize production
recovery. The 13-entrypoint baseline and two-module/708-line production verifier
budget remain unchanged; ambiguous commits, operational cutover and measured
retirement remain separate gates.


The [staged runtime-grants parent](release-runtime-grants.md)
([ADR 0055](decisions/0055-stage-native-runtime-grants.md)) now declares the
remaining reviewed grant transaction through a fixed native owner-only Job.
This staging adds no production Python and no retirement credit. The supported
host migration/grant command remains until complete-parent live grant acceptance
and operational cutover are approved. Initial bootstrap owner-parent first/repeat
acceptance is wired by [ADR 0056](decisions/0056-exercise-owner-runtime-grants.md),
with live proof post-merge; this CI evidence adds no retirement credit.
The [Job-create denial slice](decisions/0057-refuse-runtime-grant-job-create.md)
adds only disposable authority/refusal evidence and guarded restoration; no
supported command or production verifier source is removed.
The [grant SQL contention slice](decisions/0058-exercise-runtime-grant-sql-contention.md)
adds bounded refusal/owned-session evidence only, with live proof post-merge.
It changes no host command and adds no retirement credit.
The [attempted-GRANT rollback slice](decisions/0059-exercise-runtime-grant-rollback.md)
adds only CI fault/cleanup evidence and retains both failed SQL Jobs. Live proof
remains post-merge; no host command or production verifier source is retired.
The [owner-correction slice](decisions/0060-exercise-owner-grant-correction.md)
replaces only one initial CI administrator call with the existing parent. It
adds no script, production definition or retirement credit; cutover stays separate.
The [adopted/paused acceptance slice](decisions/0061-exercise-grants-on-adopted-paused-application.md)
adds only CI evidence around the same grant parent before existing recovery.
No supported host command or production verifier source is retired; live proof
and operational cutover remain separate gates.
The [v2 schema admission slice](decisions/0062-stage-retained-schema-admission.md)
adds native ledger predicates and bounded byte checks, with no production Python.
It preserves bootstrap v1 and does not retire a host command. Live v2 acceptance,
general upgrades, grant-parent admission and operational cutover remain separate.
All 13 host entrypoints and the two-module/
708-line production verifier budget remain unchanged.
