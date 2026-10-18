# Local GitOps environment

## TL;DR

- Argo CD owns the sample SUT, PostgreSQL, SeaweedFS and control-plane workloads.
  SeaweedFS completed rollback, re-adoption and restart-persistence acceptance.
- Non-secret control-plane runtime inputs use lock-selected, content-addressed
  ConfigMap names, allowing new and old selections to coexist.
- Argo CD `v3.5.2` installation and controller readiness belong to the immutable
  `k8s-infrastructure` selection. This repository owns only consumer
  credentials, Applications, namespaces and workload acceptance.
- Validate both the infrastructure selection and `gitops.py` composition before
  review, then merge the GitOps files to `main` before bootstrap because the
  root Application reads `main`.
- Use `task bootstrap` for the stateful foundation, then complete the explicit
  policy and control-plane preparation before `task status` or `task smoke`.
- Set `PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN` to a fine-grained GitHub token
  with read-only Contents access to the private repositories before deployment.
- The sample SUT, PostgreSQL and SeaweedFS synchronize and self-heal automatically.
  The migration-bearing [M04 control plane](kubernetes-runtime-deployment.md) is manually synced.
  The new [native target](local-native-target.md) also synchronizes, but its initial
  self-healing is disabled. The root does not self-heal; pruning remains disabled everywhere.
- [Resource metrics](local-resource-metrics.md) use a separate restricted Project and
  immutable infrastructure chart with external verified TLS; initial self-healing is off.
- Use the [troubleshooting runbook](troubleshooting.md) to distinguish delayed
  reconciliation, persistent drift and an unhealthy Argo CD controller.

## Scope

The selected infrastructure provider installs a non-HA Argo CD control plane
into the verified `perfeng-local` kind cluster. The consumer bootstrap then
provisions one immutable private-repository credential and creates the
`perfeng-local-root` Application. The root manages
the restricted `perfeng-local` AppProject, the sample SUT and the control plane
as child Applications. It owns the existing PostgreSQL and re-adopted SeaweedFS
Helm releases without replacing their StatefulSets, PVCs, PVs, external Secrets
or data. Database migrations, runtime Secrets and ConfigMaps, and performance-Run
resources are not migrated.

The root also owns the separate resource-metrics Project/Application, without broadening
the Perfeng workload Project. Its external serving Secret and kubelet certificate setup
are not reconciled by Argo CD.

