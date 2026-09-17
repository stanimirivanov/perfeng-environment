# perfeng-environment

## TL;DR

- This repository composes the local performance-platform workspace and its
  Kubernetes environment; it does not own component source code.
- `Taskfile.yml` provides the supported high-level local operator commands while
  the preview-first Python tools remain the auditable safety boundaries.
- [ADR 0001](docs/decisions/0001-gitops-environment-composition.md) selected the
  Argo CD-centred architecture; its local migration is now complete.
- The [local GitOps environment](docs/local-gitops.md) installs pinned Argo CD
  and reconciles all supported local workloads without competing reconcilers.
- The [migration completion record](docs/gitops-migration-completion.md) explains
  why the remaining imperative commands are bootstrap, external-state or
  acceptance boundaries rather than competing workload reconcilers.
- [ADR 0004](docs/decisions/0004-separate-kubernetes-infrastructure.md)
  selects a staged extraction of reusable Kubernetes substrate and Argo CD
  bootstrap. [ADR 0005](docs/decisions/0005-adopt-k8s-infrastructure-name.md)
  records the created repository name, `k8s-infrastructure`; the
  [cloud options evaluation](docs/cloud-kubernetes-options.md) records why OCI
  is the first guarded candidate and why it is not deployable yet.
- Clone sibling repositories with the workspace command, then use the focused
  guides under `docs` for the environment capability you need.
- Start with the [local troubleshooting runbook](docs/troubleshooting.md) when
  Docker, kind, Argo CD or an end-to-end check fails.
- Never commit credentials or replace explicit release versions with `stable`,
  `latest`, or branches.

Workspace bootstrap and local environment composition. Production composition
is not implemented.
The workspace commands manage source checkouts only. Separate, preview-first
[local Kubernetes commands](docs/local-kubernetes.md) now provide the kind and
[sample API foundation](docs/sample-api.md). Argo CD reconciles the
[local PostgreSQL](docs/local-postgres.md) and
[SeaweedFS object store](docs/local-object-store.md) workloads; their external
credentials, persistent identities and focused checks remain separate safety
boundaries.
The [local k6 capture workflow](docs/local-k6-run.md) runs the digest-pinned
checkout smoke workload and uploads checksum-verified raw artifacts to SeaweedFS.
The uploader uses [restricted storage credentials](docs/storage-access.md);
existing installations require the documented object-store upgrade. The same
identity can read, but cannot publish, reviewed policy objects for reporting.
The preview-first [policy publisher](docs/policy-publication.md) conditionally
creates exact bundle-owned policy bytes with an operator-approved checksum and
verifies the stored object before writing a local receipt.
A [native Windows browser-host profile](docs/windows-browser-host.md) defines
and checks the controlled machine conditions used by Playwright measurements.
The browser-environment assembler binds an accepted qualification to the actual
Playwright runtime without inventing missing host or browser values.
The local browser uploader publishes those exact bytes through the restricted
storage identity and returns verified immutable artifact references.
The browser-diagnostics assembler binds those references to the runner's
versioned capture receipt without inferring missing evidence.
The [local kube-burner publisher](docs/local-kube-burner.md) validates a completed
pod-startup capture, stores its three immutable evidence files, and writes the
exact receipt request consumed by the `perfeng-k8s` raw-manifest assembler. The
shared raw-result publisher then writes the assembled manifest at the stable
control-plane discovery key.
The [local control-plane deployment lock](docs/local-control-plane.md) pins the
published service image, reviewed Helm chart and runtime-registry bundle before
deployment. Its preview-first registry command validates private GHCR access and
creates only the externally managed image-pull Secret required by the chart.
The [local GitOps environment](docs/local-gitops.md) manages its AppProject,
namespaces, sample SUT, PostgreSQL, SeaweedFS and control-plane workloads.
Remaining imperative commands bootstrap the cluster and reconciler, provision
external credentials or durable inputs, perform explicit stateful rollback, or
run acceptance workflows. Workload Applications self-heal reviewed drift.
Non-secret control-plane runtime inputs are content-addressed so multiple
immutable selections can coexist; root self-healing and destructive pruning
remain disabled.

## Layout

Clone this repository into a directory named perfeng-environment. Its parent is
the workspace root; the seven other repositories are siblings. Paths are resolved
relative to workspace.json, never the terminal's current directory. No absolute
machine paths, submodules, or copied component sources are used.

workspace.json is authoritative for repository names, URLs, and initial source
branches. perfeng.code-workspace mirrors its folders for VS Code; validation
detects drift between them. The prototype and external working-notes folder
are intentionally not included. Workspace membership is source-development
composition only; it does not grant an Argo CD AppProject permission to use a
repository as workload desired state.

## Bootstrap: PowerShell

Install Git, Python 3.12, and uv; configure GitHub access through Git's credential
manager or SSH configuration before cloning. From the desired parent directory:

