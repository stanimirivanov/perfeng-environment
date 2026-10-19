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
| Staged release readiness and operational health | [Release readiness](release-readiness.md), [v1 evidence schema](../deployments/release-readiness-result-v1.schema.json) | Native resource conditions, bounded probes and named read permissions |
| Staged M04 maintenance quiescence evidence | [Maintenance checkpoint](release-maintenance-checkpoint.md), [v1 schema](../deployments/maintenance-checkpoint-v1.schema.json) | Read-only paused-state/Pod absence verification; manual maintenance and recovery stay explicit |
| Staged explicit release adoption and current identity | [Release adoption](release-adoption.md), [ADR 0017](decisions/0017-record-explicit-local-release-adoption.md) | Immutable ConfigMap receipts and create-only pointer; no automatic promotion or recovery |
| Staged verifier packaging, budget and version retirement | [Verifier package](release-verifier-package.md), [ADR 0018](decisions/0018-bound-and-retire-release-verifier-sources.md) | Two modules, immutable ConfigMap, hash-audited source retirement and retained installed versions |
| Disposable-kind release integration | [CI fixture](release-workflows-integration.md) | Maintenance API execution plus native ledger conflicts/retention, using synthetic inputs; no operational cutover |
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
