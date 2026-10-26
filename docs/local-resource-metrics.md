# Local verified-TLS resource metrics

- Status: GitOps selection prepared; live sync, scraping and qualification require acceptance
- Milestone: M04 — Kubernetes Benchmark Decision Loop

## TL;DR

- Argo CD deploys Metrics Server from the exact infrastructure-lock revision, never the sibling branch.
- The separate `perfeng-local-resource-metrics` Project permits only the reviewed source, namespaces and resource kinds.
- The Application uses the operator-provisioned immutable TLS Secret and its real public CA; no private key is committed.
- Kubelet certificate bootstrap/approval remains explicit provider work, not a GitOps hook or benchmark preflight repair.
- After merge reconcile the staged root, run `task kubernetes:metrics-health`, then `task kubernetes:qualify`; full GitOps health also requires the runtime.
- Fresh metrics unblock an observation prerequisite, not runtime promotion or M04 completion.

## Selected ownership and trust

`deployments/local-infrastructure.json` and CI select infrastructure revision
`59b175492aad9d4b0900d03fe04dd0b2ff60c8eb`, retaining the serving-CSR usage fix
and approved Argo CD Redis source/pull-policy correction.
Cluster identity, node image, placement topology and Argo CD 3.5.2 remain unchanged.
The metrics Application selects that same commit's `charts/resource-metrics` 1.0.1,
which pins the official Metrics Server 0.9.0 image by digest.
Chart 1.0.1 omits the default-false `APIService.spec.insecureSkipTLSVerify` field,
matching Kubernetes readback and preventing repeated Argo CD drift. The render
gate requires that canonical omission; live health still accepts omission or
explicit false and rejects a bypass. Secret references, placement and sync policy are unchanged.
The public serving CA is selected separately for the recreated local cluster.

The consumer owns the Project/Application and existing `monitoring` namespace.
The chart owns its eight reviewed workload/RBAC/API resources. It creates no namespace,
Secret or CSR. Operator-created `substrate-resource-metrics-tls` remains outside Argo CD.
The Application contains its public CA and Secret name only, in `helm.valuesObject`;
there is no second CA lock or generated desired-state writer.

The separate Project allows only `k8s-infrastructure`, the local server and
`monitoring`/`kube-system`. Its kind allowlists cover ServiceAccount, Service, Deployment,
RoleBinding, ClusterRole, ClusterRoleBinding and APIService. It grants no Namespace or
Secret authority. These are kind/namespace permissions, not restrictions on every RBAC
verb or resource name: the immutable chart and exact rendered identity checks remain
essential. The existing `perfeng-local` Project is unchanged and does not gain this source
or any cluster-scoped permission. See [ADR 0012](decisions/0012-select-verified-resource-metrics.md).

Metrics Server runs on the Linux control-plane node with its explicit NoSchedule
toleration, not the generator/SUT nodes. It verifies kubelet leaves using the cluster CA.
The APIService verifies the external serving leaf using the selected public CA. Neither
hop allows a TLS bypass. Initial automated sync is enabled, but self-healing, pruning and
cascading-deletion finalizers are disabled. Argo CD is the sole workload reconciler;
never use Helm install/upgrade for this package.

## Before merge and on a fresh cluster

