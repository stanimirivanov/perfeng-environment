# ADR-0010: Manage verified native connectivity as one foreground workflow

- Status: Proposed
- Date: 2026-10-13
- Milestone: M03 — Native Browser Decision Loop
- Deciders: Project owner
- Supersedes: ADR-0006 tool acquisition and independent-session operation only

## TL;DR

- Acquire a reviewed, checksum-selected Windows binary through an explicit setup action.
- Resolve the managed executable by absolute path, never by the operator's PATH.
- Diagnose prerequisites independently before starting a bounded foreground session.
- Supervise the three identity-checked tunnels and Caddy together; stop only owned children.
- Preserve CA keys, workload ownership, credentials and explicit certificate-trust approval.

## Context

Manual compilation, terminal-local PATH changes and four independent sessions
produce avoidable failures. Mocked version output and offline adaptation of an
upstream archive do not prove that the serving binary meets our requirements.

## Decision

Keep Caddy as the HTTPS implementation and kubectl as the tunnel implementation.
The environment repository owns a thin acquisition, diagnostics and foreground
lifecycle layer. Setup is an explicit network/local-file operation, not part of
preview, doctor or ordinary validation. CI builds the reviewed Caddy entry point
as a dependency using checked-in Go module selections, audits the resulting
binary and tests its actual metadata and configuration. Publication is optional
and main-only. A serving release is usable only after its asset, checksum and
source provenance are explicitly selected; an unpublished release fails closed.

The selected binary lives in versioned ignored state. Setup never overwrites a
foreign or modified installation, edits PATH or mutates certificate trust.
Doctor reports independent prerequisites and safe recovery actions. Up owns
three kubectl children and one Caddy child for at most one hour. A partial
startup, child exit, timeout or interruption stops only those children and
retains CA state. Existing individual tasks remain diagnostic entry points.

Certificate creation follows private-directory permission validation. Windows
current-user and Node trust are reported separately; installation is always an
explicit fingerprint-reviewed operator action. Endpoint probes use the reviewed
public CA without changing system trust. Readiness never implies qualification,
worker authorization, signed upload or M03 completion.

## Alternatives considered

- Manual local builds: impose a Go toolchain and dependency-resolution workflow
  on every operator and do not verify the supported installation path.
- A Windows service or automatic restart loop: adds persistent lifecycle and
  hidden recovery authority beyond the operator-started M03 scope.
- A cluster ingress or custom TLS proxy: changes deployment topology or creates
  unnecessary protocol maintenance for this same-host integration boundary.

## Security and operations

Downloads use an exact reviewed asset and digest, bounded reads and no credential
forwarding across redirects. Tool metadata is safe bounded output; request logs,
tokens and signed URLs remain suppressed. Occupied ports are conflicts, never
permission to terminate another process. No resource adoption, credential/CA
rotation, cluster replacement or certificate installation is implicit recovery.

## Compatibility, rollout and rollback

The three origins, dedicated context and exact target-Pod checks remain unchanged.
Merge the workflow, publish its first reviewed binary explicitly, select its
returned checksum/provenance and run setup. Preserve existing local builds and CA
state. Rollback restores the reviewed tool selection and stops the owned session;
it does not undo workload state or remove unrelated certificate trust.

## Validation

Deterministic tests cover acquisition integrity, repeat setup, preview isolation,
metadata failures, independent diagnosis, ownership conflicts and child cleanup.
An explicit Windows integration tier exercises the actual built binary and local
foreground lifecycle without Kubernetes or trust-store mutation. Dedicated live
acceptance remains separate and must prove all three selected endpoints.
