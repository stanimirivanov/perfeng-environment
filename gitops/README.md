# GitOps manifest organization and review boundaries

## TL;DR

- Keep environment composition as reviewed YAML and Kustomize; reusable workload packages use Helm.
- Organize RBAC by workflow identity and capability. Prefer one resource per file, allowing small Role/Binding pairs with the same lifecycle.
- Preserve Application paths, object identities, explicit release selections and manual activation boundaries when reorganizing files.
- `make render-check` renders the four Kustomize roots and checks duplicate ownership, Secret exclusion and staged release Project/ConfigMap compatibility.
- The current trust model is a dedicated local cluster. Directory structure does not establish the admission and authorization boundaries required for M06.

## Layout and ownership

| Path | Responsibility |
|:--|:--|
| `bootstrap/` | Consumer root Application, applied through explicit bootstrap. |
| `environments/local/` | Active local namespaces, AppProjects and child Applications. |
| `benchmark-authority/` | Explicit authority approved only for the dedicated local benchmark cluster. |
| `release-workflows-bootstrap/` | Separately applied, manually synced release-workflows Project/Application. |
| `release-workflows/` | Staged versioned WorkflowTemplates and immutable script ConfigMap generation. |
| `release-workflows/rbac/preflight/` | Standalone read-only preflight identity, workflow execution and cluster-identity read permissions. |
| `release-workflows/rbac/external-state/` | Composed preflight permissions plus Job creation in the two external-state namespaces. |
| `../charts/` | Existing reusable workload packages; Applications select these packages explicitly. |

The existing source paths are operational contracts. A file can move within a Kustomize root without moving an Application to a different source path or changing which Application owns an object. The release-workflows bootstrap stays outside the active local root. Do not pull it into an umbrella chart or active root as an organizational shortcut.

Applications currently have no cascading deletion finalizers, and pruning is disabled. Removing a manifest or Application does not authorize deleting its retained workloads or RBAC. Reorganization preserves these lifecycle choices; changing them requires an explicit retention and recovery review.

## File conventions

Prefer one top-level Kubernetes resource per resource-producing YAML file. A small, named Role/RoleBinding or ClusterRole/ClusterRoleBinding pair may share a file when both implement one capability for the same workflow identity and lifecycle. The four local namespaces remain a compact declarative set also used by bootstrap. These exceptions avoid a mandatory file-count rule while keeping reviewable permissions visible.

The release RBAC directories use `serviceaccount.yaml`, `workflow-access.yaml` and `cluster-identity.yaml`; external-state additionally uses `platform-jobs.yaml` and `generator-jobs.yaml`. The paired resources share a purpose; separate namespace Job grants remain separate files. The preflight and external-state ServiceAccounts remain distinct because a `templateRef` uses the invoking Workflow's ServiceAccount. The external-state identity therefore intentionally repeats the read permissions needed by composed preflight.

Keep security-sensitive differences explicit: source revisions, AppProject allowlists, destinations, RoleBinding subjects, workflow versions and sync policy. A common-label transform must not change selectors or erase ownership differences. Add an environment base/overlay when a second environment demonstrates the variation; add ApplicationSets only when repeated deployment targets justify them. Helm remains the package format for reusable workloads under [ADR 0001](../docs/decisions/0001-gitops-environment-composition.md).

## Rendering and regression checks

`make render-check` uses local `kubectl kustomize` for `environments/local`, `benchmark-authority`, `release-workflows-bootstrap` and `release-workflows`. No cluster or network access is needed. Its checks:

- reject duplicate API group/kind/namespace/name identities across those ownership roots, including duplicates expressed with different API versions;
- reject rendered Secret objects, which must remain externally supplied;
- check the staged release Application's Project identity and every rendered resource against its explicit namespaced/cluster kind allowlists and destinations; and
- check that Job ConfigMap volumes resolve to generated ConfigMaps and requested keys in the Job's namespace.

The release Project explicitly allows core ConfigMaps so its immutable script ConfigMap can synchronize. External credential Secrets remain references, not generated resources. These checks cover the current release resource kinds; they do not implement the complete Argo CD authorization engine. Keep `make workflow-lint` for Argo references and run disposable-kind acceptance before operational cutover. The supported workload render and release-selection checks remain separate.

## Current trust model and further hardening

The root currently uses Argo CD's `default` project and creates child AppProjects. Permission to create or change an AppProject is itself administrative: moving the root into a project that permits arbitrary AppProjects would not by itself prevent a child project from granting broader access. A future trust-root change must account for bootstrap, who may edit Projects/Applications, and the infrastructure provider's ownership of shared Argo CD defaults.

Benchmark authority follows `main` with automatic synchronization, without pruning or self-healing. Its broad controller permissions are approved only for the dedicated disposable cluster under [ADR 0013](../docs/decisions/0013-select-local-benchmark-runtime.md). Reducing dynamic namespace/Pod/RBAC permissions requires checking the actual benchmark controller operations and admission boundaries. Pinning or changing pruning/self-healing also requires a promotion and recovery procedure for active Runs. Directory moves do not satisfy those requirements.

The workload AppProject's destination and kind permissions should be narrowed against the complete pinned workload renders, including their execution-namespace RBAC. Pod Security Admission, Job security contexts and NetworkPolicy need image and benchmark compatibility tests. Job retention deliberately preserves failure evidence; adding a TTL requires an accepted evidence-retention policy. The resource-metrics CA has a provider-owned lifecycle and needs planned rotation. These are behavioral hardening changes, rather than automatic consequences of manifest reorganization.

The selected Argo CD version supports the current `automated.enabled` policy. Continue checking the selected version and explicit manual control-plane gate rather than changing synchronization semantics during layout cleanup. Workflow input contracts, registry proof against the selected runtime artifact and versioned child/script updates belong to the release migration and retain their [v1 compatibility boundary](../docs/decisions/0016-compose-versioned-release-templates.md).
