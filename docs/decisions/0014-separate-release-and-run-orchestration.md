# ADR-0014: Separate release orchestration from performance Runs

- Status: Proposed
- Date: 2026-10-16
- Milestone: M06 — Production Readiness
- Deciders: Project owner and performance platform maintainers
- Supersedes: None; narrows the release boundary left open by ADR 0001
- Superseded by: None

## TL;DR

- Argo CD remains the sole reconciler of Kubernetes workload desired state.
- Argo Workflows is the target for finite, auditable **release operations** that
  cannot be expressed as continuous reconciliation: prerequisite checks,
  external-state preparation, quiescence, migration and post-release evidence.
- The Perfeng control plane alone owns performance Runs, scheduling, artifacts,
  cancellation and decisions. Argo Workflows never becomes a Run backend.
- Tekton or Dagger may produce immutable upstream artifacts. Neither is required
  in the local delivery path, and neither promotes or deploys an environment.
- Keep the M04 manual maintenance and sync boundary until an independently
  reviewed workflow proves equivalent safety and recovery behavior.

## Context

[ADR 0001](0001-gitops-environment-composition.md) established Argo CD as the
workload reconciler and excluded Argo Workflows from **performance Run**
orchestration. The completed [local migration](../gitops-migration-completion.md)
left imperative work at the edges: kind and Argo CD bootstrap, credentials,
bucket and database identities, immutable runtime inputs, schema transitions,
health and acceptance. The current [M04 cutover](../kubernetes-runtime-deployment.md)
also requires a quiescent controller and a manual child-Application sync.

Those finite release operations are a different domain from product Runs. They
currently span the Taskfile, Python commands and a lengthy operator guide. The
[retirement baseline](../deployment-script-retirement.md) counts 13 unique
Python entrypoints and 3,545 physical lines in `task bootstrap` and
`task kubernetes:prepare`. Local simplification must preserve the path to
M06's secure, recoverable and upgradeable non-local deployment.

## Decision

### One owner for each boundary

| Boundary | Owner | Contract |
|:--|:--|:--|
| Cluster creation, access and pinned Argo CD/Argo Workflows installation | `k8s-infrastructure` | Explicit substrate selection, isolated kubeconfig and removable controller lifecycle; this precedes any in-cluster release workflow. |
| Build, package and publish | Upstream component build project | Immutable image digests, chart/bundle versions, checksums and provenance; Tekton or Dagger is an optional producer implementation. |
| Environment selection and promotion | `perfeng-environment` Git review | Reviewed lock and GitOps manifests select exact upstream artifacts. No workflow silently edits or promotes Git state. |
| Continuous workload reconciliation | Argo CD | Applications, Helm/Kustomize rendering, drift, sync and health; no second controller applies the same workload resources. |
| Finite release preparation and verification | Argo Workflows | Versioned WorkflowTemplates with explicit inputs, bounded steps, service accounts, durable results and a single release-operation owner. |
| Performance execution and evidence | Perfeng control plane | Run lifecycle, admission, worker leases, cancellation, normalization, baseline decisions and retained artifacts. |

The release input is an immutable reviewed selection: environment identity,
source commit, image digests, chart/bundle versions, migration identity and
required external artifact checksums. A workflow consumes that selection; it
must reject mutable tags, a changed cluster identity, a mismatched current
selection, or a second active release of the same environment. The concrete
schema and submission mechanism belong to a later PR and require offline and
live verification before use.

```text
Upstream artifact + provenance -> reviewed environment selection
    -> release workflow preflight and external-state preparation
    -> reviewed Git promotion -> Argo CD workload sync
    -> release workflow health/recovery evidence
```

For M04, the promotion and child sync arrows remain explicit human actions.

For local kind, retain an explicit bootstrap command to create/select the
cluster and install Argo CD and Argo Workflows from pinned releases. Once those
controllers are available, reviewed WorkflowTemplates handle finite release
steps. Argo CD remains responsible for applying workload desired state and
reporting convergence. A release workflow may request or observe an authorized
Argo CD sync at a guarded point; it does not `kubectl apply` a duplicate
Deployment or turn migration Jobs into perpetual GitOps hooks.

Release workflow status is operational evidence, not the source of desired
state. A failed step must stop, retain its Job/log/output identity and leave an
explicit recovery point. Re-running a workflow may verify or reuse an already
completed external action only after ownership and content checks; it must not
rotate credentials, replay a non-idempotent migration, replace a failed Job or
delete state as a repair shortcut.

### M04 and M06 rollout boundaries