The sample SUT and control plane no longer have second Helm deployment paths:
Argo CD is their only workload reconciler. A stateless release rollback uses a reviewed
Git selection; the current control-plane schema boundary needs the coordinated
[M04 recovery procedure](kubernetes-runtime-deployment.md#authority-failure-and-rollback).
PostgreSQL and SeaweedFS retain their explicit Helm rollback commands
under ADR 0002. The migration performed no data migration or credential rotation.

Environment composition lives in Kustomize and Argo CD resources.
The [manifest layout guide](../gitops/README.md) explains the existing ownership
roots, capability-based RBAC grouping and local trust-model limitations.
`make render-check` renders all four Kustomize roots offline, rejects duplicate
ownership and rendered Secrets, and checks the staged release Project permissions
and Job ConfigMap references. Workload Helm rendering remains in the same gate.
The consumer Python boundary does not generate those resources: it requires the
infrastructure-owned `argocd` namespace, protects consumer ownership, supplies
private credentials over stdin, applies only the root Application, and performs
bounded workload health checks. The Taskfile composes that boundary with the
selected provider without generating or rewriting desired state.

## Review the pinned inputs

Validate the explicit release and local objects without accessing the network,
Docker or Kubernetes:

```powershell
uv run --locked python scripts/infrastructure_release.py validate
uv run --locked python scripts/infrastructure_runtime.py argocd validate
uv run --locked python scripts/gitops.py validate
uv run --locked python scripts/gitops.py bootstrap
```

The infrastructure commands verify the selected provider commit, compatibility
contract, official semantic Argo CD release and executable lifecycle. The
consumer validation verifies:

- root Application source, destination and non-destructive sync policy;
- sample-SUT chart source, release name, destination and sync policy;
- PostgreSQL chart source, unchanged release name, destination and conservative
  adoption policy;
- SeaweedFS chart source, unchanged release name, destination and reviewed
  stateful self-healing policy;
- control-plane chart commit, image digest, runtime resource names, registry
  mounts, destination and sync policy against the reviewed deployment lock;
- native-target chart commit, candidate digest/provenance and external pull
  Secret against its separate lock, with no initial self-healing;
- metrics chart revision against the infrastructure lock, real public CA, external Secret
  name, control-plane placement and separate kind/namespace-scoped Project;
- exact ownership of the four reviewed local namespaces;
- a least-privilege AppProject source allowlist containing exactly the
  repositories referenced by its child Applications.

Workspace membership does not imply deployment authorization. In particular,
the project-neutral `k8s-infrastructure` checkout is not a workload source for
the Perfeng workload AppProject. Metrics uses its own explicitly reviewed Project.
Adding another child Application requires reviewing its
repository access in the same change.

The preview commands never read a credential or contact the cluster. There is
no imperative workload deploy action after bootstrap; Argo CD reconciles
committed changes. Provider implementation and upgrade instructions live in
`k8s-infrastructure`; the selected release is recorded and validated through
the [local infrastructure lock](local-infrastructure-release.md).

## Fresh-cluster staged bootstrap

The fresh-cluster path deliberately separates external credential creation from
workload reconciliation. Run these stages in order after the reviewed GitOps
state reaches `main`:

1. create the cluster and apply only the reviewed namespace resources;
2. create PostgreSQL and S3 credential Secrets without running Helm;
3. create the private GHCR pull Secret;
4. bootstrap provider-owned Argo CD, then consumer credentials and Applications;
5. initialize the S3 bucket and publish the reviewed reporting policy;
6. provision database identities, run the migration and assemble immutable
   control-plane runtime inputs; and
7. require the complete GitOps health workflow and product smoke test to pass.

Task 3 provides the supported high-level entry point for stages 1 through the
S3 initialization and access probe:

```powershell
task bootstrap
```

The task creates a missing `perfeng-local` cluster or validates and reuses the
owned cluster when it already exists. It then calls the same commands shown
below. It never passes secret values in arguments and does not weaken ownership,
timeout or credential-reuse checks. The task stops at the explicit policy and
control-plane preparation boundary; complete those reviewed stages below.

The individual commands remain canonical for preview, diagnosis and partial
recovery:

```powershell
uv run --locked python scripts/infrastructure_runtime.py cluster ensure --execute
uv run --locked python scripts/cluster.py bootstrap --execute
uv run --locked python scripts/postgres.py bootstrap --execute
uv run --locked python scripts/object_store.py bootstrap --execute

$env:PERFENG_REPOSITORIES_READ_TOKEN = Read-Host `
  "GHCR package-read token" -MaskInput
uv run --locked python scripts/control_plane_registry.py deploy --execute

$env:PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN = Read-Host `
  "GitHub private-repository read token" -MaskInput
uv run --locked python scripts/infrastructure_runtime.py argocd bootstrap --execute
uv run --locked python scripts/gitops.py bootstrap --execute
uv run --locked python scripts/object_store.py initialize --execute
uv run --locked python scripts/storage_access.py --execute
```

Publish the checkout policy through the exact reviewed command in
[policy publication](policy-publication.md), then continue:

```powershell
uv run --locked python scripts/control_plane_database.py deploy --execute
uv run --locked python scripts/control_plane_migration.py deploy --execute
uv run --locked python scripts/control_plane_runtime.py deploy --execute
uv run --locked python scripts/gitops.py health --execute
uv run --locked python scripts/control_plane_smoke.py --execute
```

During the staged interval, `perfeng-local-control-plane` may be `Degraded`
because its external Secrets, database schema and selected runtime ConfigMaps do
not yet exist. The bootstrap command does not wait for or claim success for that
Application. Metrics may likewise wait for its explicit certificate prerequisites;
stateful bootstrap does not wait for it. Final GitOps health remains fail-closed and requires the root plus all six workload
Applications and every workload acceptance check.

Neither stateful bootstrap action creates a workload, PVC or bucket. Repeating
them validates and reuses the immutable credential identities without rotation.
The `initialize` action runs only after Argo CD has reconciled SeaweedFS and
creates the expected bucket if it is absent.

After the remaining control-plane prerequisites are complete, use the compact
status and acceptance entry points:

```powershell
task status
task smoke
```

`task status` requires the complete platform to be healthy and never applies
desired workload state. Its SeaweedFS semantic check creates a bounded diagnostic
Job retained for inspection. `task smoke` runs status first and then creates one
retained product Run and its evidence.

## Reconcile after merge

The root Application reads
`https://github.com/stanimirivanov/perfeng-environment.git` at `main`. Merge this
foundation before deployment so that Argo CD can find
`gitops/environments/local` in the remote repository.

If the GitOps foundation is already running, merging a child-Application change
causes the root Application to reconcile its definition automatically unless
the root is explicitly paused for maintenance. The current M04 control-plane
child has automatic sync disabled: use the
[maintenance cutover](kubernetes-runtime-deployment.md), then health. There is no
deploy command to reapply workload state.

The PostgreSQL child intentionally references, but does not own, the separately
provisioned `perfeng-postgres-auth` Secret and StatefulSet-generated PVC. Use the
staged bootstrap above on a fresh cluster. Do not run the PostgreSQL Helm
deployment command while its child Application is active.

The control-plane child owns the two versioned non-secret runtime ConfigMaps and
the workload chart in one manually synced Application. It does not generate or
own credentials, database state or migrations. Before expecting complete
health, complete the provisioning sections in
[the local control-plane guide](local-control-plane.md) through **Runtime
inputs**. Use the staged `bootstrap` action on a fresh cluster. Verify the
external resources with:

```powershell
uv run --locked python scripts/control_plane_migration.py health --execute
uv run --locked python scripts/control_plane_runtime.py health --execute
```

The Application may report `Degraded` until these prerequisites exist. Argo CD
does not replace, rotate or delete them.

For the current runtime-selection change, run `task kubernetes:prepare` from the
reviewed branch **before merging**. It stops the API/worker and pauses both root
and child before the migration, then prepares external inputs without activating
the new workload. Follow [the complete M04 cutover](kubernetes-runtime-deployment.md)
to publish policy, merge, resume the root and manually sync the child. Previous
ConfigMaps remain retained, but a Git revert alone is not a safe rollback across
cleanup migration 0018.

Use the same private-repository token used by repository validation. Create a
fine-grained personal access token restricted to the required `perfeng-*` repositories and `k8s-infrastructure`,
with repository **Contents** set to **Read-only**; GitHub adds read-only Metadata
access automatically. It needs no write permission and no Packages permission.
The classic `PERFENG_REPOSITORIES_READ_TOKEN` with `read:packages` remains a
separate GHCR credential and cannot check out private Git repositories. Store
the source token only in the current process environment:

```powershell
$env:PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN = Read-Host `
  "GitHub private-repository read token" -MaskInput
uv run --locked python scripts/gitops.py bootstrap --execute
```

If an earlier GitOps bootstrap created the immutable repository-credential
Secret from the package-only token, replace only that known owned Secret before
rerunning bootstrap. This is an explicit credential rotation; bootstrap never
deletes or silently overwrites it:

```powershell
kubectl --kubeconfig .local/perfeng-local.kubeconfig `
  --context kind-perfeng-local -n argocd delete secret `
  perfeng-github-repository-credentials
uv run --locked python scripts/gitops.py bootstrap --execute
```

Bootstrap:

1. delegates local cluster ensure and health to the selected infrastructure
   revision;
2. pre-creates the exact four consumer namespaces;
3. delegates Argo CD install and controller readiness to that same revision;
4. requires the complete `k8s-infrastructure` namespace ownership identity;
5. creates or validates an immutable Argo CD repository-credential template;
6. ownership-checks and applies the local root Application;
7. waits for the root and the sample-SUT, PostgreSQL and SeaweedFS Applications
   to become `Synced` and `Healthy`; and
8. verifies PostgreSQL health before returning at the staged boundary.

After explicit policy, database, migration and runtime preparation, `task status`
first checks provider cluster and Argo CD health, then `gitops.py health
--execute` waits for every Application and validates the sample SUT, PostgreSQL,
SeaweedFS and control-plane workloads. Consumer health never applies the Argo CD
installation, credentials, root Application or workload resources.

The root Application owns the exact four local namespaces. The AppProject does
not permit arbitrary destinations, and bootstrap may pre-create only those same
reviewed resources before Argo CD adoption.

The token is not accepted as an argument, written to a file, printed, or placed
in Git. It is sent to Kubernetes only as manifest stdin. An existing owned
credential is validated and reused without reading or rotating the environment
token. Recovery from an expired token remains explicit because the Secret is
immutable.

## Health and inspection

Run the non-destructive health workflow after bootstrap:

```powershell
uv run --locked python scripts/gitops.py health
uv run --locked python scripts/gitops.py health --execute
```

The executable consumer command validates namespace and Secret ownership,
verifies the root Application identity, requires all
six workload Applications to be both `Synced` and `Healthy`, and validates the sample
SUT, PostgreSQL, SeaweedFS, control plane and selected resource metrics. Metrics validation
delegates generic API/freshness checks to the pinned provider; it never provisions certificates.
Workload acceptance is
reconciler-neutral: it validates live resources and endpoints without expecting
a Helm release record.

The SeaweedFS health check creates one bounded, short-lived client Job for
authenticated bucket access. It does not upload an object, create a bucket,
rotate credentials or modify stored data.

Each wait names the exact Deployment, StatefulSet or Application and desired
state before it starts. An Application that narrowly misses its primary
180-second reconciliation window receives one 60-second grace period. The
command inspects status at both timeout boundaries, so a state reached while
`kubectl wait` exits is accepted without another delay. Before the grace period,
it requests one normal refresh using the
[`argocd.argoproj.io/refresh` annotation](https://argo-cd.readthedocs.io/en/stable/user-guide/annotations-and-labels/).
This is a metadata mutation that requests a fresh comparison, not an explicit
sync or hard cache refresh. Existing automated-sync, pruning and self-healing
policies remain unchanged. The request has a 10-second API timeout; failure
prints a warning and does not prevent the grace wait or replace its error.

If reconciliation still fails, output includes the Application's sync and
health state, revision, operation phase, bounded condition messages and up to
20 reported out-of-sync resource identities, health states and pruning flags.
If no individual drift is reported, the output says so without treating the
Application as synchronized. Workload rollout failures
include replica counts and concise Pod readiness, restart and waiting reasons.
The diagnostic queries never read Secret resources or resource manifests,
redact common credential forms, and do not change cluster state. Only the
explicitly announced refresh request changes Application metadata.

Standard Kubernetes inspection remains available without installing the Argo CD
CLI:

```powershell
kubectl --kubeconfig .local/perfeng-local.kubeconfig `
  --context kind-perfeng-local -n argocd get applications,appprojects
kubectl --kubeconfig .local/perfeng-local.kubeconfig `
  --context kind-perfeng-local -n argocd get pods
```

Do not paste Secret YAML, decoded credentials or the initial Argo CD admin
password into an issue or diagnostic output.

## Controlled self-healing acceptance

Self-healing is enabled for the sample-SUT, PostgreSQL and SeaweedFS Applications.
The current control-plane child is manually synced with self-healing disabled.
The root does not self-heal. Pruning remains
disabled everywhere.

After this change reaches `main` and GitOps health passes, introduce one bounded,
reversible replica drift in the stateless sample SUT:

```powershell
kubectl --kubeconfig .local/perfeng-local.kubeconfig `
  --context kind-perfeng-local -n perf-sut patch deployment sample-sut `
  --type merge --patch '{"spec":{"replicas":2}}' -o jsonpath='{.spec.replicas}'
kubectl --kubeconfig .local/perfeng-local.kubeconfig `
  --context kind-perfeng-local -n perf-sut wait `
  --for=jsonpath='{.spec.replicas}'=1 deployment/sample-sut --timeout=180s
uv run --locked python scripts/gitops.py health --execute
```

The patch response must show `2`; the wait succeeds only after Argo CD restores
the reviewed value `1`. The final health check requires all Applications to be
`Synced` and `Healthy` and both workloads to pass their existing acceptance
checks. Do not use the control plane for deliberate drift: the stateless sample
SUT proves the same reconciliation policy without interrupting the Run API.

## Sample SUT ownership and rollback

The child Application renders the existing `charts/sample-sut` chart from this
repository with Helm release name `sample-sut` into `perf-sut`. It does not copy
the chart or invent a second set of values. The root Application also owns the
four reviewed namespace resources and reconciles them before workload
Applications. `cluster.py bootstrap` may pre-create those exact resources for
credential bootstrap, but it does not deploy the sample SUT.

Recover a bad sample-SUT change by reverting the reviewed chart or Application
change in Git and waiting for `perfeng-local-sample-sut` to reconcile. Do not
manually run Helm beside Argo CD. Removing the Application entirely is a
separate environment-composition change, not a routine rollback procedure.

## PostgreSQL ownership and adoption

The reviewed PostgreSQL child renders `charts/postgres` with the unchanged Helm
release name `postgres` into `perf-platform`. Its policy has automated
synchronization and self-healing enabled while pruning remains disabled.
The chart continues to reference the separately provisioned immutable Secret
and generates the same retained `data-postgres-0` PVC through the same
StatefulSet claim template.

Before merging the first adoption, record the non-secret StatefulSet, PVC, PV
and Secret identities and create the persistence probe described in
[ADR 0002](decisions/0002-stateful-gitops-ownership.md). After reconciliation,
require unchanged identities, PostgreSQL health and the original probe value.
Then restart only the StatefulSet and verify that the same probe still exists.
An Application reporting `Healthy` is not sufficient evidence if an identity
or binding changed.

The initial adoption and its rollback rehearsal both passed with unchanged
StatefulSet, PVC, PV and Secret identities. The persistence probe survived an
adoption restart, orphan propagation, Helm reassertion and a rollback restart.
This revision reactivates the exact reviewed child manifest without changing its
chart, release name or conservative sync policy.

The re-adoption also passed with unchanged identities and probe through another
restart. This revision enables self-healing and makes the chart's rolling-update
partition explicit at `0`. After merge, test only that safe field by changing it
to `1` and waiting for Argo CD to restore `0`:

```powershell
kubectl --kubeconfig .local/perfeng-local.kubeconfig `
  --context kind-perfeng-local -n perf-platform patch statefulset postgres `
  --type merge --patch '{"spec":{"updateStrategy":{"rollingUpdate":{"partition":1}}}}'
kubectl --kubeconfig .local/perfeng-local.kubeconfig `
  --context kind-perfeng-local -n perf-platform wait `
  --for=jsonpath='{.spec.updateStrategy.rollingUpdate.partition}'=0 `
  statefulset/postgres --timeout=180s
uv run --locked python scripts/gitops.py health --execute
```

This drift test does not change replicas, the Pod template, credentials or
storage. Finish by verifying the recorded identities and probe. Do not run
`scripts/postgres.py deploy --execute` while the child is active.

## SeaweedFS ownership and adoption

The reviewed SeaweedFS child renders `charts/seaweedfs` with the unchanged
release name `seaweedfs` into `perf-platform`. Its policy enables synchronization
and self-healing while keeping pruning disabled. Its server and client
credentials stay outside direct Argo CD ownership, and the retained PVC
continues to be generated by the existing StatefulSet claim template.

The initial adoption, rollback rehearsal and re-adoption all passed with
unchanged StatefulSet, PVC, PV and credential Secret identities. The original
S3 probe survived each restart, orphan propagation and Helm reassertion with its
expected checksum. This revision enables self-healing and makes the chart's
rolling-update partition explicit at `0`.

After this change reaches `main` and normal GitOps health passes, test only that
safe field by changing it to `1` and waiting for Argo CD to restore `0`:

```powershell
kubectl --kubeconfig .local/perfeng-local.kubeconfig `
  --context kind-perfeng-local -n perf-platform patch statefulset seaweedfs `
  --type merge --patch '{"spec":{"updateStrategy":{"rollingUpdate":{"partition":1}}}}'
kubectl --kubeconfig .local/perfeng-local.kubeconfig `
  --context kind-perfeng-local -n perf-platform wait `
  --for=jsonpath='{.spec.updateStrategy.rollingUpdate.partition}'=0 `
  statefulset/seaweedfs --timeout=180s
uv run --locked python scripts/gitops.py health --execute
```

The drift test does not change replicas, the Pod template, credentials or
storage. Finish by verifying the recorded StatefulSet, PVC, PV and Secret
identities and the original S3 probe checksum. Do not run
`scripts/object_store.py deploy --execute` while the child is active.

## Control-plane ownership and rollback

The control-plane child renders `charts/control-plane` from the exact
`perfeng-k8s` commit selected in `deployments/local-control-plane.json`. Its
values use the selected image digest and name the immutable runtime resources
assembled from that same reviewed release. Argo CD owns only the chart's
Deployment, Service and service account/RBAC resources; the prerequisite
Secrets, ConfigMaps, migration Job, PostgreSQL data and SeaweedFS data stay
outside the Application.

The non-secret ConfigMap identities are derived from their exact canonical
content and recorded in the deployment lock. Changing their bytes therefore
creates a new resource rather than mutating or manually deleting the active
immutable ConfigMap. Secret identities remain stable and credential rotation
stays outside GitOps.

The superseded control-plane Helm deployment command was removed after the
GitOps path passed release, smoke and post-restart acceptance. The current M04
selection adds a schema boundary: pause first and follow
[coordinated recovery](kubernetes-runtime-deployment.md#authority-failure-and-rollback).
Do not start a pre-cleanup worker against migration 0018 with a Git revert alone.
Retain external Secrets, runtime resources, migration Jobs, database and object-store
data. Removing the Application or returning it to Helm is not routine rollback.

The control-plane child sets `CreateNamespace=false` deliberately. The
externally provisioned prerequisites create and validate `perf-platform` before
the workload is reconciled.

## Safety and limitations

Neither provider nor consumer bootstrap adopts an `argocd` namespace with an
unknown owner. Consumer bootstrap does not install Argo CD, rotate an existing
credential, enable pruning, self-heal the root Application, expose the Argo CD
API server, or install a host-side Argo CD CLI. There is deliberately no
uninstall command in this repository.

The standard non-HA installation is appropriate for the disposable local
environment, not production. Production topology, SSO, external secrets,
network exposure, backups, high availability and controller resource sizing
remain undecided.

The first immutable promotion moves the required HTTP listener policy from a
chart-level override into the complete runtime ConfigMap. The deployment lock
selects chart `0.4.0` and the new content-addressed ConfigMap together. Operators
pre-stage that ConfigMap before merge; Argo CD then rolls out the new selection
without mutating or deleting the previous one. Reverting the Git selection
restores the prior chart and ConfigMap identity.

[ADR 0002](decisions/0002-stateful-gitops-ownership.md) defines the required
retention, unchanged-identity and rollback acceptance for stateful adoption.
PostgreSQL and SeaweedFS completed adoption, rollback rehearsal, re-adoption and
controlled stateful self-healing. Pruning remains disabled pending a separate
retention and decommissioning decision.

## Upgrading Argo CD

Upgrade Argo CD in `k8s-infrastructure`, merge and validate that provider
release, then promote its immutable commit through
`deployments/local-infrastructure.json`. Never edit the consumer lock to point
at `stable`, `latest` or a branch. Review the official release notes and rendered
component image tags before promotion, then repeat the after-merge acceptance
from the infrastructure release guide.

References:

- [Argo CD installation](https://argo-cd.readthedocs.io/en/stable/operator-manual/installation/)
- [Argo CD declarative setup](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/)
- [Argo CD private repositories](https://argo-cd.readthedocs.io/en/stable/user-guide/private-repositories/)
- [ADR 0001](decisions/0001-gitops-environment-composition.md)
- [GitOps migration completion](gitops-migration-completion.md)
