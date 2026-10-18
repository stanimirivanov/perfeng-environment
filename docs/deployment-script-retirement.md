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

## Retirement and verification gate

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
