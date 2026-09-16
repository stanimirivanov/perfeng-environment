# Cloud Kubernetes options under a zero-cost constraint

- Status: Architecture evaluation
- Evaluated: 2026-09-16
- Pricing basis: Public list pricing and free-tier documentation; verify again
  immediately before provisioning

## TL;DR

- No major provider offers an unrestricted, useful managed Kubernetes platform
  that can be assumed to remain free.
- OCI OKE Basic with Ampere A1 workers is the only credible ongoing zero-cost
  candidate for this platform, subject to account allowance, regional capacity,
  Arm-compatible images and strict cost controls.
- The current Perfeng runtime is `linux/amd64`; OCI's viable free workers are
  Arm. Multi-architecture releases are therefore a hard prerequisite.
- GKE waives one cluster-management fee but charges usable compute. AKS waives
  control-plane management but charges required nodes. EKS charges for the
  control plane before worker costs.
- Credits and 12-month offers are useful for a time-boxed experiment but do not
  satisfy an ongoing zero-cost requirement.
- Keep kind as the supported environment. Build a provider-independent
  infrastructure boundary first, then attempt a guarded OCI pilot.

## What “free” means for this evaluation

An option qualifies as ongoing zero-cost only when its normal small deployment
is expected to produce no invoice after introductory credits expire. A free
control plane with billed workers does not qualify. Neither does an architecture
that depends on remembering to destroy resources before a trial ends.

“Expected zero” is not a guarantee. Providers may change allowances, capacity
can disappear, metered network traffic can exceed a threshold and billing data
can be delayed. A candidate therefore also needs:

- a checked-in allowlist of eligible resource types and maximum sizes;
- a plan-time rejection of unapproved billable resources;
- a dedicated account/project/compartment and least-privilege identity;
- resource quotas where the provider supports them;
- budget and anomaly alerts as secondary detection;
- a manual apply and separate explicit destroy operation; and
- post-apply inventory and billing verification.

Budgets are not spending caps. OCI describes budgets as soft limits evaluated
periodically, and Azure states that budget notifications do not stop resources.

## Provider comparison

| Provider option | Control plane | Workers and supporting resources | Ongoing zero-cost assessment |
|:--|:--|:--|:--|
| OCI OKE Basic + Ampere A1 | Basic OKE is listed as free. It provides core Kubernetes without the enhanced-cluster SLA and features. | OCI publishes ongoing A1 compute, block-volume, object-storage and one small load-balancer allowance. Capacity is not guaranteed, idle free instances may be reclaimed and the allowance differs between Always Free-only and paid tenancies. | **Candidate.** Viable only after Arm images, allowance validation, capacity success and cost/security hardening. |
| Google GKE zonal Standard | The monthly GKE credit covers one zonal Standard or Autopilot cluster management fee. | Compute, networking and storage are excluded. Google's ongoing Compute Engine allowance is one `e2-micro` VM with 1 GB-class capacity, which is not a credible host for Argo CD plus the Perfeng services. | **Not continuously free for Perfeng.** Good portability target with a small explicit budget or short-lived nodes. |
| Google GKE Autopilot | The same cluster-management credit applies. | Running Perfeng Pods are metered by requested or provisioned compute. Persistent PostgreSQL, SeaweedFS, Argo CD and control-plane workloads do not scale to zero as a complete environment. | **Not continuously free.** Convenient for a time-boxed paid integration test. |
| Azure AKS Free tier | Cluster management is free and has no financially backed SLA. | Azure charges consumed VM, storage and networking resources. Current AKS system pools require at least two nodes and a supported SKU with at least 4 vCPU and 4 GB per node; B-series VMs are not supported for system pools. | **Not continuously free.** The word “Free” applies to management, not the required compute substrate. |
| Amazon EKS | Standard-support clusters cost USD 0.10 per cluster-hour; extended support costs more. | EC2/Fargate, storage, public IPv4 and network costs are additional. New-account credits are temporary. | **Not a zero-cost candidate.** Use only after an explicit budget is approved. |

