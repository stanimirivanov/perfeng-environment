# ADR-0004: Separate reusable Kubernetes infrastructure from Perfeng composition

- Status: Accepted
- Date: 2026-09-16
- Milestone: M06 — Production Readiness
- Deciders: Project owner
- Supersedes:
- Superseded by:

## TL;DR

- Create `kubernetes-platform-infrastructure` as a project-neutral repository for
  Kubernetes substrates, Argo CD installation, cluster capability validation and
  cloud cost guardrails.
- Keep `perfeng-environment` as the Perfeng integration and distribution
  repository: it owns workspace navigation, the cross-repository roadmap,
  environment composition, release selection and end-to-end acceptance.
- Define a small cluster-capability boundary instead of a provider-neutral
  Terraform abstraction. Local kind and each cloud provider retain explicit
  implementations.
- Extract local infrastructure incrementally and preserve its source history;
  do not copy everything or change live ownership in one pull request.
- Treat OCI OKE Basic on Ampere A1 as the first cloud candidate, not as an
  approved deployment. Current Perfeng images and execution bundles require
  `linux/amd64`, while the viable OCI free compute is Arm-based.
- Require an explicit zero-cost preflight, manual apply approval and post-apply
  cost verification. Provider budgets are alerts, not hard spending caps.

## Context

The completed local GitOps migration established Argo CD as the sole workload
reconciler, but `perfeng-environment` still contains two different kinds of
responsibility:

1. reusable infrastructure that can create or qualify a Kubernetes substrate
   and install its GitOps controller; and
2. Perfeng-specific composition that selects releases, defines Applications,
   establishes application identities and proves the product path.

Keeping both together makes the Python and documentation surface look like one
large deployment system even though the responsibilities have different users,
change rates and security boundaries. It also prevents another project from
reusing the proven kind, kubeconfig, ownership and Argo CD bootstrap behavior
without depending on Perfeng-specific workloads.

Cloud deployment adds another reason to separate the concerns. VPCs, managed
control planes, node pools, storage classes, IAM, cost controls and kubeconfig
delivery are infrastructure. PostgreSQL identities, S3 application credentials,
runtime registry selection, policy publication and performance acceptance are
properties of the deployed product environment.

The accompanying
[cloud Kubernetes evaluation](../cloud-kubernetes-options.md) concludes that a
useful, continuously running managed Kubernetes cluster is not generally
available at zero ongoing cost across the major providers. OCI is the only
credible candidate in the accounts under consideration, and it has capacity,
architecture and billing constraints that must be proven before use.

## Decision

### Create one project-neutral infrastructure repository

Create a repository named `kubernetes-platform-infrastructure`. Its purpose is
to provide small, qualified Kubernetes substrates for multiple projects. It
owns:

- local kind cluster creation, identity, kubeconfig handling and destruction;
- provider-specific Terraform or OpenTofu stacks for networks, managed
  Kubernetes control planes, node pools, storage and cluster access;
- pinned Argo CD installation and controller-readiness validation;
- generic cluster capability and conformance checks;
- provider IAM needed to create and operate the substrate;
- cost-policy checks, quotas where supported, budget alerts and resource tags;
- explicit infrastructure plan, apply, status and destroy workflows; and
- provider-specific recovery documentation.

It does not own Perfeng namespaces, workload charts, release locks, database or
S3 application users, runtime registries, performance policies, Runs, baselines
or acceptance evidence.

The repository name is deliberately not `perfeng-infrastructure`. The intended
consumer set is broader than Perfeng. Reuse is still earned: only the existing
kind and Argo CD boundaries are extracted initially, and a cloud provider is
added only when its implementation is exercised.

### Keep provider implementations explicit

The infrastructure repository exposes a common capability description and
operator vocabulary, not a lowest-common-denominator Terraform module. The
initial layouts are expected to be independently understandable:

```text
clusters/
  local-kind/
  oci-oke/
```

Possible later stacks such as `gcp-gke`, `azure-aks` or `aws-eks` are added only
after a funded, testable use case exists. They may share validation code and
policy, but their network, identity, storage, upgrade and cost models remain
provider-specific.

The initial implementation keeps the proven tool boundaries:

- Task provides the small operator-facing command surface;
- typed Python managed by `uv` performs cross-platform identity, ownership and
  safety validation that shell commands cannot express clearly;
- plain, version-pinned Terraform provisions provider infrastructure;
- Kustomize installs the pinned Argo CD distribution; and
- Argo CD reconciles generic cluster add-ons and each consumer's separately
  owned environment root.

Python does not generate provider or workload desired state. The extraction does
not simultaneously migrate to OpenTofu, Pulumi or Crossplane. Terragrunt is not
carried into the first provider stack unless multiple independently deployed
Terraform roots demonstrate an orchestration need.

