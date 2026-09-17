# Local Kubernetes foundation

## TL;DR

- The immutable `k8s-infrastructure` selection owns the disposable three-node
  kind cluster; `perfeng-environment` owns only Perfeng composition above it.
- Commands are previews unless `--execute` is supplied and always use the
  dedicated `.local/perfeng-local.kubeconfig`.
- Prefer `task bootstrap` for safe create-or-reuse behavior. Use `task destroy`
  only when permanent loss of all cluster-local data is intended.
- `scripts/cluster.py bootstrap` is retained solely to pre-create the four
  reviewed Perfeng namespaces before Argo CD adopts them.
- This topology is neither a production environment nor a calibrated
  performance lab.

## Prerequisites

Use kind v0.31.0, Docker with Linux containers and cgroup v2, Helm, kubectl,
Python 3.12, uv and Task 3. Rendering was checked with Helm v4.1.4. The local
kubectl client is v1.36.4; live compatibility still depends on the Docker host.

The selected kind node image is digest-pinned. All three nodes are containers
on one host: generator/SUT placement labels separate scheduling but do not
isolate CPU, disk or network contention. The Kubernetes API binds to loopback,
and no host service ports are opened by default.

## Provider commands

Run commands from `perfeng-environment`. First validate the immutable selection:

```sh
uv sync --locked
uv run --locked python scripts/validate_cluster.py
uv run --locked python scripts/infrastructure_release.py validate
```

Preview and execute the selected provider lifecycle through the consumer
wrapper:

```sh
uv run --locked python scripts/infrastructure_runtime.py cluster ensure
uv run --locked python scripts/infrastructure_runtime.py cluster ensure --execute
uv run --locked python scripts/infrastructure_runtime.py cluster health --execute
```

`ensure` creates the cluster only when both its name and dedicated kubeconfig
are absent. Otherwise it validates and reuses the complete owned identity. An
orphaned cluster or kubeconfig is rejected rather than adopted or replaced.
The wrapper executes an archive of the commit selected in
`deployments/local-infrastructure.json`, never the sibling checkout's current
branch or working tree.

After cluster creation, pre-create only the namespaces later owned by the root
Application, then continue with the [local GitOps guide](local-gitops.md):

```sh
uv run --locked python scripts/cluster.py bootstrap
uv run --locked python scripts/cluster.py bootstrap --execute
```

`cluster.py` has no public create, health or destroy actions. Its remaining
bootstrap command applies no workload, Secret, PVC or reconciler.

## State and safety boundaries

Provider commands reserve `.local/perfeng-local.kubeconfig` and never use the
global kubeconfig. Every kubectl operation pins that file and context
`kind-perfeng-local`. Do not edit or share the file: it contains cluster-admin
credentials. The directory is Git-ignored, with owner-only permissions
requested on POSIX; review Windows ACLs on shared machines.

Execution requires a local Docker socket context. TCP and SSH Docker contexts
are rejected. The global `KUBECONFIG` variable is not used by child commands.
Failed creation retains nodes and state for diagnosis. Inspect them before any
explicit cleanup; repeated startup never silently erases a partial environment.

Provider cluster health requires three Ready, uncordoned nodes, the reviewed
placement labels, no memory/disk/PID pressure, and a successful CoreDNS rollout.
It does not measure CPU saturation, network performance or workload health.
Perfeng workload health belongs to `gitops.py health` and `task status`.

The four consumer namespaces are:

- `perf-platform`: control plane, PostgreSQL and object storage;
- `perf-generators`: k6 and other execution Jobs;
- `perf-sut`: local SUT workloads; and
- `monitoring`: reserved for later observability installation.

## Accessing the sample SUT

After GitOps deployment:

```sh
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local -n perf-sut port-forward service/sample-sut 8080:8080
```

Request `http://127.0.0.1:8080` from another terminal. The response is a fixed
fixture for functional integration checks, not a realistic performance claim.

## Destructive teardown

Preview the provider action directly when diagnosing its selection:

```sh
uv run --locked python scripts/infrastructure_runtime.py cluster down
```

For routine teardown use the operator façade:

```sh
task destroy
```

The Task prompt and provider's exact `--confirm-delete perfeng-local` check are
independent protections. Deletion permanently destroys PostgreSQL, SeaweedFS,
Run evidence and every other cluster-local resource, then removes the dedicated
kubeconfig. It never recursively removes host directories. Do not use teardown
as a repair step.

## Provenance and acceptance

The local lifecycle originated in the completed prototype and was hardened in
this repository before its source history and ownership moved to
`k8s-infrastructure`. The environment now consumes a content-selected provider
release while retaining product-specific bootstrap and acceptance.

Offline validation is not deployment acceptance. Run the exact procedure in
[local infrastructure release selection](local-infrastructure-release.md)
after every provider promotion, including stateful identity checks when reusing
an existing cluster. The initial ownership cutover passed that gate and the
superseded lifecycle source has been removed.
