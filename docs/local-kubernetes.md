# Local Kubernetes foundation

## TL;DR

- This creates a disposable three-node kind cluster for local development, not
  a production environment or calibrated performance lab.
- Commands are previews unless `--execute` is supplied and always use the
  dedicated `.local/perfeng-local.kubeconfig`.
- After cluster creation, bootstrap the reviewed namespaces and use the
  [GitOps path](local-gitops.md) for workload deployment.
- Prefer `task bootstrap` for safe create-or-reuse behavior and `task destroy`
  only when permanent loss of all cluster-local data is intended.
- Use the [troubleshooting runbook](troubleshooting.md) before recreating an
  unavailable cluster or changing kubeconfig state.
- Teardown permanently destroys all cluster-local state.

This is a disposable development environment, not a production configuration or
a calibrated performance lab. No cluster is created by validation or CI.

## Prerequisites

Use kind v0.31.0, Docker with Linux containers and cgroup v2, Helm, kubectl,
Python 3.12, uv and Task 3 for the high-level operator commands. Rendering was
checked with Helm v4.1.4. The local kubectl client is v1.36.4; live compatibility
still needs validation on your Docker host.

The kind node image is pinned to the digest published in the
[kind v0.31.0 release](https://github.com/kubernetes-sigs/kind/releases/tag/v0.31.0).
All three nodes are containers on one host: separate generator/SUT labels provide
scheduling separation, not CPU, disk, or network isolation from host contention.
The Kubernetes API binds to loopback. No host service ports are opened by default.

## Commands: PowerShell and shell

Run these same uv commands from perfeng-environment in either PowerShell or a
POSIX shell. Paths are resolved relative to this repository.

```sh
uv sync --locked
uv run --locked python scripts/validate_cluster.py
uv run --locked python scripts/cluster.py up
uv run --locked python scripts/cluster.py up --execute
```

The high-level `task bootstrap` command uses `cluster.py ensure --execute`: it
creates the cluster only when both its name and dedicated kubeconfig are absent,
or validates the health and ownership of the existing cluster. An orphaned
cluster or kubeconfig is rejected rather than adopted or replaced.

After cluster creation, continue with the [local GitOps guide](local-gitops.md).
Bootstrap applies only the same namespace manifest that the root Application
later owns. It does not install or reconcile a workload:

```sh
uv run --locked python scripts/cluster.py bootstrap
uv run --locked python scripts/cluster.py bootstrap --execute
uv run --locked python scripts/cluster.py health --execute
```

Without --execute, every action only prints its argument arrays. Creation
reserves .local/perfeng-local.kubeconfig and never uses your global kubeconfig.
All kubectl calls pin that file and kind-perfeng-local explicitly; before
access, its contents must match the named kind cluster's exported kubeconfig.
Do not edit or share the file: it contains cluster-admin credentials. The
directory is Git-ignored, with owner-only permissions requested on POSIX;
review Windows ACLs on shared machines.

Execution requires a local Docker socket context (Unix socket or Windows named
pipe). TCP/SSH Docker contexts are rejected, even if a TCP endpoint is loopback.
The global KUBECONFIG variable is not used by child commands.

Each executed command prints start/completion messages, with an elapsed-time
update every 10 seconds while waiting. These updates indicate waiting, not
proof that the child is making progress; there is no overall wrapper deadline.
Initial creation may take several minutes to download the node image. Child
stdout/stderr remain private because they can contain cluster credentials.
Output is captured as bytes and returned data decoded explicitly as UTF-8,
independent of the Windows console encoding.

Startup refuses existing clusters or kubeconfig files and never performs
automatic deletion or adoption. Failed creation retains nodes and local state
for diagnosis. Inspect them before choosing explicit cleanup; do not rerun
startup expecting it to erase a partial environment.

Bootstrap applies the four namespace resources with the same identities and
labels later reconciled by the root Application. The cluster command no longer
installs the sample SUT or any other workload. Cluster health requires three
Ready, uncordoned nodes, correct placement labels, no memory/disk/PID pressure,
and a successful CoreDNS rollout. Workload health belongs to `gitops.py health`.
These checks do not measure CPU saturation or network performance.

To access the local fixture after deployment:

```sh
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local -n perf-sut port-forward service/sample-sut 8080:8080
```

Then request http://127.0.0.1:8080 from another terminal. The response should be
a JSON fixture description. The [sample API](sample-api.md) implements the local
checkout/search/account flows; /healthz is the readiness endpoint. These fixed
responses support functional integration checks, not realistic performance claims.

## Teardown

Preview, then explicitly confirm the exact cluster name:

```sh
uv run --locked python scripts/cluster.py down
uv run --locked python scripts/cluster.py down --execute --confirm-delete perfeng-local
```

The operator façade retains both protections. `task destroy` displays an
interactive destructive-action prompt and then supplies the exact reviewed name
to the cluster command. Do not bypass the prompt in routine use.

Deletion destroys all cluster-local state and removes the dedicated kubeconfig.
Data cannot be recovered unless exported beforehand. The command verifies the
named cluster/configuration before deletion and never performs recursive host
directory cleanup. If a failed creation has no valid kubeconfig, inspect it and
use kind's explicit named-cluster cleanup manually; no automatic recovery is
attempted.

## Namespace, placement, secret, and storage boundaries

- perf-platform: control-plane, PostgreSQL and object-store infrastructure.
- perf-generators: k6 and other execution Jobs, selecting workload: performance-generator.
- perf-sut: local SUT workloads, selecting workload: sut.
- monitoring: reserved for later observability installation.

The base cluster commands install no workload, privileged platform RBAC,
default password, Secret, PVC, PostgreSQL, object store or observability
component. The [PostgreSQL](local-postgres.md) and
[SeaweedFS](local-object-store.md) workflows provision external credentials and
retain explicit rollback boundaries while Argo CD owns their workloads.
The sample pod has no
service-account token, runs non-root, drops capabilities, and uses a read-only
root filesystem with resource requests/limits and readiness/liveness probes.

The sample API uses a digest-pinned Python runtime and a read-only source
ConfigMap with a rollout checksum. It remains local development composition,
not a production application image or calibrated SUT.

Local kind node filesystems are ephemeral. Later storage work must define
credentials, access boundaries, retention, and export before any raw artifacts
or database state are treated as durable. Do not point this configuration at
production or assume default kind networking enforces NetworkPolicies.

## Provenance and remaining work

Adapted from performance-platform commit
57c1b5074898d4d86476e8b4f99c19eff3a77018:
infra/local/kind/cluster-config.yaml, infra/local/scripts/, the sample-sut chart,
and the namespace definitions under infra/charts/perfeng-infra/charts/namespaces/.

The three-node layout and labels are retained; the original echo fixture has
been replaced by the owned local sample API.
Lifecycle scripts are replaced by one portable Python implementation: no cmd
string construction, current-context reliance, or pre-creation cleanup.
The source remains intact; historical ancestry import is owner-operated.

Offline Helm lint/template and unit checks are not a successful deployment.
Run cluster creation, namespace bootstrap, GitOps deployment and health checks, then
verify the endpoint on a Docker-capable host before considering live acceptance
complete. PostgreSQL and SeaweedFS support the control plane, and the sample API
supports the existing business-flow scenarios.