Prices, allowances and service rules above are current as of the evaluation date.
They are selection inputs, not immutable project facts.

## OCI candidate in detail

### Why OCI remains the best candidate

OCI combines a free Basic OKE control plane with an ongoing Ampere A1 allowance.
Oracle also documents Always Free block volume, object storage and a small load
balancer. That is enough in principle for a compact integration environment,
whereas the other major providers charge for the minimum practical worker
capacity.

Oracle's current documentation distinguishes account models:

- an Always Free-only tenancy receives 1,500 A1 OCPU-hours and 9,000 GB-hours
  each month, described as 2 OCPUs and 12 GB continuously; and
- the OCI price list states that a paid tenancy receives the first 3,000 A1
  OCPU-hours and 18,000 GB-hours each month without charge.

Account eligibility must be read from the actual tenancy before planning. The
larger paid-tenancy allowance must never be assumed from the repository.

Oracle explicitly recommends trying another availability domain, waiting, or
upgrading the account when an Always Free shape reports out-of-host capacity.
It also says eligible Always Free usage remains free after an upgrade. Upgrading
is therefore a reasonable manual experiment, but it is not a capacity guarantee
and it enables chargeable usage above the allowance.

### Current blockers

The complete Perfeng platform cannot be deployed to A1 today:

- the k6 and analysis identities select `linux/amd64`;
- execution definitions contain `kubernetes.io/arch: amd64` selectors;
- image verification in the control-plane and analysis repositories rejects
  non-amd64 images; and
- the local runtime bundle describes a Windows/amd64 environment.

Every executable container and relevant third-party dependency must publish and
verify a `linux/arm64` image. Architecture becomes part of the immutable release
selection. QEMU emulation is unsuitable because it changes performance and adds
an opaque failure mode.