```powershell
git clone https://github.com/stanimirivanov/perfeng-environment.git
Set-Location perfeng-environment
uv sync --locked
uv run --locked python scripts/workspace.py validate
uv run --locked python scripts/browser_host.py validate
uv run --locked python scripts/browser_environment.py --help
uv run --locked python scripts/workspace.py bootstrap --dry-run
uv run --locked python scripts/workspace.py bootstrap
uv run --locked python scripts/workspace.py status
code perfeng.code-workspace
```

## Bootstrap: shell

```sh
git clone https://github.com/stanimirivanov/perfeng-environment.git
cd perfeng-environment
uv sync --locked
uv run --locked python scripts/workspace.py validate
uv run --locked python scripts/workspace.py bootstrap --dry-run
uv run --locked python scripts/workspace.py bootstrap
uv run --locked python scripts/workspace.py status
code perfeng.code-workspace
```

If the repositories already exist, skip the git clone step. Do not clone over
an existing directory. VS Code can also open the workspace through File > Open
Workspace from File. IntelliJ's root .idea directory is ignored.

The workspace script itself uses only the Python standard library. A preinstalled
Python 3.12 can run it directly without uv; uv pins the development tools.
The separate cluster tooling also requires the locked PyYAML dependency.

## Local operator tasks

Install [Task 3](https://taskfile.dev/docs/installation/) in addition to the
prerequisites in the focused operating guides. The Taskfile is a small,
cross-platform façade over the existing Python commands; orchestration safety,
credential input, identity validation and timeouts remain in those commands.

```sh
task --list
task bootstrap
task status
task smoke
task destroy
```

- `task bootstrap` creates or validates `perfeng-local`, establishes external
  credential identities, reconciles the stateful GitOps foundation and
  initializes the local S3 bucket. The two required GitHub tokens are read from
  their documented environment variables or requested through hidden input.
- Bootstrap intentionally stops before reviewed policy publication and the
  control-plane database, migration and runtime-input stages. Continue with the
  [fresh-cluster staged bootstrap](docs/local-gitops.md#fresh-cluster-staged-bootstrap).
- `task status` does not apply desired state. It validates the workspace,
  cluster and complete GitOps environment and fails while a staged bootstrap is
  incomplete. SeaweedFS semantic health uses a bounded diagnostic Job retained
  for inspection.
- `task smoke` first requires complete platform health, then creates one retained
  end-to-end Run and its evidence.
- `task destroy` displays an interactive warning before invoking the existing
  exact-name deletion boundary. It permanently deletes all cluster-local data.

The detailed Python commands remain the diagnostic, preview and recovery
interface. The Makefile remains the engineering validation interface; Task does
not duplicate formatting, linting or test targets.

## Safety and status

Bootstrap validates the manifest and checks all existing destinations before
cloning anything. It clones only missing paths, initially checking out the
configured branch. Existing checkouts are never fetched, pulled, switched,
reset, stashed, or edited, even if on a different branch or dirty.

A file, ordinary directory, wrong origin, nested checkout, inaccessible Git
repository, or redirected/symlinked path is a conflict. Fix conflicts explicitly;
bootstrap does not attempt automatic repairs. HTTPS and common GitHub SSH
origin spellings for the same repository are accepted. No credentials belong
in the manifest. Git ownership errors require owner review; the script never
adds safe.directory exceptions or changes global Git configuration.

A destination is exclusively reserved before clone. A failed clone retains
its partial directory for inspection; successful earlier clones also remain.
Resolve the failed directory manually before retrying. There is no rollback or
automatic deletion.

Status is local-only: it reports branch (including detached/unborn states),
commit, dirty state, and missing/conflicting checkouts. It does not claim remote
freshness or report ahead/behind counts. Exit code is zero when all checkouts
are valid, including dirty or non-main branches, and one for missing/conflicting
checkouts or command errors. Bootstrap dry-run treats missing paths as planned
clones; conflicts return one. Runtime clone errors return one.

## Source refs versus deployed artifacts

source.branch is an initial clone default, not a deployment pin, release,
cross-repository compatibility guarantee, or instruction to update existing
checkouts. Current defaults are main. Only branch refs are supported in this
manifest version; source snapshot/tag pinning can be introduced separately.

Environment composition selects immutable image digests, chart versions, and
contract artifacts in separate deployment configuration. Do not put image tags
or production versions in workspace.json. The initial local control-plane lock
does not claim a production environment or a successful deployment.

## Development

Start with the repository working agreement and follow the linked sources of
truth:

- [Repository working agreement](AGENTS.md)
- [Contributor workflow](CONTRIBUTING.md)
- [Engineering standards](docs/development/engineering-standards.md)
- [Architecture decisions](docs/decisions/README.md)
- [Cross-repository roadmap](docs/roadmap/milestones.md)
- [Security policy](SECURITY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)

Contribution and documentation conventions include the required TL;DR for long
documents and the exact completion-report format used for PR-sized work.

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/workspace.py validate
uv run --locked python scripts/gitops.py validate
uv run --locked python scripts/validate_cluster.py
```

On systems with Make, `make validate` runs the same non-mutating checks and
tests. The individual commands above remain the portable, authoritative
interface.

Tests use temporary paths and mocked clone operations, never GitHub writes.
CI is configured to validate on Windows and Linux.
