# Local GitOps foundation

## TL;DR

- This is a parallel architecture spike; it does not replace or modify the
  working Milestone 1 deployment path.
- Argo CD is selected through the official, human-readable `v3.5.2` install
  manifest; the upstream manifest supplies its tested dependency image tags.
- Run `gitops.py validate` offline before review, then merge the GitOps files to
  `main` before executing bootstrap because the root Application reads `main`.
- Set `PERFENG_REPOSITORIES_READ_TOKEN` to a GitHub token with read-only Contents
  access to the private repositories before executing deployment.
- The root and sample-SUT Applications synchronize automatically, but pruning
  and self-healing remain disabled during the spike.

## Scope

This step installs a non-HA Argo CD control plane into the existing verified
`perfeng-local` kind cluster, provisions one immutable private-repository
credential, and creates the `perfeng-local-root` Application. The root currently
manages the restricted `perfeng-local` AppProject and the sample SUT as its first
child Application. PostgreSQL, SeaweedFS, the control plane and performance-Run
resources are not migrated yet.

The existing Python deployment commands remain available as the Milestone 1
rollback path. Once the child Application is active, Argo CD owns the sample SUT
and `cluster.py deploy --execute` must not be run concurrently. Other existing
deployments are unchanged, and this step performs no data migration.

Environment composition in this spike lives in Kustomize and Argo CD resources.
The small Python boundary does not generate those resources: it verifies the
dedicated local cluster, protects ownership, supplies private credentials over
stdin, invokes Kustomize, and performs bounded health checks. Later migration
steps can replace that boundary with a task runner and an external-secrets
solution without rewriting the composition.

## Review the pinned inputs

Validate the explicit release and local objects without accessing the network,
Docker or Kubernetes:

```powershell
uv run --locked python scripts/gitops.py validate
uv run --locked python scripts/gitops.py deploy
```

The first command verifies:

- the official Argo CD source and explicit semantic release tag;
- root Application source, destination and non-destructive sync policy;
- sample-SUT chart source, release name, destination and sync policy;
- creation of only the explicitly allowed `perf-sut` destination namespace;
- agreement between the AppProject source repositories and `workspace.json`.

The second command is only a deployment preview. Preview never downloads the
install manifest, reads a credential or contacts the cluster.

The official upstream manifest is not copied into this repository. The
Kustomize bootstrap references the explicit `v3.5.2` release tag and trusts the
component image versions tested in that release. This keeps the selected
version visible and upgrades small enough to review without introducing another
manifest-generation script.

Render the exact bootstrap for review without applying it:

```powershell
kubectl kustomize gitops/bootstrap/argocd
```

Review the rendered Argo CD, Dex and Redis image tags as part of an upgrade.
Rendering needs network access to fetch the versioned upstream manifest; the
repository validation above remains offline.

## Deploy after merge

The root Application reads
`https://github.com/stanimirivanov/perfeng-environment.git` at `main`. Merge this
foundation before deployment so that Argo CD can find
`gitops/environments/local` in the remote repository.

If the GitOps foundation is already running, merging this change causes the root
Application to create the sample-SUT child automatically. Run the health command
afterward; rerunning deployment is safe but not required.

Use the same private-repository token used by repository validation. It needs
read-only Contents access to the private `perfeng-*` repositories. Package-only
access is insufficient for Git. Store it only in the current process
environment:

```powershell
$env:PERFENG_REPOSITORIES_READ_TOKEN = Read-Host `
  "GitHub private-repository read token" -MaskInput
uv run --locked python scripts/gitops.py deploy --execute
```

The command:

1. verifies the local Docker endpoint, kind identity and dedicated kubeconfig;
2. creates the owned `argocd` namespace or validates its existing ownership;
3. lets Kustomize fetch the official versioned install manifest;
4. applies the rendering using server-side apply, as required for the Argo CD
   CRDs;
5. waits for all six Deployments and the application-controller StatefulSet;
6. creates or validates an immutable Argo CD repository-credential template;
7. ownership-checks and applies the local root Application;
8. waits for the root and sample-SUT Applications to become `Synced` and
   `Healthy`;
9. waits for the sample-SUT Deployment rollout.

On a fresh cluster the child creates its exact `perf-sut` destination namespace.
The AppProject does not permit an arbitrary destination, and an existing
namespace's metadata is left untouched.

The token is not accepted as an argument, written to a file, printed, or placed
in Git. It is sent to Kubernetes only as manifest stdin. An existing owned
credential is validated and reused without reading or rotating the environment
token. Recovery from an expired token remains explicit because the Secret is
immutable.

## Health and inspection

Run the read-only health workflow after bootstrap:

```powershell
uv run --locked python scripts/gitops.py health
uv run --locked python scripts/gitops.py health --execute
```

The executable command validates namespace and Secret ownership, waits for the
pinned Argo CD workloads, verifies the root Application identity, requires the
root and sample-SUT Applications to be both `Synced` and `Healthy`, and waits for
the sample-SUT Deployment rollout.

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

## Sample SUT ownership and rollback

The child Application renders the existing `charts/sample-sut` chart from this
repository with Helm release name `sample-sut` into `perf-sut`. It does not copy
the chart or invent a second set of values. Argo CD can adopt the resources from
an existing local Helm deployment; its Helm release metadata is not deleted.

Do not use the legacy Helm deployment command while the child Application is
active. To return ownership to the legacy path:

1. merge a revert that removes `sample-sut-application.yaml` from the local root
   Kustomization;
2. delete only the now-unwanted Application while orphaning its workloads;
3. run the existing Helm deployment command to reassert the legacy release.

```powershell
kubectl --kubeconfig .local/perfeng-local.kubeconfig `
  --context kind-perfeng-local -n argocd delete application `
  perfeng-local-sample-sut --cascade=orphan
uv run --locked python scripts/cluster.py deploy --execute
```

The order matters: deleting the child before the Git revert reaches `main`
allows the root Application to recreate it.

## Safety and limitations

Bootstrap does not delete or adopt an existing `argocd` namespace owned by
another installation. It does not rotate an existing credential, enable pruning,
enable self-healing, expose the Argo CD API server, or install a host-side Argo
CD CLI. There is deliberately no uninstall command in this step.

The standard non-HA installation is appropriate for the disposable local spike,
not production. Production topology, SSO, external secrets, network exposure,
backups, high availability and controller resource sizing remain undecided.

The next migration task deploys the control plane with content-addressed runtime
configuration. Drift correction and immutable promotion are tested only after
that child exists.

## Upgrading Argo CD

An Argo CD upgrade changes the explicit release tag, never the resource URL to
`stable`, `latest`, or a branch. Review the official release and upgrade notes,
update the version in the Kustomize resource URL, and inspect the rendered
component image tags. Run offline validation before executing the upgrade.

References:

- [Argo CD installation](https://argo-cd.readthedocs.io/en/stable/operator-manual/installation/)
- [Argo CD declarative setup](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/)
- [Argo CD private repositories](https://argo-cd.readthedocs.io/en/stable/user-guide/private-repositories/)
- [ADR 0001](decisions/0001-gitops-environment-composition.md)
