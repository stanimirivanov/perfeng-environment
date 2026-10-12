# ADR-0007: Package the controlled browser target as an immutable candidate

- Status: Proposed
- Date: 2026-10-12
- Milestone: M03 — Native Browser Decision Loop
- Deciders: Project owner (direction approved; implementation review pending)
- Supersedes: None
- Superseded by: None

## TL;DR

- Package the environment-owned integration fixture as an immutable container candidate.
- Use digest-pinned unprivileged NGINX rather than a new custom HTTP server.
- Publish only through an explicit main-branch workflow that verifies registry-addressable bytes.
- Keep current checkout-file gateway serving as pre-promotion only; image-backed acceptance requires a later deployment and gateway cutover.
- Preserve qualification, secrets, workload ownership and current deployment selections.

## Context

Run creation requires an approved candidate image identity. A locally served HTML fixture has source/content identity but no corresponding image, and assigning an unrelated image would misrepresent the tested candidate. The owner approved packaging the controlled target to resolve this gap.

## Decision

Keep fixture source in `perfeng-environment`, its existing owner, and publish `perfeng-native-search` from that source repository. Use maintained NGINX unprivileged with a pinned base manifest and narrow build context. Run as a non-root user with temporary writes confined to `/tmp`; serve only the reviewed files and health endpoint, reject modifying methods, disable caching and request logs, and retain the current CSP.

Validate packaging offline. Make local Docker build/verification explicit and isolated. Add a main-only, opt-in publisher with source/content labels, least-privilege package credentials, unique temporary tags and post-push digest pull/reverification. CI image checks never publish.

Do not deploy, add an Application, change gateway routing, select the service image or enable acquisition in this change. Kubernetes definitions remain owned by `perfeng-k8s`; their later promotion must run the exact candidate digest behind the HTTPS target before claiming image-backed acceptance.

## Alternatives considered

- Unrelated candidate image as a label: does not identify the tested application.
- A new custom application server: adds unnecessary protocol/runtime maintenance for static files.
- A separate production application repository: creates ownership/release overhead for an environment-only fixture.
- Automatically deploy or promote after push: bypasses reviewed immutable selection and host qualification gates.

## Consequences

The fixture gains honest image identity without changing its browser selectors or bytes. Publication adds one explicit release boundary, and base updates require review. The existing gateway remains usable for connectivity but is not image-backed evidence. Functional image verification is not an advisory scan or performance baseline.

## Compatibility and migration

No public contract, runtime bundle, profile fingerprint or deployed resource changes. The target content fingerprint remains stable. A later native selection must bind the published digest, target source, compatible component releases and real qualified environment evidence. Prior immutable versions remain untouched.

## Security and operations

Protect build context, pin base identity, bound resources and reads, expose verification only on loopback and clean up only exact invocation-owned containers. No Secrets or local evidence enter the image. GitHub publication is opt-in with environment repository association; candidate pull authorization is reviewed separately. Preserve private CA state and worker credentials.

## Validation

Run repository acceptance, focused metadata/provenance/HTTP/ownership tests and a real read-only unprivileged Docker image check. Exercise publication after merge, then validate the complete native bundle and real Windows decision loop separately.
