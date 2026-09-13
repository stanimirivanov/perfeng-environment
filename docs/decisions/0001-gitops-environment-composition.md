# ADR 0001: Use GitOps for environment composition

- Status: Accepted
- Date: 2026-09-13
- Owners: Performance platform maintainers

## TL;DR

- Use Argo CD as the target reconciler for Kubernetes environment state.
- Keep Helm for reusable packages and use Kustomize for environment overlays and
  content-addressed configuration.
- Keep cluster creation, initial credentials and Argo CD installation in a thin
  bootstrap layer exposed through Task.
- Do not use Argo CD to execute performance Runs or absorb product-specific
  artifact, analysis or Windows-worker behavior.
- Prove the design on a disposable kind cluster before replacing any current
  Python deployment path; retain the old path until Milestone 1 passes through
  the new path.
- Require a TL;DR near the beginning of every long document, as specified in
  `CONTRIBUTING.md`.

## Context

Milestone 1 proved a complete local k6 path through the control plane,
PostgreSQL, SeaweedFS, Kubernetes execution, normalization, reporting and
post-restart retrieval. The implementation also exposed an architectural
problem in this repository.

The repository describes itself as workspace bootstrap and environment
composition, but currently contains 24 Python scripts with approximately 6,700
lines. Those scripts mix at least five responsibilities:

1. workspace and kind-cluster bootstrap;
2. Kubernetes deployment and health reconciliation;
3. release-lock and cross-repository validation;
4. policy and artifact publication workflows;
5. product acceptance tests and native browser-host assembly.

The Python code was valuable executable discovery work. It established safety
rules, ownership checks, immutable identities, failure behavior and acceptance
criteria. Continuing to encode Kubernetes desired state and reconciliation in
custom subprocess orchestration would duplicate established platform tooling
and make every new component increase bespoke maintenance cost.

## Decision drivers

The target approach must:

- keep desired state declarative, reviewable and version controlled;
- select immutable Git revisions, chart versions and image digests;
- detect and correct cluster drift;
- expose deployment health and history without reading script internals;
- preserve preview-first and least-privilege behavior;
- work for a disposable local kind cluster and a future on-premises cluster;
- support private Git repositories and private OCI images without committing
  credentials;
- preserve PostgreSQL and object-store data during migration;
- keep performance execution in the performance control plane;
- avoid a big-bang rewrite or an irreversible tool commitment.

## Considered options

| Option | Strengths | Limitations | Decision |
| --- | --- | --- | --- |
| Continue custom Python orchestration | Existing behavior is tested, portable and explicit. | Reimplements reconciliation, ordering, health and drift handling; responsibilities continue to accumulate. | Retain temporarily as the migration oracle, not the target. |
| Helmfile | Small operational footprint and a declarative description of multiple Helm releases. | Primarily client-driven; does not provide an in-cluster reconciliation loop or the operational application view required for the platform. | Do not adopt as the primary reconciler. |
| Flux | Strong Kubernetes-native reconciliation, composable controllers, Helm/Kustomize support and an effective bootstrap model. | More controller- and CLI-oriented; provides less of the application-centric operational interface desired for this project. | Valid alternative, not selected. Reconsider if Argo CD proves too heavy. |
| Argo CD | Declarative Applications, continuous reconciliation, drift visibility, health, history, Helm support and a useful operator UI. | Requires an in-cluster control plane, repository credentials and a bootstrap step; cannot consume uncommitted local files. | Selected target, subject to the spike exit criteria. |

Terraform or OpenTofu is not an alternative to this decision. It may provision
future infrastructure or managed clusters, while Argo CD reconciles resources
inside an existing cluster. Argo Workflows is also out of scope: the performance
control plane already owns performance Run orchestration.

## Target responsibility model

### Argo CD owns

- namespaces, RBAC and resource policies;
- PostgreSQL, SeaweedFS and their Kubernetes services;
- the control-plane Deployment and Service;
- the sample SUT and future observability components;
- runtime registry and non-secret runtime configuration;
- deployment ordering, convergence and health;
- the exact environment selection of reviewed component artifacts.

Argo CD Applications refer to committed sources and immutable release inputs.
Automated synchronization and self-healing may be enabled per environment after
the spike proves safe behavior. Destructive pruning remains disabled until its
resource-retention behavior is explicitly tested.

### Helm and Kustomize own

Helm remains the packaging format for reusable deployable components. Component
charts belong with their deployment owner, currently `perfeng-k8s` for shared
platform workloads and this repository for environment-only fixtures during the
migration.

Kustomize composes environment overlays and generates content-hashed ConfigMaps.
Changing runtime registry bytes therefore creates a new resource identity and a
workload reference change instead of requiring an in-place update or manual
deletion of an immutable ConfigMap.

### The bootstrap layer owns

A thin, cross-platform bootstrap layer creates or selects the kind cluster,
installs the pinned Argo CD distribution, and injects only the credentials Argo
CD and the container runtime need initially. A `Taskfile.yml` provides memorable
entry points such as `task bootstrap`, `task status`, `task smoke` and
`task destroy`.

Bootstrap is necessarily outside Argo CD because a reconciler cannot install
itself into a cluster that does not yet exist. Bootstrap must remain idempotent,
bounded and substantially smaller than the deployment implementation it
replaces.

