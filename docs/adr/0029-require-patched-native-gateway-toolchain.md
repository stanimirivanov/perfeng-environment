# ADR-0029: Require the patched native gateway toolchain

- Status: Proposed
- Date: 2026-10-23
- Milestone: M06 — Production Readiness
- Amends: ADR 0006 and ADR 0010's serving toolchain policy

## TL;DR

- The owner requires Go 1.27.3 across gateway builds, auditing, CI, smoke fixtures and serving checks.
- Reject old-toolchain setup/start operations; preserve existing asset metadata, files, running processes and CA state.
- Publish/select a replacement separately; never relabel a Go 1.26.9 asset as patched or weaken binary auditing.

## Context and decision

The actual executable audit found reachable GO-2026-6809 in crypto/x509 under
Go 1.26.9. The official advisory fixes it in Go 1.26.10 and Go 1.27.3. The owner
selected Go 1.27.3 everywhere in the gateway boundary rather than retaining the
older compiler branch. This is an explicit compatibility/security change.

Pin both Go module directives to 1.27.3. CI reads its version from the build
module; build/audit execution, recorded metadata and isolated smoke selection
use the same reviewed version as serving checks. A regression enforces pin
agreement. Keep Caddy/dependency versions and symbol-level govulncheck unchanged.

The selected asset genuinely reports Go 1.26.9. Keep its immutable tag, asset ID,
checksum, source revision and compiler metadata intact. The selection reader now
refuses old toolchains before acquisition/state creation, and actual binary
metadata must report the reviewed Go 1.27.3 profile. This blocks new setup/start
operations until a patched release is separately published and selected. It
does not stop running processes, delete installed binaries, rotate CA state,
change trust stores or fabricate a replacement selection.

## Verification and release boundary

Use the existing build/audit and isolated Windows TLS smoke against the resulting
executable. Failed or unavailable audits never approve a package. Local dirty
builds remain development evidence, not publishable reviewed artifacts. Only
the existing explicit main publisher may create a release; selection remains a
separate reviewed step. This task does not dispatch publication or select output.

GO-2026-5932 concerns the unmaintained OpenPGP packages, with no fixed version.
Keep module-only observations visible and re-evaluate the actual candidate's
symbol/package reachability. Do not suppress the advisory or describe all of
x/crypto as vulnerability-free. Any callable finding still blocks approval.

Run repository validation, focused obsolete-toolchain/metadata/pin checks and
the actual candidate audit. Retain the original selection while awaiting the
replacement. Do not roll back to a known vulnerable compiler as a CI repair.

References: [GO-2026-6809](https://pkg.go.dev/vuln/GO-2026-6809),
[Go releases](https://go.dev/dl/), and
[GO-2026-5932](https://pkg.go.dev/vuln/GO-2026-5932).
