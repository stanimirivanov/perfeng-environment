# Local Kubernetes foundation

This is a disposable development environment, not a production configuration or
a calibrated performance lab. No cluster is created by validation or CI.

## Prerequisites

Use kind v0.31.0, Docker with Linux containers and cgroup v2, Helm, kubectl,
Python 3.12, and uv. Rendering was checked with Helm v4.1.4. The local kubectl
client is v1.36.4; live compatibility still needs validation on your Docker host.

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
uv run --locked python scripts/cluster.py deploy
uv run --locked python scripts/cluster.py deploy --execute
uv run --locked python scripts/cluster.py health --execute
```

Without --execute, every action only prints its argument arrays. Creation
reserves .local/perfeng-local.kubeconfig and never uses your global kubeconfig.
All kubectl/Helm calls pin that file and kind-perfeng-local explicitly; before
access, its contents must match the named kind cluster's exported kubeconfig.
Do not edit or share the file: it contains cluster-admin credentials. The
directory is Git-ignored, with owner-only permissions requested on POSIX;
review Windows ACLs on shared machines.

Execution requires a local Docker socket context (Unix socket or Windows named
pipe). TCP/SSH Docker contexts are rejected, even if a TCP endpoint is loopback.
Inherited Helm Kubernetes overrides and the global KUBECONFIG variable are not
used by child commands.

Startup refuses existing clusters or kubeconfig files and never performs
automatic deletion or adoption. Failed creation retains nodes and local state
for diagnosis. Inspect them before choosing explicit cleanup; do not rerun
startup expecting it to erase a partial environment.

Deployment applies four namespaces and installs the sample-sut Helm release in
perf-sut. Health checks require three Ready, uncordoned nodes, correct placement
labels, no memory/disk/PID pressure, and successful CoreDNS/sample-SUT rollouts.
These checks do not measure CPU saturation or network performance.

To access the local fixture after deployment:

```sh
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local -n perf-sut port-forward service/sample-sut 8080:8080
```

Then request http://127.0.0.1:8080 from another terminal. The response should be
PerfEng SUT. This is HTTP echo only: it does not implement checkout/search/account
APIs and cannot be used to claim that the business-flow k6 tests pass.

## Teardown

Preview, then explicitly confirm the exact cluster name:

```sh
uv run --locked python scripts/cluster.py down
uv run --locked python scripts/cluster.py down --execute --confirm-delete perfeng-local
```

Deletion destroys all cluster-local state and removes the dedicated kubeconfig.
Data cannot be recovered unless exported beforehand. The command verifies the
named cluster/configuration before deletion and never performs recursive host
directory cleanup. If a failed creation has no valid kubeconfig, inspect it and
use kind's explicit named-cluster cleanup manually; no automatic recovery is
attempted.

## Namespace, placement, secret, and storage boundaries

- perf-platform: future control-plane/analysis infrastructure.
- perf-generators: future k6/browser Jobs, selecting workload: performance-generator.
- perf-sut: local SUT workloads, selecting workload: sut.
- monitoring: reserved for later observability installation.

This slice installs no privileged platform RBAC, default passwords, Secrets,
PVCs, PostgreSQL, MinIO, or observability components. The sample pod has no
service-account token, runs non-root, drops capabilities, and uses a read-only
root filesystem with resource requests/limits and readiness/liveness probes.

The sample image is version-tagged hashicorp/http-echo:1.0.0 for local use.
Unlike the kind node image, it is not digest-pinned; production promotion
requires an independently verified digest. No claim of production image
immutability is made.

Local kind node filesystems are ephemeral. Later storage work must define
credentials, access boundaries, retention, and export before any raw artifacts
or database state are treated as durable. Do not point this configuration at
production or assume default kind networking enforces NetworkPolicies.

## Provenance and remaining work

Adapted from performance-platform commit
57c1b5074898d4d86476e8b4f99c19eff3a77018:
infra/local/kind/cluster-config.yaml, infra/local/scripts/, the sample-sut chart,
and the namespace definitions under infra/charts/perfeng-infra/charts/namespaces/.

The three-node layout, labels, and HTTP-echo fixture intent are retained.
Lifecycle scripts are replaced by one portable Python implementation: no cmd
string construction, current-context reliance, or pre-creation cleanup.
The source remains intact; historical ancestry import is owner-operated.

Offline Helm lint/template and unit checks are not a successful deployment.
Run up/deploy/health and verify the endpoint on a Docker-capable host before
considering live acceptance complete. Stateful backing services and the owned
business API fixture are subsequent PR-sized slices before durable k6 Jobs.