Keep Docker Desktop running and use the dedicated environment kubeconfig/context.
Certificate operations are separate, mutating provider actions requiring maintenance
coordination, identity checks and explicit confirmation. Follow the selected provider's
[resource-metrics guide](https://github.com/stanimirivanov/k8s-infrastructure/blob/59b175492aad9d4b0900d03fe04dd0b2ff60c8eb/docs/resource-metrics.md).
Run those commands from `k8s-infrastructure` with `--state-root ../perfeng-environment`.

For an existing cluster with completed serving bootstrap/approvals and the matching
owned Secret, do not repeat node rollout or generate a new CA. On a fresh/recreated
cluster, first use the normal staged bootstrap to establish the consumer namespaces and
Argo CD, then complete the explicit certificate procedure before final health. Stateful
bootstrap deliberately does not wait for metrics, whose external prerequisites may not
exist yet. A newly generated serving CA requires a reviewed change to the Application;
never substitute the provider's synthetic rendering fixture or edit a qualification receipt.

### Recovery after Docker storage removal

Deleting all Docker containers and volumes removes the cluster, PostgreSQL/object-store
data and the external metrics TLS Secret. An old public CA cannot recreate its discarded
signing key. Archive an orphaned dedicated kubeconfig only after verifying that the named
cluster is absent; keep host credentials, publication receipts and qualification evidence.
Use staged bootstrap and the exact selected provider's certificate procedure above.
Cold image pulls can exceed a readiness deadline: inspect Pod events and controller state,
then resume the failed supported stage once its prerequisite is ready. Do not weaken deadlines.

The rebuilt cluster has kube-system UID `257a288d-9e0f-4956-a0e3-9c4104de6476`.
Its newly issued public serving CA has SHA-256 fingerprint
`e8f3d17003d9bbaa4aa7b62a0a6ee19b525e40505d6cd15cd5eb3961deedfc53`.
The public CA selected during trust recovery is retained unchanged by this chart
promotion. It does not restore deleted data or activate runtime workloads.
Reverting to the old CA cannot recover the lost Secret and would break this new binding;
recover through a reviewed selection matching the retained immutable Secret instead.

The selected benchmark bundle/package 1.0.1 now binds this rebuilt cluster UID,
following fresh accepted qualification and merged upstream PR #61. Keep all historical
receipts intact. Qualification records an observation, not permanent admission;
repeat qualification when exercising the benchmark loop. As described in
[runtime deployment](kubernetes-runtime-deployment.md#fresh-cluster-recovery), the existing
preparation Task supports first installation; preparation and manual activation remain separate gates.

The existing Argo CD owner-prefix Git credential can be reused only if its fine-grained
Contents token permits read access to `k8s-infrastructure` as well as the existing workload
sources. CI uses `PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN` for that exact checkout.
Package-only GHCR credentials are not Git source credentials. Missing permission needs
explicit credential recovery; no token is silently rotated or added to Git.

From `perfeng-environment`, the following checks are offline after dependency bootstrap:

```powershell
uv sync --locked
make validate
make release-selection-check
```

Normal `make verify` needs no sibling checkout or cluster. The release-selection tier
reads the actual selected Git objects, lints/renders the chart with public values and
verifies resource identities, TLS and placement. It does not execute mutable sibling files.
The locked cryptography dependency is also needed by the selected provider's health module.

## After-merge acceptance

Run in PowerShell from `perfeng-environment`, after the GitOps files reach remote `main`:

```powershell
Set-Location C:\Source\perf-eng\perfeng-environment
uv sync --locked
uv run --locked python scripts/gitops.py bootstrap --execute
task kubernetes:metrics-health
task kubernetes:qualify
```

The root reads `main`, creates the separate Project in wave -1 and reconciles the metrics
Application. No extra deploy or Helm command is needed for an already bootstrapped root.
Require the metrics Application to be Synced/Healthy at the selected source after
root reconciliation. On the rebuilt cluster the manual control-plane child may
remain Missing until the existing `task kubernetes:prepare`, policy publication
and manual activation are complete.
Only then run `uv run --locked python scripts/gitops.py health --execute` for full
GitOps acceptance; that command also waits for all workload Applications. Its existing
timeout path may request one normal Application refresh. The focused metrics task
performs no mutations or refreshes.

`kubernetes:metrics-health` checks the live Application/Project against reviewed desired
state and the APIService's exact public CA/service binding. It then executes only the
selected provider's read-only `health` from its isolated Git archive, requiring an Available
verified-TLS APIService and complete fresh node usage. No Secret data is read. It never
enables serving bootstrap, approves a CSR, creates certificates or repairs a deployment.

All six benchmark qualification checks must separately report PASS, retaining a new
ACCEPTED integration receipt and environment descriptor under ignored `results`.
Missing/stale observations remain UNKNOWN. Actual contention may still reject a healthy
metrics installation. Keep previous rejected/unknown evidence; do not overwrite it.

## Failure and recovery

- Missing Application: verify the root's remote revision and resource list; do not apply
  the chart imperatively. On a fresh cluster, use the normal GitOps bootstrap boundary.
- Repository permission denied: inspect the source credential's allowed repositories;
  do not replace the source pin, grant write access or reuse a package-only token.
- Secret missing or near expiry: inspect the external certificate setup/renewal procedure.
  GitOps health does not create or rotate keys, and a new CA requires selection review.
- API unavailable or x509 errors: inspect Pod readiness, Service endpoints, APIService
  conditions and serving CSR issuance. Do not disable either TLS verification hop.
- Selection mismatch: compare live Application/Project/API CA with committed desired state.
  Self-healing is initially off; restore the reviewed selection explicitly through Argo CD.
- Missing/stale usage or interference: follow [benchmark qualification](kubernetes-benchmark-qualification.md)
  and [troubleshooting](troubleshooting.md). Metrics Server is coarse admission evidence,
  not historical monitoring or calibrated benchmark telemetry.

Read-only inspection using the dedicated context:

```powershell
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local --request-timeout=10s -n argocd get application perfeng-local-resource-metrics
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local --request-timeout=10s -n monitoring get pods -l app.kubernetes.io/name=metrics-server
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local --request-timeout=10s get apiservice v1beta1.metrics.k8s.io
```

## Rollback and limits

Roll back a bad metrics upgrade by reverting its reviewed source/values selection in Git
and explicitly syncing the prior compatible chart. Preserve external TLS material, node
backups, CSRs, namespaces, volumes and other Applications. Removing these new manifests
from the root does not uninstall retained resources because pruning is disabled; a full
removal requires a separately reviewed disable/orphan/decommission operation. Do not delete
the shared `monitoring` or `kube-system` namespace or roll back the provider lock alone while
leaving a mismatched metrics Application. Node certificate rollback remains explicit provider work.

This slice does not enable runtime bundles, run kube-burner, approve a baseline or complete
M04. Metrics Server is not a replacement for M05 monitoring, and kind nodes still share
their host's resources. Cloud kubelet PKI remains provider-owned, not a reason to execute
the local kind rollout on another cluster.
