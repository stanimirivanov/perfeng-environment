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

Verified resource-metrics selection locks `cryptography` 50.0.2 (Apache-2.0 OR
BSD-3-Clause), plus CFFI (MIT-0) and pycparser (BSD-3-Clause), matching the selected
provider's runtime requirement. It validates the public CA without custom ASN.1
or shell parsing and supports isolated provider health execution. Private key
generation/handling remains provider-owned. The adapter boundary is
`scripts/resource_metrics_release.py`; existing dependencies are not upgraded.

Kubernetes qualification uses maintained MIT-licensed `jsonschema` for the
vendored Draft 2020-12 evidence schema, with remote resolution disabled. The
local adapter is its replacement boundary. Only the required core graph is
installed. RFC 3339 calendar/time-zone checks use the standard library plus the
pinned timestamp pattern instead of the unrelated URI/email/duration format
dependency set. Existing dependencies are not upgraded with this admission.

Helm, Git, Task, Docker, kind, kubectl, and Argo CD tools are operational prerequisites with different authority. `make doctor` reports the core engineering toolchain; focused operating guides define additional live prerequisites. Offline render checks may invoke Helm; cluster tools appear only in explicitly selected live paths. Tool acquisition, when automated, must use a reviewed version and repository-anchored integrity check. CI verifies Helm 4.1.4 against `.helm-linux-amd64.sha256` before extraction.

The authorized contributor delivery workflow additionally requires GitHub CLI
(`gh`) and an existing authenticated account with access to the target repository.
It is not a Python dependency, validation prerequisite or cluster credential.
Use an existing executable outside PATH when necessary; do not install, log in,
change credential scopes or alter machine-wide PATH implicitly.

## GitHub Actions and immutable selections

The opt-in native gateway uses Caddy 2.11.7 (Apache-2.0); see the
[connectivity guide](../native-worker-connectivity.md) for the replacement
boundary and patched-toolchain requirement for live serving. Its official
Linux archive SHA-512 values are pinned in
`native-gateway/caddy-release.json`. CI uses checksum-verified binaries solely
for offline adaptation. Changes to these pins require a release/security review
and actual Caddyfile adaptation, not a text-only configuration test.

Workflow `uses:` entries may use reviewed release tags or commit SHAs. The repository policy validator does not enforce one reference style. Deployment image digests, artifact checksums, lock content, and `actions/checkout` `ref:` values selecting a reviewed sibling revision remain exact immutable identities.

Automated dependency review is not required. Dependabot updates GitHub Actions only. Python dependency updates remain manual and MUST update `uv.lock` with the repository-pinned toolchain; every dependency pull request must pass the same checks as other changes.

## Supply-chain response

[ADR 0029](../decisions/0029-require-patched-native-gateway-toolchain.md) records
the owner's Go 1.27.3 requirement following the reachable crypto/x509 finding
[GO-2026-6809](https://pkg.go.dev/vuln/GO-2026-6809). Build/audit modules, CI,
recorded metadata, smoke fixtures and serving verification must agree. Retain
the old selected asset's truthful Go 1.26.9 metadata but reject new setup/start
until a patched release is separately published/selected. No advisory exclusion
or symbol-level gate reduction is permitted.

The gateway uses govulncheck's binary symbol-level exit status: callable findings
block publication. Package/module-only observations remain visible in verbose
CI logs and require review. On 2026-10-13 the locked binary has no symbol or
imported-package findings; its x/crypto module triggers
[GO-2026-5932](https://pkg.go.dev/vuln/GO-2026-5932) for the unmaintained OpenPGP
package, which is not imported into this executable. This is a reachability
assessment, not an advisory suppression or claim that the entire module is
vulnerability-free. Re-review this assessment for each release/dependency update.

Serving uses a separate `native-gateway/serving-release.json` selection. The
Linux publisher builds the standard entry point as a dependency with the
checked-in `native-gateway/build` and `native-gateway/audit` Go module locks,
then audits the executable and runs an isolated real TLS smoke. The main-only
publisher never overwrites releases or selects its own output. Explicit setup
downloads only the Git-selected asset/checksum, verifies its actual metadata,
and never edits PATH or certificate trust. A null serving release deliberately
blocks installation until publication and review; the adaptation archive is
not a fallback. Go exists only in this developer/CI boundary, not on operators'
normal installation path. See [ADR 0010](../decisions/0010-manage-native-connectivity-workflow.md).


`make supply-chain` verifies lock consistency and scans installed dependencies for known vulnerabilities. A finding is triaged for reachability, severity, available fixes, and operational exposure. Exceptions must be narrow, documented, time-bounded, and reviewed; a scanner outage or unavailable advisory source is reported as not run rather than passed.