The infrastructure/environment handoff records only non-secret capabilities:

- Kubernetes version and supported node architectures;
- dedicated kubeconfig/context identity delivered outside Git;
- default or named dynamic `ReadWriteOnce` StorageClass;
- DNS and outbound-network expectations;
- Argo CD version, namespace and readiness;
- optional ingress, load-balancer and external-secret capabilities; and
- substrate revision and provider profile.

The first extraction documents this interface. It does not introduce a new
published cross-repository schema until two implementations demonstrate that a
machine-readable contract is useful.

### Reduce `perfeng-environment` to product composition and integration

`perfeng-environment` remains the entry point for assembling and validating the
Perfeng distribution. It owns:

- `workspace.json`, the editor workspace and the authoritative project roadmap;
- Perfeng AppProjects, namespaces, root and child Applications;
- environment overlays and immutable component release selections;
- application-specific external Secret identities and initialization;
- database migrations, runtime inputs and policy publication orchestration;
- environment-specific compatibility and health validation; and
- end-to-end smoke, persistence and product acceptance entry points.

This keeps ADR 0003 in force: the Perfeng roadmap does not move into a generic
infrastructure repository. Workload packaging and runtime bundles remain in
`perfeng-k8s`; component behavior remains in component repositories; schemas
remain in `perfeng-contracts`.

Some current acceptance scripts may later move to the component that owns the
behavior or to a dedicated acceptance repository. That is a separate ownership
decision and is not required to extract the Kubernetes substrate.

### Use OCI only after architecture and cost gates pass

OCI OKE Basic with Ampere A1 managed nodes is the first cloud candidate because
the Basic control plane is free and OCI publishes ongoing free A1, block-volume,
object-storage and load-balancer allowances. It is not selected as a supported
environment yet.

The candidate is blocked by the current `linux/amd64` runtime contract. Perfeng
execution and analysis identities, node selectors and image verifiers must
support a reviewed `linux/arm64` variant before an A1 deployment can run the
complete platform. Emulation is not accepted for performance evidence.

The existing `oci-oke-terraform` repository is a source-history donor, not an
implementation to apply unchanged. Before a live pilot, its useful history is
imported and the OCI stack must:

- pin Terraform/OpenTofu, providers, Kubernetes and node images;
- reconcile requested A1 CPU and memory with the account's current allowance;
- remove unrelated Tekton claims and resources;
- restrict the public API endpoint and remove public all-protocol NodePort
  ingress;
- make billable resource classes fail validation by default;
- establish compartment quotas and budget alerts where supported;
- replace automatic apply-on-main with a reviewed manual apply boundary;
- keep destroy separate, exact and explicitly approved; and
- verify the provider bill and resource inventory after every live change.

Converting an OCI account to Pay As You Go may improve access to resource types
while retaining eligible free allowances, but it also permits billable overage.
It is therefore an operator choice, not an automated recovery from capacity
failure. No budget alert is treated as a spending cap.

### Preserve Git history and migrate incrementally

Extraction uses history-preserving filtered imports from
`perfeng-environment` and, for the OCI stack, `oci-oke-terraform`. Each cutover is
a separate pull request:

1. create the infrastructure repository and contributor harness;
2. define the capability boundary and import local kind lifecycle code;
3. import pinned Argo CD bootstrap and readiness checks;
4. make `perfeng-environment` consume a reviewed infrastructure release;
5. remove only the superseded source after local acceptance passes;
6. harden and import the OCI provider stack;
7. add multi-architecture component releases in their owning repositories; and
8. run a manually approved OCI plan and live integration pilot.

Temporary duplication is allowed only between an import and its verified
cutover. There must never be two active owners for one cluster or Argo CD
installation.

## Alternatives considered

### Keep infrastructure in `perfeng-environment`

- Benefits: no new repository or cross-repository release dependency.
- Costs and risks: unrelated projects cannot reuse the substrate; provider and
  product concerns continue to expand together; cloud IAM and product secrets
  remain harder to separate.
- Reason not selected: the completed GitOps migration exposed a stable,
  independently useful substrate boundary.

### Create `perfeng-infrastructure`

- Benefits: naming matches the existing polyrepo and makes ownership obvious.
- Costs and risks: the name encourages Perfeng-specific assumptions in code that
  is intended for other projects.
- Reason not selected: the infrastructure contract is Kubernetes substrate and
  controller bootstrap, not performance-engineering behavior.

### Put all providers behind one Terraform module

- Benefits: one nominal interface and less visible duplication.
- Costs and risks: provider IAM, networking, storage, upgrades and cost controls
  differ materially; abstraction would hide important safety choices.