The current M04 release remains manual. In particular, keep the root and child
pause, zero-replica and no-Pod proof, schema migration through 0018, reviewed
policy publication, merge, root resume and manual child sync exactly as
documented. A workflow may first exercise an offline or disposable-cluster
candidate; production use requires evidence for quiescence, races, failed-step
recovery, retained data and the forward-repair or backup-restore path. A Git
revert alone cannot reverse migration 0018.

M06 adds the non-local prerequisites before this design is promoted: tenant
isolation/admission, SSO and service identities, external secrets and rotation,
backup/restore, retention, HA/capacity and disaster-recovery exercises. The
current benchmark controller authority is approved only for the dedicated
disposable kind cluster under [ADR 0013](0013-select-local-benchmark-runtime.md).
Installing Argo Workflows does not make that authority safe for a shared cluster.

### Artifact producer boundary

Tekton and Dagger are credible upstream build implementations. Choose one only
when the producing repository needs it and evaluate its security, maintenance
and output contract there. A producer publishes immutable artifacts and
attestations; release workflows consume their identifiers. Do not install
Tekton and Dagger alongside Argo Workflows merely to replace local shell or
Python wrappers. This keeps the local controller footprint bounded while the
release contract remains portable to a future producer choice.

## Alternatives considered

| Alternative | Benefit | Reason not selected as release owner |
|:--|:--|:--|
| Continue Task/Python orchestration | Existing safeguards and previews already work. | Every new finite release step adds custom subprocess, wait and recovery code; the operator must reconstruct state across commands. |
| Argo CD hooks and sync waves for every step | One controller and declarative ordering. | External identities, quiescence, irreversible migrations and promotion approvals need explicit finite execution and recovery state; hooks also couple them to reconciliation retries. |
| Tekton as both build and deployment orchestrator | Established Kubernetes pipeline model. | It would add another in-cluster controller to the local delivery path and overlap the chosen finite release owner. Reconsider if upstream build requirements justify it independently. |
| Dagger as the deployment orchestrator | Portable, containerized pipeline authoring. | It is an upstream/CI execution choice; it does not replace the in-cluster release state and access boundary selected here. |
| Perfeng control plane for deployment releases | Existing durable Run state. | Self-deployment would couple platform availability to its own upgrade, and performance Run semantics would acquire unrelated infrastructure authority. |

## Consequences

- A pinned Argo Workflows installation, RBAC, retention and recovery runbook add
  local footprint. The PR that introduces it must measure this against the
  script reduction and provide a removable kind installation.
- WorkflowTemplates and release inputs become reviewed deployment artifacts.
  Generic validation, migration and health steps can be reused locally and in a
  future non-local environment without importing kind-specific credentials.
- The external state boundaries remain explicit. Credential and object-store
  providers may change for M06; the release workflow contract should not expose
  local secret values or hardcode a provider implementation.
- Argo CD sync history and workflow execution history answer different questions.
  Operators need both the selected desired state and the finite operation result.

## Compatibility and migration

Follow the [measured retirement plan](../deployment-script-retirement.md).
Introduce the release-input contract, pinned controller and one low-risk
workflow slice before replacing any supported command. Preserve the existing
Taskfile path while proving parity on a disposable kind cluster. Migrate
credential/external-state and M04 maintenance slices separately; cut over
only after failure and recovery evidence. Retire at least 11 of the 13 counted
deployment Python entrypoint files without moving equivalent orchestration into
new Python modules. Product acceptance and explicit stateful rollback are
classified separately.

## Security and operations

Workflow service accounts receive only each step's required permissions. Secret
values come from an external provider or protected Kubernetes Secret and never
from Git, workflow parameters, logs or process arguments. Environment-scoped
serialization and immutable input validation guard concurrent releases.
Production promotion needs an authorized human or policy gate; a successful
workflow is not itself approval to mutate desired state or restore data.
Cluster destruction stays an explicit substrate operation, outside release
workflows. The exact M04 isolation limitation and rollback warning remain in
the operating guide.

## Validation

This PR changes no live release path. `make docs-check` and `make verify` check
repository structure and existing behavior. Review the baseline against
`Taskfile.yml`, the staged GitOps guide and the M04 cutover guide. Later PRs
must verify controller installation, immutable inputs, idempotence, parallel
release rejection, failure retention, Argo CD ownership and M04 recovery on a
disposable cluster before any operational switch.

References: [Argo Workflows WorkflowTemplates](https://argo-workflows.readthedocs.io/en/latest/workflow-templates/),
[synchronization](https://argo-workflows.readthedocs.io/en/latest/synchronization/),
and [Argo CD sync waves and hooks](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/).
