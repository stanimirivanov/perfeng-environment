# Local platform troubleshooting

## TL;DR

- For trusted Windows-worker API/S3 access or search-target failures, follow the
  [native connectivity checklist](native-worker-connectivity.md#failure-and-recovery).
  TLS/readiness is not proof of signed upload, internal read-back or acquisition.

- Run every command from the `perfeng-environment` repository and use
  `.local/perfeng-local.kubeconfig`; the similarly named context in the global
  kubeconfig may point to an earlier kind API-server port.
- Diagnose in dependency order: Docker, kind, Argo CD, Applications, stateful
  prerequisites, control-plane workload, then the end-to-end smoke path.
- Stop at the first failing layer. A passing smoke run proves the product path,
  but it does not waive a persistent GitOps health failure.
- Use `task status` for the complete platform and the first failing underlying
  Python command for focused diagnosis or recovery.
- Commands marked **read-only** inspect or wait. Commands marked **mutating**
  intentionally change the local environment. Run no **destructive** command
  unless loss of the disposable cluster and all cluster-local data is intended.
- Never paste Secret YAML, credential environment variables, decoded tokens or
  unreviewed logs into an issue.

## Scope and command safety

For missing Kubernetes resource metrics, run **read-only** `task kubernetes:metrics-health`
and follow [the resource-metrics guide](local-resource-metrics.md). The command checks the
exact live Application/Project and public API CA, then delegates health to the pinned provider.
Missing external TLS material or unissued kubelet serving CSRs need explicit provider setup,
not Helm deployment, automatic approval or a TLS bypass. Ensure the Argo CD Contents-read
credential includes `k8s-infrastructure`. Newly generated CA values need a reviewed selection;
never rotate a Secret or edit evidence to make health pass. Final GitOps health includes
this new prerequisite; staged stateful bootstrap deliberately does not wait for it.

For M04 qualification, run **read-only** `task kubernetes:qualify` and follow the
[benchmark qualification guide](kubernetes-benchmark-qualification.md). Missing,
stale or denied Metrics API observations remain UNKNOWN, not zero load. Known
pressure, placement/capacity failures or competing workloads reject the capture.
Retain both result files and inspect the failed check. No metrics-server install,
Docker restart, credential change or workload cancellation happens implicitly.
Preflight is not a target reservation or production-performance qualification.

For native gateway failures, start with **read-only** `task native:doctor`.
It reports independent tool/checksum/build, private-state, Docker, dedicated API,
resource, port and trust checks. `RELEASE_UNSELECTED` means the first audited
serving binary has not been published/selected, not that Docker needs resetting.
`TOOL_VERSION` includes expected/observed metadata and executable path; a manual
build may print `unknown`. Do not recompile by hand or change PATH: use the
[managed setup and release selection](native-worker-connectivity.md#managed-setup).
`PORT_CONFLICT` requires inspection of the exact listener, not killing every
Caddy/kubectl process. Stop your old independent sessions before `native:up`.
`STATE_PERMISSIONS`/`HOST_SECURITY` require review of the gateway directory's
Security settings; `task native:permissions` is a separately prompted local
permission change, never an implicit doctor repair. Previously exposed CA keys
require a separate trust/CA review, not an automatic rotation. API/cluster probes
have bounded command budgets; failures retain all cluster data. `native:up`
stops its own children on failure, interruption or one-hour expiry and retains CA.


For image-backed target failures, use [the local native target guide](local-native-target.md).
Run `task native:target-health` before `task native:target`; candidate package
access, ready platform digest and UID controller ownership must agree. Restart
the target's fourth foreground forward after Pod replacement and restart an old
gateway to apply the proxy-only route. A missing target/tunnel fails closed,
never back to checkout files. Retain pull credentials, CA and rejected evidence.

For candidate image build/publication or rejected browser-host qualification,
follow the [candidate guide](native-browser-candidate.md). GUID braces/case are
equivalent power-plan identity; hardware, OS, display and calibration mismatches
still require review. Never edit a rejected report to qualify a run. Image
functional verification does not replace base/runtime advisory review.

For the four-core 4K laptop, select
`--profile profiles/browser/windows-four-core-4k.yaml` as documented in the
[host guide](windows-browser-host.md#select-a-comparison-cohort). Use it for both
the live host check and environment assembly, and match the runner's profile
identity/fingerprint. A missing selection still uses mainstream and will reject
the different hardware. OS build 22621 or background CPU 20% still rejects the
laptop profile; resolve the independent gate before making a fresh capture.
Never alter the earlier report, reuse its hash as environment evidence or mix
the two cohorts' baselines. These checks do not modify host settings.

This runbook diagnoses the supported local Windows and kind environment. It is
not a production incident procedure. Start PowerShell in the repository root:

```powershell
Set-Location C:\Source\perf-eng\perfeng-environment
$kubeconfig = (Resolve-Path .local/perfeng-local.kubeconfig).Path
```

The labels used below mean:

| Label | Meaning |
|---|---|
| **read-only** | Reads local or cluster state, or waits for a condition |
| **mutating** | Creates, restarts or reconciles an explicitly named local resource |
| **destructive** | Deletes durable or broad local state |

Preview-first repository commands remain previews until `--execute` is supplied.
The cluster command does not deploy workloads. The control plane has no separate
Helm deployment command; Argo CD is its only workload reconciler. Use
`control_plane_health.py --execute` for its focused read-only check.

## Operator task boundaries

Use `task bootstrap` for a fresh or already-owned local cluster, then complete
the policy and control-plane preparation documented in the
[GitOps guide](local-gitops.md). Use `task status` only when checking the complete
platform: it is expected to fail during the intentional staged-bootstrap gap.
Use the individual Python command from the first failing layer below when a task
needs diagnosis or partial recovery.

If `task` is not recognized, install Task 3 and open a new terminal so the
executable is on `PATH`. Do not replace it with an unrelated executable also
named `task`. `task --list` must show `bootstrap`, `status`, `smoke` and
`destroy` from this repository's `Taskfile.yml`.

`task smoke` is mutating because it creates retained Run evidence. `task destroy`
is destructive and requires an interactive confirmation; it removes all local
database, object-store and run state with the named kind cluster.

## Quick health checklist

Run the checks in order and stop at the first failure.

| Layer | Command | Healthy result |
|---|---|---|
| Workspace | `uv run --locked python scripts/workspace.py status` | Every repository is `existing`; no destination is missing or conflicting |
| Docker | `docker context show` and `docker version` | Context is `desktop-linux`; client and Linux server respond |
| Infrastructure selection | `uv run --locked python scripts/infrastructure_release.py validate` | The selected provider commit and compatibility contract validate |
| kind | `uv run --locked python scripts/infrastructure_runtime.py cluster health --execute` | The named cluster, kubeconfig, nodes, labels and CoreDNS pass |
| Argo CD | `uv run --locked python scripts/infrastructure_runtime.py argocd health --execute` | The infrastructure-owned namespace and every selected controller pass |
| Applications | `kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd get applications -o wide` | Root and all five workload Applications are `Synced` and `Healthy` |
| PostgreSQL | `uv run --locked python scripts/postgres.py health --execute` | PostgreSQL health completes successfully |
| Object storage | `uv run --locked python scripts/object_store.py health --execute` | SeaweedFS health completes successfully |
| Database identities | `uv run --locked python scripts/control_plane_database.py health --execute` | Owned roles and credentials validate without rotation |
| Migration | `uv run --locked python scripts/control_plane_migration.py health --execute` | The locked migration Job and runtime grants validate |
| Runtime inputs | `uv run --locked python scripts/control_plane_runtime.py health --execute` | The selected Secrets and immutable ConfigMaps validate |
| GitOps workload | `uv run --locked python scripts/gitops.py health --execute` | All Applications and both managed workloads pass |
| Product path | `uv run --locked python scripts/control_plane_smoke.py --execute` | A Run completes with five verified artifacts |

The product-path command is **mutating**: it creates a Run, Kubernetes Jobs and
immutable evidence. Most health checks are read-only. Object-store health creates
a bounded `s3-health-*` diagnostic Job and retains it for inspection, so
`task status` does not reconcile desired state but is not strictly read-only.
Prerequisite discovery contacts Docker, kind or Kubernetes.

## Docker or kind does not respond

Symptoms include `kind get clusters` hanging, an empty Docker container list
after cleanup, or a connection failure before any Kubernetes check.

**Read-only** checks:

```powershell
docker context show
docker version
kind get clusters
```

Docker Desktop must be running with Linux containers. After a Docker Desktop
restart, wait until `docker version` reports both client and server before
retrying kind. Removing images only makes the next bootstrap slower; it does not
invalidate the repository configuration.

If `perfeng-local` is genuinely absent, preview and then create it:

```powershell
# read-only preview
uv run --locked python scripts/infrastructure_runtime.py cluster ensure

# mutating
uv run --locked python scripts/infrastructure_runtime.py cluster ensure --execute
```

For the supported end-to-end bootstrap, run `task bootstrap` instead. It uses
the idempotent ensure boundary and continues through the stateful GitOps
foundation. If a cluster exists without its dedicated kubeconfig, or the
kubeconfig exists without the named cluster, ensure stops for operator diagnosis
instead of adopting or deleting either side.

Do not use the provider `cluster down` action or `task destroy` as a repair.
Either is **destructive** and permanently removes all PostgreSQL, SeaweedFS and
Run state stored in the cluster.

## Plain kubectl cannot connect

A message such as `dial tcp 127.0.0.1:<port>: connectex: ... actively refused`
usually means the global kubeconfig retained the API-server port of a deleted
kind cluster. The context name may still be `kind-perfeng-local`, so the name
alone is not proof that the endpoint is current.

Use the repository-owned kubeconfig explicitly:

```powershell
# read-only
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local get nodes
```

For convenience, redirect only the current PowerShell process:

```powershell
$env:KUBECONFIG = $kubeconfig
kubectl --context kind-perfeng-local get nodes
```

Remove the process override when finished:

```powershell
Remove-Item Env:KUBECONFIG
```

The environment tools deliberately do not rewrite the user's global
`~/.kube/config`. Do not run `kind export kubeconfig` as routine project repair;
it broadens a local operation into global configuration maintenance.

## Argo CD controllers are unavailable

First run the selected provider's read-only health boundary:

```powershell
uv run --locked python scripts/infrastructure_runtime.py argocd health --execute
```

Inspect the controller workloads and recent warnings:

```powershell
# read-only
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  get deployments,statefulsets,pods
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  get events --field-selector type=Warning `
  --sort-by=.metadata.creationTimestamp
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n kube-system `
  get pods -l k8s-app=kube-dns
```

`Pending`, `CrashLoopBackOff`, high restart counts, or DNS timeout events identify
an infrastructure failure rather than Application drift. Preserve the warning
output before recovery.

If CoreDNS is ready but a repo server or application controller repeatedly
reports transport or DNS failures, recreate only its Pod and wait for its owner
to restore readiness:

```powershell
# mutating; use only after recording the failure
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  delete pod -l app.kubernetes.io/name=argocd-repo-server `
  --wait=true --timeout=120s
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  rollout status deployment/argocd-repo-server --timeout=300s
```

Use the equivalent exact label and StatefulSet rollout only when the failing Pod
is `argocd-application-controller`. Do not restart every Argo CD component at
once; doing so removes the evidence needed to identify the failing boundary.
On a fresh cluster, use `infrastructure_runtime.py argocd bootstrap --execute`
before the consumer `gitops.py bootstrap --execute`. Do not use consumer
bootstrap to repair missing provider controllers.

## An Application is OutOfSync or Degraded

Inspect all Applications, then describe only the failing one:

```powershell
# read-only
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  get applications -o wide
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  describe application perfeng-local-root
```

Interpret the result before changing anything:

- A primary Application wait timeout prints up to 20 reported out-of-sync
  resources and requests one normal comparison refresh before its single
  60-second grace period. This announced metadata mutation has a 10-second API
  timeout; it is not an explicit sync and leaves reconciliation policies
  unchanged. A refresh warning does not replace the original timeout.
- `operation=Succeeded`, `health=Healthy`, healthy child Applications and a
  recent expected revision usually indicate delayed aggregate status. Wait for
  one reconciliation interval and rerun GitOps health once. If the output says
  no individual drift was reported, the comparison may be stale; do not assume
  synchronization or reset the cluster.
- A revision older than the merged `main` commit points to delayed repository
  refresh or private-repository access. Inspect Application conditions and the
  repo-server Pod before rotating credentials.
- `Degraded` or a named resource with persistent drift is not transient. Inspect
  that resource and its events; do not run Helm or manually apply a competing
  manifest.
- A passing control-plane smoke run proves the execution path still works. Keep
  the GitOps health gate strict until its management-plane status also converges.

Check only that the repository credential exists; never print its data:

```powershell
# read-only; does not retrieve Secret content
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  get secret perfeng-github-repository-credentials -o name
```

For an expired or incorrectly scoped credential, follow the explicit immutable
Secret rotation procedure in [the GitOps guide](local-gitops.md). The token needs
read-only Contents access to the selected private repositories, not write access.

## A prerequisite health check fails

Run the prerequisite checks in the order shown in the quick checklist. A later
failure is often only a consequence of an earlier missing service.

- If PostgreSQL or SeaweedFS credentials are missing on a fresh cluster, run the
  corresponding `bootstrap` action before `gitops.py bootstrap --execute`.
  Bootstrap does not deploy a workload or rotate an existing identity.
- If either stateful workload is missing after credentials exist, inspect its
  child Application and use the staged procedure in the
  [GitOps guide](local-gitops.md#fresh-cluster-staged-bootstrap). Do not repair it
  with the retained Helm rollback command while Argo CD owns the Application.
- If the object-store workload is ready but bucket health fails, run
  `object_store.py initialize --execute`, then repeat object-store health.
- If control-plane database credentials are missing, deploy the database
  identities before the migration. Deployment never silently rotates an
  existing credential.
- If the migration Job has an unexpected identity, stop. Do not delete it until
  its image, arguments, ownership labels and deployment revision have been
  compared with the lock.
- If a selected content-addressed ConfigMap is missing, validate the release and
  runtime selection first. For a reviewed promotion, pre-stage it with
  `control_plane_runtime.py deploy --execute` before merging the Git selection.
- If an existing Secret or immutable resource has unexpected ownership or data,
  treat it as a conflict. The tooling intentionally refuses automatic adoption,
  overwrite or rotation.

The complete creation order and recovery boundaries are maintained in the
[staged GitOps guide](local-gitops.md#fresh-cluster-staged-bootstrap). The
control-plane Application may be `Degraded` during that bounded staged interval;
final GitOps health must not be accepted in that state. Stateful GitOps ownership
changes must also satisfy the unchanged-identity and rollback gates in
[ADR 0002](decisions/0002-stateful-gitops-ownership.md).

## The control-plane workload is unhealthy

Inspect the Deployment, Pods and warning events without reading Secrets:

```powershell
# read-only
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  get deployment control-plane
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  get pods -o wide
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  get events --field-selector type=Warning `
  --sort-by=.metadata.creationTimestamp
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  logs deployment/control-plane --tail=100
```

Review logs locally for URLs, identifiers or other sensitive values before
sharing them. Common classifications are:

- `ImagePullBackOff`: validate the GHCR credential with
  `control_plane_registry.py health --execute`, then use the documented explicit
  rotation only if the credential is expired.
- `CreateContainerConfigError`: a selected Secret or ConfigMap is absent; run the
  prerequisite health checks rather than editing the Deployment.
- readiness failure: check PostgreSQL, SeaweedFS, migration and runtime inputs in
  order, then inspect the bounded application log.
- wrong immutable selection: compare the live `envFrom` ConfigMap name with
  `deployments/local-control-plane.json`; fix the Git selection or allow Argo CD
  to reconcile it rather than patching the Deployment.

Inspect the selected non-secret runtime identity with:

```powershell
# read-only
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  get deployment control-plane `
  -o jsonpath='{.spec.template.spec.containers[0].envFrom[0].configMapRef.name}'
```

## The end-to-end smoke run fails

The smoke command is **mutating** and intentionally retains its client Pod on
failure. Record the printed Pod name, then inspect that exact Pod:

```powershell
# read-only; replace the placeholder with the printed name
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  describe pod <retained-smoke-pod>
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  logs <retained-smoke-pod>
```

Do not delete the Pod until its phase, events and logs have been inspected. A Run
that completes with five verified artifacts proves API admission, PostgreSQL
persistence, Kubernetes execution, SeaweedFS publication, normalization and
reporting for the selected runtime bundle.

## Validate immutable selections offline

Use these **read-only** checks before blaming live reconciliation:

```powershell
uv run --locked python scripts/control_plane_release.py validate
uv run --locked python scripts/control_plane_runtime.py validate
uv run --locked python scripts/infrastructure_release.py validate
uv run --locked python scripts/infrastructure_runtime.py cluster ensure
uv run --locked python scripts/infrastructure_runtime.py argocd validate
uv run --locked python scripts/gitops.py validate
```

They detect mismatched commits, infrastructure profiles, chart versions,
runtime bytes, content-addressed names and Application values without changing
the cluster. Infrastructure release validation reads the selected commit
without importing it. The two runtime previews then materialize and execute the
exact selected code without `--execute`, proving its CLI boundary without
contacting the cluster. All three require the commit to exist in the sibling
`k8s-infrastructure` object database and never switch or execute its working
tree. If the commit is missing, fetch it through the normal repository workflow
rather than changing the lock to the current branch. Never repair an identity
mismatch by editing or deleting an immutable ConfigMap in place. Create the new
reviewed identity, select it in Git and retain the previous identity for
rollback.

## What to include in a defect report

Include:

- the exact failing command and complete redacted error;
- `git rev-parse HEAD` and `git status --short --branch`;
- Docker context name and whether `docker version` reaches the server;
- `kind get clusters` and the explicit-kubeconfig node summary;
- `kubectl ... -n argocd get applications -o wide`;
- Pod names, phases, readiness and restart counts in the failing namespace;
- bounded warning events and the name of any retained smoke Pod;
- whether retrying once changed a transient Application state.

Do not include Secret YAML, decoded Secret data, token environment variables,
database URLs, registry authorization headers, the Argo CD initial admin
password, or logs that have not been reviewed and redacted.

## Maintenance contract

This runbook owns the diagnostic order, expected healthy signals and routing to
recovery documentation. The focused guides own deployment details, credential
rotation, persistence guarantees and destructive procedures.

A pull request must update this runbook when it changes a supported operational
command, prerequisite, healthy signal, failure classification or recovery path.
New diagnostics default to read-only, use the dedicated kubeconfig and identify
their exact target. New mutations and destructive operations must be labeled and
linked to the document that defines their ownership and rollback boundary.