The existing
[`oci-oke-terraform`](https://github.com/stanimirivanov/oci-oke-terraform)
repository was inspected at revision
`fd9db9213cf3732b416bfea001acb2e91bf3378b`. It is valuable source material but
is not safe to apply as the Perfeng cloud foundation yet:

- its two workers request 1 OCPU and 8 GB each, or 2 OCPUs and 16 GB total;
  that exceeds the documented 12 GB Always Free-only allowance;
- the README claims 4 OCPUs/24 GB and automated Tekton installation, while the
  reviewed Terraform requests different capacity and contains no Tekton
  resource;
- Kubernetes and node images are selected dynamically rather than pinned;
- the OCI provider uses an open-ended minimum version;
- the Kubernetes API allows TCP 6443 from `0.0.0.0/0`;
- worker NodePort ingress allows every protocol from `0.0.0.0/0`;
- a push to `main` can apply infrastructure automatically; and
- there is no fail-closed free-allowance policy before apply.

Those are migration inputs, not reasons to discard the repository history.

### Recommended zero-cost profile

The first OCI profile should be intentionally small:

- one OKE Basic cluster in the tenancy's home region;
- A1 managed nodes only, with CPU and memory derived from the verified account
  allowance rather than a claimed universal free size;
- no virtual nodes, enhanced cluster or paid fallback shape;
- boot and persistent volumes kept within the verified 200 GB aggregate free
  block allowance;
- at most the documented free load balancer, created only if ingress is needed;
- no NAT gateway or other resource unless its zero-cost status is proven;
- API access restricted to reviewed source CIDRs or a deliberate private-access
  path;
- no public all-protocol NodePort ingress;
- a dedicated compartment, resource tags, quotas and least-privilege IAM;
- a very small budget plus alerts, clearly labelled as delayed detection; and
- an inventory and cost check immediately after apply and on a schedule.

Even if it fits, this is an integration environment. A load generator, SUT,
database, object store, controllers and analysis Jobs sharing two small workers
produce noisy, capacity-constrained measurements. The environment can prove
deployment, contracts and end-to-end behavior; it cannot establish a trustworthy
performance baseline for another system.

## Alternatives that do not satisfy the requirement

### Self-managed k3s on free VMs

A single-node k3s cluster can reduce control-plane overhead, but it does not fix
the underlying free-compute limit. On OCI it still depends on scarce A1 capacity
and Arm-compatible images. On Google Cloud the ongoing free VM is too small for
the complete platform. Self-management also adds upgrade, backup and control
plane recovery work while providing less managed-Kubernetes coverage.

It may be useful for a tiny generic application, but OKE Basic is the better
Perfeng experiment when A1 capacity exists.

### Trial credits

Google Cloud's welcome credit, AWS's new-customer credits and Azure's introductory
offers can fund a short compatibility exercise. They expire and therefore must
be modelled as a time-boxed experiment with an automatic expiry/destroy date,
not as the platform's cloud environment.

### Free control plane with nodes scaled to zero

GKE Standard and AKS can minimize idle cost, but a complete Perfeng environment
has controllers and stateful services that require nodes. Scaling to zero turns
the environment off and may leave storage or networking charges. It is a useful
cost optimization for an explicitly billable ephemeral environment, not an
ongoing free deployment.

### Community sandboxes

Developer sandboxes and hosted playgrounds commonly restrict cluster-wide
controllers, persistent storage, network exposure, lifetime or administrative
access. They are useful for learning but cannot exercise this environment's
Argo CD, stateful persistence and recovery contracts reliably.

## Recommended sequence

1. Create `kubernetes-platform-infrastructure` and extract the proven local
   kind and Argo CD substrate without changing behavior.
2. Define and validate the substrate capability boundary consumed by
   `perfeng-environment`.
3. Import the OCI Terraform as a plain pinned provider stack; do not carry its
   current automatic apply or add an infrastructure-engine migration.
4. Add multi-platform image builds and immutable `linux/arm64` identities in
   the component repositories.
5. Harden the imported OCI history without applying it.
6. Inspect the OCI tenancy's account type, current A1 allowance, home-region
   capacity and existing resource consumption.
7. Decide manually whether to remain Always Free-only or enable Pay As You Go.
8. Run a no-apply OCI plan that proves every selected resource is on the
   reviewed allowlist and below its allowance.
9. Apply manually, verify the provider inventory and billing view, then deploy a
   cloud integration overlay from `perfeng-environment`.
10. Run health and smoke acceptance, label the results as integration evidence,
   and destroy the pilot if zero-cost eligibility or capacity cannot be proven.

GKE or AKS should be the second provider only when a small recurring budget is
acceptable. EKS should not be prioritized for the zero-cost objective.

## Sources

- [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [OCI public price list](https://www.oracle.com/cloud/price-list/)
- [OCI Basic and Enhanced clusters](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contengcomparingenhancedwithbasicclusters_topic.htm)
- [OCI compute quotas](https://docs.oracle.com/en-us/iaas/Content/Quotas/Concepts/resourcequotas_topic-Compute_Quotas.htm)
- [OCI budgets](https://docs.oracle.com/en-us/iaas/Content/Billing/Concepts/budgetsoverview.htm)
- [Google Cloud Free Program](https://cloud.google.com/free/docs/free-cloud-features)
- [GKE pricing](https://cloud.google.com/kubernetes-engine/pricing)
- [Google Compute Engine E2 machine types](https://cloud.google.com/compute/docs/general-purpose-machines#e2_machine_types)
- [AKS cluster-management tiers](https://learn.microsoft.com/azure/aks/free-standard-pricing-tiers)
- [AKS system node pools](https://learn.microsoft.com/azure/aks/use-system-pools)
- [Azure free services](https://azure.microsoft.com/pricing/free-services/)
- [Azure budget behavior](https://learn.microsoft.com/azure/cost-management-billing/costs/quick-create-budget-bicep)
- [Amazon EKS pricing](https://aws.amazon.com/eks/pricing/)
- [AWS Free Tier plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)