- Reason not selected: share policy and tests first, and abstract only repeated
  behavior demonstrated by real implementations.

### Replace Terraform during extraction

- Benefits: OpenTofu, Pulumi or Crossplane may offer licensing, language or
  reconciliation advantages in a mature infrastructure platform.
- Costs and risks: combines repository extraction, provider behavior and state
  compatibility in one migration; Crossplane also requires the cluster it would
  provision around.
- Reason not selected: retain pinned Terraform for the first cutover and assess
  another engine only against a concrete limitation.

### Extend only `oci-oke-terraform`

- Benefits: reuses an existing repository and provider-specific work.
- Costs and risks: does not extract the local foundation, embeds unrelated
  Tekton intent and cannot define the project-neutral handoff.
- Reason not selected: import its useful history into the broader repository
  after hardening instead of making it the platform boundary.

### Move the Perfeng roadmap into the infrastructure repository

- Benefits: one new central repository appears to own cross-project work.
- Costs and risks: a generic substrate repository would own product priorities
  it neither implements nor releases.
- Reason not selected: ADR 0003 remains correct; roadmap ownership stays with
  the Perfeng integration repository.

## Consequences

### Positive

- Local and cloud substrates gain one explicit owner and lifecycle.
- Other projects can consume a qualified cluster without importing Perfeng.
- Cloud credentials and provider state are separated from product identities.
- `perfeng-environment` becomes smaller and easier to explain.
- Provider cost and security constraints remain visible in provider-specific
  code and review.

### Negative

- Bootstrap now crosses a versioned repository boundary.
- A new repository, release process and compatibility check must be maintained.
- History-preserving extraction requires careful sequencing.
- A zero-cost OCI target requires multi-architecture image work across several
  component repositories before live deployment.

### Neutral or follow-up

- Local kind remains the supported environment during extraction.
- M06 remains deferred as a production claim. A free cloud integration lab does
  not provide production availability, security, backup or representative
  performance results.
- GKE and AKS are useful future portability targets when a small explicit budget
  is approved. EKS is not a zero-cost target under current pricing.

## Compatibility and migration

The decision changes target ownership, not the current command surface. Existing
local commands and resource identities remain supported until their individual
cutover passes the M01 smoke and persistence checks. Kubeconfig paths, cluster
names, namespaces, Secrets, StatefulSets, PVCs and Argo CD Applications are not
renamed as part of extraction.

The first infrastructure release must support the existing
`perfeng-local`/`kind-perfeng-local` identity. `perfeng-environment` pins an
immutable infrastructure revision or release; it never tracks an unreviewed
branch for bootstrap behavior.

## Security and operations

- Cloud apply and destroy are manual, explicit and separately authorized.
- Provider credentials and Terraform state remain outside Git and outside
  Perfeng application Secrets.
- Cloud API endpoints are source-restricted; public `0.0.0.0/0` administrative
  access is not an accepted default.
- Cost eligibility is validated before apply and checked again against the
  provider inventory and billing view afterward.
- Quotas provide resource boundaries where supported. Budgets provide delayed
  alerts only and cannot guarantee a zero invoice.
- Performance results from a tiny shared free cluster are integration evidence,
  not a qualified performance baseline.

## Validation

- The extraction inventory maps every moved file to exactly one target owner.
- Local bootstrap, status, smoke and restart-persistence acceptance remain green
  before superseded source is removed.
- Infrastructure validation contains no Perfeng namespace, image, release or
  application credential.
- Environment validation fails clearly when the substrate lacks a required
  version, architecture, storage or Argo CD capability.
- OCI planning fails when it selects an enhanced cluster, non-A1 workers,
  resources above the reviewed allowance or unrestricted administrative
  ingress.
- No OCI apply is attempted until every required runtime image has a verified
  `linux/arm64` manifest and the capacity/cost preflight passes.

## References

- [Cloud Kubernetes options](../cloud-kubernetes-options.md)
- [ADR 0001: Use GitOps for environment composition](0001-gitops-environment-composition.md)
- [ADR 0003: Centralize the Perfeng project roadmap](0003-centralize-project-roadmap.md)
- [GitOps migration completion](../gitops-migration-completion.md)
- [OCI OKE Terraform source](https://github.com/stanimirivanov/oci-oke-terraform)
- [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [OCI Kubernetes Engine pricing](https://www.oracle.com/cloud/price-list/)
- [OCI Basic and Enhanced clusters](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contengcomparingenhancedwithbasicclusters_topic.htm)
- [GKE pricing](https://cloud.google.com/kubernetes-engine/pricing)
- [AKS pricing tiers](https://learn.microsoft.com/azure/aks/free-standard-pricing-tiers)
- [Amazon EKS pricing](https://aws.amazon.com/eks/pricing/)
