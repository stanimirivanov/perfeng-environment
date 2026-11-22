# ADR-0006: Use an opt-in loopback HTTPS gateway for native Windows integration

- Status: Proposed
- Date: 2026-10-11
- Milestone: M03 — Native Browser Decision Loop
- Deciders: Project owner
- Supersedes:
- Superseded by:

## TL;DR

- Run Caddy on the same Windows host as the worker; expose three loopback HTTPS origins.
- Forward the existing API and S3 Services through identity-checked, bounded kubectl sessions.
- Serve a repository-owned search fixture with the existing journey selectors.
- Keep CA trust installation an explicit operator decision; never disable TLS verification.
- Do not change workload ownership, deployment pins, authentication or native acquisition in this change.

## Context

The local Kubernetes Services use internal HTTP. Native Worker requests and
presigned upload grants require HTTPS. The control plane can now sign uploads
for a separate HTTPS origin without moving internal reads. A controlled browser
target and trusted worker connectivity are prerequisites for selecting the
immutable native bundle; neither exists in the current deployed selection.

## Decision

Use Caddy 2.11.7 with explicit loopback listeners for API (8443), S3 (9443) and
the controlled browser target (10443). kubectl forwards the existing Services
to 18080 and 18333. Each foreground session is bounded to one hour and requires
an explicit execution path; previews are offline. No workload is deployed or
reconciled by this gateway, so Argo CD remains the only workload owner.

Live serving additionally refuses builds older than Go 1.26.9 or x/net v0.60.0,
missing build provenance and module replacements. The official binary built
with Go 1.26.8 is suitable only for offline adaptation here. Use the documented
isolated source build and binary advisory audit for serving; version minima
alone do not establish that the whole dependency graph is free of findings.

The HTTP proxy preserves the original Host, URI and signed headers. Internal
S3 reads stay on the cluster endpoint. Caddy manages a private local CA under
ignored state but does not install trust. The operator reviews its fingerprint
and explicitly trusts the public CA for the Windows browser and Node client.
The admin API, automatic HTTP redirects and request logging are disabled.
Operational logs are discarded because upstream failures can contain presigned
query strings; bounded credential-free probes provide diagnosis instead.

The fixture is deterministic in content and selector behavior, not in latency.
It is an integration target, not a representative customer application or an
approved performance baseline. Its content fingerprint is not a host
qualification fingerprint and must never be substituted for one.

## Alternatives considered

- Cluster ingress: useful for remote workers, but introduces ingress packaging,
  host routing and another infrastructure lifecycle before local acceptance.
- A custom Python TLS proxy: creates protocol and security maintenance that an
  established proxy already solves.
- Plain HTTP or TLS verification bypasses: violate the worker boundary.

## Consequences

- Local integration needs no DNS, public ports, cloud billing or cluster replacement.
- Three operator-owned foreground sessions must remain running and be restarted
  after Pod replacement or expiry. A remote Windows worker is not supported.
- CA keys must remain private; trust removal requires explicit fingerprint review.
- The application deployment and native bundle remain separately pinned. A
  later promotion must prove a signed PUT followed by internal read-back and
  registration; readiness and anonymous S3 denial are not that proof.

## Security and operations

Use the [native connectivity guide](../native-worker-connectivity.md). Stop
foreground sessions to remove exposure without touching stored objects, PVCs,
Secrets or Applications. Never reuse the GHCR token as a worker credential.
Trust-store writes, credential provisioning, image promotion and live artifact
publication require their own explicit operator actions.

## Validation

Unit tests cover offline preview, foreign-cluster rejection, Service identity,
bounded execution and responses, verified TLS, no redirects/proxies, fixture
identity, anonymous storage denial and safe errors. CI adapts the real Caddyfile
using official checksum-verified binaries on Windows and Linux without starting
a server or installing trust. Live checks and final native acceptance remain
explicit and cannot be inferred from those tests.
