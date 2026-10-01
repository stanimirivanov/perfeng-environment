# Dependency policy

## TL;DR

- Use Python 3.12 and the exact `uv.lock`; bootstrap with `uv sync --locked`.
- Add a dependency only for a current repository-owned need after license, maintenance, security, and replacement review.
- Keep live infrastructure CLIs explicit prerequisites rather than hidden Python dependencies.
- GitHub Actions may use reviewed release tags or commit pins; component and source selections retain their exact identities.
- Dependabot may propose grouped GitHub Actions updates; Python updates remain a manual, lockfile-aware workflow until the hosted updater supports the pinned uv toolchain reliably.

## Python dependencies

Runtime libraries belong in `project.dependencies`; development-only checks belong in dependency groups. This repository is not a component implementation, so dependencies must support environment composition or its deterministic validation. Prefer the standard library when it expresses the boundary clearly, but do not reimplement a maintained parser or protocol merely to avoid a justified dependency.

Every update preserves `uv.lock` and runs `make verify`. Security-sensitive or behavior-changing upgrades receive focused tests and release-note review. A lock change must not be mixed with unrelated product behavior.

## External tools

Helm, Git, Task, Docker, kind, kubectl, and Argo CD tools are operational prerequisites with different authority. `make doctor` reports the core engineering toolchain; focused operating guides define additional live prerequisites. Offline render checks may invoke Helm; cluster tools appear only in explicitly selected live paths. Tool acquisition, when automated, must use a reviewed version and repository-anchored integrity check. CI verifies Helm 4.1.4 against `.helm-linux-amd64.sha256` before extraction.

## GitHub Actions and immutable selections

Workflow `uses:` entries may use reviewed release tags or commit SHAs. The repository policy validator does not enforce one reference style. Deployment image digests, artifact checksums, lock content, and `actions/checkout` `ref:` values selecting a reviewed sibling revision remain exact immutable identities.

Automated dependency review is not required. Dependabot updates GitHub Actions only. Python dependency updates remain manual and MUST update `uv.lock` with the repository-pinned toolchain; every dependency pull request must pass the same checks as other changes.

## Supply-chain response

`make supply-chain` verifies lock consistency and scans installed dependencies for known vulnerabilities. A finding is triaged for reachability, severity, available fixes, and operational exposure. Exceptions must be narrow, documented, time-bounded, and reviewed; a scanner outage or unavailable advisory source is reported as not run rather than passed.
