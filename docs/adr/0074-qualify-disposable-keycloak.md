# ADR 0074: Qualify disposable Keycloak machine identity

- Status: Proposed
- Date: 2026-11-13

## TL;DR

- Environment composition owns a disposable real-provider fixture for control-plane #356.
- Use pinned official Keycloak, loopback TLS and a private-key-authenticated CI client.
- This first slice produces bounded machine-profile evidence, never full admission.

## Decision

Implement the explicitly authorized fixture as a local opt-in Python command,
using existing locked cryptography and standard-library TLS/HTTP plus Docker.
Keycloak 26.8.2 is Apache-2.0; the exact Linux amd64 manifest digest is reviewed
in the fixture lock. No library is added or upgraded. The replacement boundary
is `scripts/keycloak_qualification.py`, not the application authentication layer.

This implements the environment ownership assigned by accepted control-plane
[ADR 0046](https://github.com/stanimirivanov/perfeng-control-plane/blob/17985575c5f199c2375037f2591403bb6894dc4b/docs/decisions/0046-select-issuer-and-bff-delegation-trust.md).
The owner authorized a disposable provider and fresh test-only credentials.
Select no production issuer namespace, hosting, certificate, client credential or
persistent storage. Do not modify the existing cluster or applications.

Machine credentials use private_key_jwt. Human login and delegate clients remain
unusable until separately qualified: no password grant or CI service account is
allowed to stand in for a person. The receipt records NOT_ADMITTED even when all
machine checks pass; it cannot be consumed as authorization to enable serving.

## Alternatives and consequences

A production deployment would require lifecycle, backup, secret delivery and
shared connectivity decisions outside this slice. A mocked issuer cannot prove
actual grant or claim behavior. A mutable image tag weakens reproducibility.
The official image needs first-start build augmentation, so the disposable
container layer is writable; private files stay on tmpfs and are never retained.
The embedded dev-file database qualifies this fixture only, not PostgreSQL or
production Keycloak operation. UID 1000 and Linux amd64 are explicit limits.

## Verification and rollback

Offline TDD covers signed-profile rejection, grant separation, redaction and
non-mutating preview. The authorized real run must verify machine issuance over
TLS and negative grants, then remove its own uniquely labelled container and
private directory. Full `make verify` remains offline. Delete the fixture code
on rollback; retain no identity migration or durable state. Real human exchange,
control-plane token acceptance, rotation and BFF/browser qualification follow
in separately reviewable slices. Package-wide image vulnerability scanning is
not a claim made by this fixture or by the release pin.
