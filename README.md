# perfeng-environment

Workspace bootstrap and development/production environment composition.
The workspace commands manage source checkouts only. Separate, preview-first
[local Kubernetes commands](docs/local-kubernetes.md) now provide the kind and
[sample API foundation](docs/sample-api.md); production composition is not implemented.
An optional [local PostgreSQL deployment](docs/local-postgres.md) adds persistent
metadata storage with generated credentials; it is deployed and checked separately.
An optional [SeaweedFS object store](docs/local-object-store.md) adds S3 storage
with upload/download and restart-persistence checks.
The [local k6 capture workflow](docs/local-k6-run.md) runs the digest-pinned
checkout smoke workload and uploads checksum-verified raw artifacts to SeaweedFS.
The uploader uses [restricted storage credentials](docs/storage-access.md);
existing installations require the documented object-store upgrade.

## Layout

Clone this repository into a directory named perfeng-environment. Its parent is
the workspace root; the six other repositories are siblings. Paths are resolved
relative to workspace.json, never the terminal's current directory. No absolute
machine paths, submodules, or copied component sources are used.

workspace.json is authoritative for repository names, URLs, and initial source
branches. perfeng.code-workspace mirrors its folders for VS Code; validation
detects drift between them. The prototype and external working-notes folder
are intentionally not included.

## Bootstrap: PowerShell

Install Git, Python 3.12, and uv; configure GitHub access through Git's credential
manager or SSH configuration before cloning. From the desired parent directory:

```powershell
git clone https://github.com/stanimirivanov/perfeng-environment.git
Set-Location perfeng-environment
uv sync --locked
uv run --locked python scripts/workspace.py validate
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

Future environment composition must select immutable image digests, chart
versions, and contract artifacts in separate deployment configuration. Do not
put image tags or production versions in workspace.json. No such deployment
lock or production environment is claimed by this change.

## Development

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/workspace.py validate
```

Tests use temporary paths and mocked clone operations, never GitHub writes.
CI is configured to validate on Windows and Linux.