### Argo CD does not own

- cloning or updating developer workspaces;
- building or publishing component images;
- executing performance Runs;
- baseline selection, normalization or analysis;
- native Windows browser-host qualification and execution;
- ad hoc artifact upload or report retrieval;
- end-to-end product acceptance.

These behaviors remain with their product repositories, the performance control
plane, or a dedicated acceptance-test layer. They must not be disguised as Argo
CD hooks merely to remove Python from this repository.

## Secret handling

No secret value is stored in Git, Helm values, an Argo CD Application or a Task
command line. The local spike may create initial Kubernetes Secrets from
explicit environment variables. It must preserve hidden input, avoid logging
secret-bearing commands, and verify existing secret ownership before reuse.

Production secret synchronization is deferred until an external secret store is
selected. External Secrets Operator is the preferred integration boundary at
that point. Adopting it now would add a controller without a real external
provider and would not eliminate the initial local bootstrap secret.

## Source and release model

`perfeng-environment` becomes the source of truth for environment overlays,
release selections and Argo CD Applications. Component repositories publish
versioned images, charts, contracts and runtime artifacts; they do not deploy
themselves into shared environments.

Argo CD deployment sources must be pinned according to the environment's
promotion policy. Production does not track a mutable component branch. Local
development may select a reviewed feature revision, but Argo CD does not replace
the separate fast loop needed to test uncommitted source.

## Migration sequence

1. Add a pinned Argo CD bootstrap and a root local Application without changing
   the current deployment path.
2. Deploy the sample SUT as the first low-risk child Application.
3. Deploy the control plane with content-addressed runtime configuration.
4. Prove synchronization, health, drift correction and immutable promotion.
5. Migrate PostgreSQL and SeaweedFS only after retention and rollback tests are
   defined; do not recreate their persistent volumes.
6. Represent migrations and immutable policy publication as reviewed,
   content-addressed Jobs where that is safer than an external product command.
7. Run the Milestone 1 k6 and post-restart acceptance checks through the GitOps
   deployment.
8. Remove superseded Python deployment code and relocate remaining
   product-specific tools to their owning repositories.

Each step is a separate pull request with an independently reversible outcome.

## Architecture spike

The next implementation step is a disposable-cluster spike. It may add new
manifests and bootstrap commands, but it must not remove or redirect the current
working deployment path.

The spike succeeds only when it demonstrates:

- a pinned Argo CD installation on a newly created kind cluster;
- declarative root and child Applications from this repository;
- private repository access without a credential in Git or process arguments;
- healthy sample-SUT and control-plane synchronization;
- visible drift followed by controlled self-healing;
- promotion from one immutable runtime selection to another without manually
  deleting a ConfigMap;
- useful status and failure information from standard Argo CD/Kubernetes tools;
- successful Milestone 1 smoke and post-restart retrieval using the deployed
  control plane.

The spike fails, and this ADR must be revisited, if local resource use is
unreasonable, Windows bootstrap is unreliable, private-source authentication
cannot meet the security constraints, or routine local iteration becomes
materially slower or less understandable than the current path.

## Consequences

Positive consequences:

- most Kubernetes orchestration becomes declarative desired state;
- reconciliation, drift and deployment health use established controllers;
- reviewed Git changes become the deployment audit trail;
- content-addressed configuration removes manual immutable-resource promotion;
- local and future on-premises environments share the same composition model.

Costs and constraints:

- Argo CD adds cluster resource use, CRDs, upgrades and security maintenance;
- bootstrap and initial credentials still require a small external mechanism;
- private repositories require explicit repository credentials or deploy keys;
- GitOps favors committed changes and is not a hot-reload development loop;
- operators must understand both Argo CD and the underlying Kubernetes objects;
- migration temporarily supports two deployment paths.

## Rollback and supersession

Until the spike and Milestone 1 acceptance pass, the existing Python path is the
rollback path. New GitOps resources use separate, explicitly owned identities
where practical. The migration must not delete retained Jobs, object-store
evidence, database records, Secrets or persistent volume claims.

If the spike is rejected, remove only its verified disposable-cluster resources
and supersede this ADR with the selected alternative. Do not gradually introduce
Helmfile or Flux alongside Argo CD without a new decision; multiple reconcilers
would make ownership and drift behavior ambiguous.

## Documentation rule

Long documentation is rarely read linearly. Every long document across the
platform repositories must therefore provide a concise `## TL;DR` near its
beginning. The measurable threshold and required content are defined in
`CONTRIBUTING.md`. New and materially changed documents comply immediately;
existing documents are migrated deliberately rather than receiving low-value
mechanical summaries.

## References

- [Argo CD overview](https://argo-cd.readthedocs.io/en/stable/)
- [Argo CD declarative setup](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/)
- [Argo CD automated synchronization](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/)
- [Argo CD sync phases and waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/)
- [Argo CD Helm integration](https://argo-cd.readthedocs.io/en/stable/user-guide/helm/)
- [Flux core concepts](https://fluxcd.io/flux/concepts/)
- [Helmfile](https://helmfile.readthedocs.io/en/stable/)
- [Kubernetes Kustomize documentation](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/)
- [Task documentation](https://taskfile.dev/docs/getting-started/)
- [External Secrets Operator](https://external-secrets.io/)
