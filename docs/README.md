# Perfeng environment documentation map

## TL;DR

- Start with [CONTRIBUTING.md](../CONTRIBUTING.md), then use this map to read only the guidance relevant to the task.
- This repository owns workspace and environment composition; component behavior and shared contracts stay in their owning repositories.
- Offline repository validation, Helm rendering, pinned cross-repository selection, and live cluster operations are separate verification tiers.
- Update this map whenever a canonical guide moves or a recurring task lacks a clear route.

## Route by task

| Task or changed area | Read first | Verify or consult as needed |
|:--|:--|:--|
| Contributor workflow, ambiguity, or issue preparation | [Contributor guide](../CONTRIBUTING.md) | [Working agreement](../AGENTS.md) |
| Build, CI, repository policy, dependency, or local setup | [Harness guide](development/harness.md), [dependency policy](development/dependency-policy.md) | [Engineering standards](development/engineering-standards.md), `make verify` |
| Workspace composition or sibling checkouts | [Repository README](../README.md) | `scripts/workspace.py`, `workspace.json` |
| Local Kubernetes substrate or Argo CD bootstrap | [Local infrastructure release](local-infrastructure-release.md), [local Kubernetes](local-kubernetes.md) | [ADR index](decisions/README.md), specialized release-selection checks |
| GitOps composition, ownership, or recovery | [Local GitOps](local-gitops.md), [manifest layout](../gitops/README.md), [migration completion](gitops-migration-completion.md) | [Troubleshooting](troubleshooting.md), `scripts/gitops.py`, `make render-check` |
| Release workflow ownership or deployment-script retirement | [ADR 0014](decisions/0014-separate-release-and-run-orchestration.md), [retirement baseline](deployment-script-retirement.md) | [Taskfile](../Taskfile.yml), [M04 cutover](kubernetes-runtime-deployment.md) |
| Immutable release input, promotion or retry identity | [Release request](release-request.md), [ADR 0015](decisions/0015-bind-releases-to-reviewed-inputs.md) | [v1 schema](../deployments/release-request-v1.schema.json), existing deployment locks |
| Read-only Argo Workflows release preflight pilot | [Release preflight workflow](release-preflight-workflow.md), [ADR 0014](decisions/0014-separate-release-and-run-orchestration.md) | Staged GitOps manifests and focused workflow tests |
| Staged external-state preparation | [Release external state](release-external-state.md), [ADR 0014](decisions/0014-separate-release-and-run-orchestration.md) | Versioned bucket, database-role and execution-registry Jobs on disposable kind |
| Staged schema migration | [Schema migration](release-schema-migration.md), [ADR 0049](decisions/0049-stage-pinned-schema-migration.md), [repeat acceptance](decisions/0050-exercise-paused-schema-migration-repeats.md) | Pinned intent/quiescence/native Job and trusted-main repeat, [Job-create denial](decisions/0051-exercise-schema-job-create-denial.md) and [first-install](decisions/0052-exercise-complete-schema-first-install.md) and [SQL contention](decisions/0053-exercise-schema-sql-contention.md)/[DDL rollback](decisions/0054-exercise-schema-ddl-rollback.md) acceptance; local execution and cutover deferred |
| Staged runtime SQL privileges | [Runtime grants](release-runtime-grants.md), [ADR 0055](decisions/0055-stage-native-runtime-grants.md), [owner-parent acceptance](decisions/0056-exercise-owner-runtime-grants.md), [Job-create denial](decisions/0057-refuse-runtime-grant-job-create.md) | Fixed owner-only native Job, immutable SQL and quiescence; initial-job refusal/first/repeat acceptance runs post-merge; cutover deferred |
| Staged release readiness and operational health | [Release readiness](release-readiness.md), [v1 evidence schema](../deployments/release-readiness-result-v1.schema.json) | Native resource conditions, bounded probes and named read permissions |
| Staged M04 maintenance quiescence evidence | [Maintenance checkpoint](release-maintenance-checkpoint.md), [v1 schema](../deployments/maintenance-checkpoint-v1.schema.json) | Read-only paused-state/Pod absence verification; manual maintenance and recovery stay explicit |
| Staged guarded maintenance pause | [Maintenance pause](release-maintenance-pause.md), [ADR 0037](decisions/0037-stage-guarded-maintenance-pause.md), [ADR 0038](decisions/0038-exercise-maintenance-pause-and-checked-recovery.md) | Initial-job pause/manual recovery and receipt/request refusal gates, checked Pod replacement and original evidence retention; live proof pending |
| Staged explicit release adoption and current identity | [Release adoption](release-adoption.md), [ADR 0017](decisions/0017-record-explicit-local-release-adoption.md), [root refresh boundary](decisions/0036-tolerate-root-reconciliation-bookkeeping.md) | Immutable ConfigMap receipts and create-only pointer; no automatic promotion or recovery |
| Staged verifier packaging, budget and version retirement | [Verifier package](release-verifier-package.md), [ADR 0018](decisions/0018-bound-and-retire-release-verifier-sources.md) | Two modules, immutable ConfigMap, hash-audited source retirement and retained installed versions |
| Disposable-kind release integration | [CI fixture](release-workflows-integration.md) | Maintenance, ledger recovery, external-state Jobs, readiness HTTP/native observations and Git preflight child parity; [complete-parent response loss](release-workflows-integration.md#complete-parent-receipt-response-loss), [CI authorization](decisions/0019-use-ci-identity-for-registry-parity.md), synthetic inputs and no operational cutover |
| Complete preflight on trusted main | [CI fixture](release-workflows-integration.md#complete-preflight-on-trusted-main), [ADR 0020](decisions/0020-publish-disposable-ci-release-requests.md) | Run-owned temporary Git requests, actual kind UID and guarded ref cleanup; write-capable CI identity stays outside PR validation |
| Complete maintenance-checkpoint composition | [CI fixture](release-workflows-integration.md#complete-maintenance-parent-composition), [checkpoint guide](release-maintenance-checkpoint.md) | Reuse trusted-main request; verify nested preflight, observation refusal/correction and preserved fixture identities; no maintenance writes or operational cutover |
| Complete external-state composition | [CI fixture](release-workflows-integration.md#complete-external-state-parent-composition), [ADR 0021](decisions/0021-authorize-trusted-main-external-state-parity.md) | Same request/ref, approved package-read identity, sequential bucket/database/registry Jobs and partial failure recovery; transient providers only |
| Selected migrator/schema CI parity | [CI fixture](release-workflows-integration.md#selected-migration-image-parity) | Exact released image, fresh maintenance evidence, runtime-role CREATE denial and first/repeat schema identity on the initially empty fixture; no production migration authorization |
| Runtime database grant CI parity | [CI fixture](release-workflows-integration.md#runtime-database-grant-parity) | Exact supported grant transaction, runtime-only native client, missing/excess privilege refusals and repeat ACL/data preservation; server wiring remains separate |
| Selected server CI parity | [CI fixture](release-workflows-integration.md#selected-server-rollout-and-http-parity), [ADR 0022](decisions/0022-use-pinned-source-for-server-parity.md) | Actual selected chart/package/image, native Deployment/Service and production HTTP adapter; source-read token stays in pinned checkout; no full readiness or Argo CD claim |
| PostgreSQL chart CI parity | [CI fixture](release-workflows-integration.md#postgresql-chart-and-pvc-retention) | Actual chart/defaults, native StatefulSet observation, authenticated TCP and PVC/PV/canary preservation across Pod replacement; no backup/recovery claim |
| SeaweedFS chart CI parity | [CI fixture](release-workflows-integration.md#seaweedfs-chart-proxy-and-pvc-retention) | Actual chart/HAProxy, private-listener peer checks, native restart and sentinel/PVC preservation; production IAM, Argo CD and full readiness remain separate |
| Actual Argo CD CI reconciliation | [CI fixture](release-workflows-integration.md#actual-argo-cd-convergence), [ADR 0023](decisions/0023-authorize-disposable-argocd-source-access.md) | Pinned bootstrap, exact-URL private source credential, manual sync and real drift correction in disposable trusted-main CI; complete readiness/adoption remain separate |
| Complete readiness-parent CI | [CI fixture](release-workflows-integration.md#complete-release-readiness-parent), [readiness contract](release-readiness.md#complete-parent-ci-acceptance) | Actual Git preflight, foundations, Argo CD, server and HTTP composition; wrong digest/access refusals and fresh correction; no adoption/promotion or retirement claim |
| PostgreSQL Application CI adoption | [CI fixture](release-workflows-integration.md#postgresql-application-adoption), [ADR 0024](decisions/0024-authorize-environment-source-for-foundation-parity.md) | Exact environment source credential, manual pinned Application reconciliation, native/TCP checks and database/storage retention; root remains skipped |
| SeaweedFS Application CI adoption | [CI fixture](release-workflows-integration.md#seaweedfs-application-adoption) | Existing authorized source, manual pinned reconciliation, native/proxy/private-peer checks and object/storage retention; PostgreSQL remains intact and root stays skipped |
| PostgreSQL or object storage | [Local PostgreSQL](local-postgres.md), [local object store](local-object-store.md) | [Storage access](storage-access.md), [troubleshooting](troubleshooting.md) |
| Control-plane deployment or runtime inputs | [Local control plane](local-control-plane.md) | Deployment lock and pinned sibling validation |
| M04 runtime selection, migration or benchmark controller authority | [Kubernetes runtime deployment](kubernetes-runtime-deployment.md), [ADR 0013](decisions/0013-select-local-benchmark-runtime.md) | Explicit pause/prepare/manual sync; exact registry, cluster and execution credentials |
| M04 end-to-end decision, baseline approval, cancellation or restart acceptance | [Kubernetes benchmark acceptance](kubernetes-benchmark-acceptance.md) | Preview-first explicit execution, retained partial evidence and no automatic repair |
| k6, browser, or Kubernetes evidence workflows | [Local k6 run](local-k6-run.md), [Windows browser host](windows-browser-host.md), [local kube-burner](local-kube-burner.md) | Relevant tests and focused acceptance guide |
| Kubernetes benchmark qualification, pressure or interference | [Benchmark qualification](kubernetes-benchmark-qualification.md), [ADR 0011](decisions/0011-qualify-local-kubernetes-benchmarks.md) | Opt-in live preflight, pinned schema and integration-only eligibility |
| Resource Metrics API selection, TLS prerequisites or scrape health | [Local resource metrics](local-resource-metrics.md), [ADR 0012](decisions/0012-select-verified-resource-metrics.md) | Separate restricted Project, pinned chart rendering and explicit read-only metrics health |
| Windows worker HTTPS access or controlled browser target | [Native connectivity](native-worker-connectivity.md), [ADR 0006](decisions/0006-loopback-native-worker-connectivity.md) | Gateway adaptation, live preflight and final promotion gates |
| Managed native tool setup, diagnosis or session lifecycle | [Native connectivity](native-worker-connectivity.md), [ADR 0010](decisions/0010-manage-native-connectivity-workflow.md) | Explicit publisher/selection, native doctor and foreground ownership |
| Image-backed target selection, deployment or forwarding | [Local native target](local-native-target.md), [ADR 0009](decisions/0009-select-image-backed-native-target.md) | Pinned release checks, candidate health and exact HTTPS content |
| Milestones or cross-repository sequencing | [Milestones](roadmap/milestones.md) | Relevant ADRs and component repositories |
| Operational failure or live recovery | [Troubleshooting](troubleshooting.md) | The focused operating guide; live commands require explicit authorization |

## Canonical collections

For controlled target image builds/publication and qualification blockers, read
[the immutable browser candidate guide](native-browser-candidate.md).
For explicit four-core 4K cohort selection and evidence binding, read the
[Windows host guide](windows-browser-host.md#select-a-comparison-cohort) and
[ADR 0008](decisions/0008-separate-four-core-4k-browser-cohort.md).

- [Development](development/) contains engineering and harness guidance.
- [Decisions](decisions/README.md) records durable architecture and ownership choices.
- [Roadmap](roadmap/milestones.md) owns checked-in cross-repository sequencing.
- The focused local guides document prerequisites, healthy signals, recovery, and explicit execution boundaries.

## Keeping this map useful

This map routes readers; it does not restate policy. A change MUST update it when a canonical source is added, renamed, moved, or removed. `make docs-check` verifies repository-local links and policy structure, while semantic ownership still requires review.
