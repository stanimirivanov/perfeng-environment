# Disposable Keycloak identity qualification

## TL;DR

- Preview offline; run the fixture only with explicit `--execute` authorization.
- The image lock selects official Keycloak 26.8.2 by Linux amd64 manifest digest.
- Test-only TLS and client keys, realm and database are fresh for each invocation.
- Passing machine and human/exchange checks still emits `NOT_ADMITTED`: provider/rotation/BFF gates remain.

## Commands and ownership

On Linux amd64 with a local Unix-socket Docker daemon and UID 1000, acquire exactly the image named in
[the lock](../tests/qualification/keycloak/fixture.lock.json) with `docker pull`.
The command never downloads a mutable tag or installs a tool. Use the locked
Python environment:

```sh
uv run --locked --offline python -m scripts.keycloak_qualification
uv run --locked --offline python -m scripts.keycloak_qualification --execute
```

`make keycloak-qualification` runs the focused unit tests followed by the explicit
execution path. Remote Docker endpoints/contexts are refused.

Preview makes no Docker or network call and generates no credential. Execution
requires separate authorization; issue #389 records the owner's authorization
for this qualification run. One uniquely labelled container publishes only its
HTTPS port on 127.0.0.1. It has bounded memory, CPUs and PIDs, no added capabilities
or bootstrap administrator, and no Docker log retention. The official unbuilt
image performs first-start augmentation in its disposable writable container
layer; the dev-file database is on tmpfs. This is not production storage.

Fresh TLS and client signing keys are separate. Private files are mode 0600 in
a mode-0700 temporary directory on `/dev/shm`; the process must run as UID 1000
so the image's unprivileged user can read them. The fixture trusts only its fresh
TLS certificate, uses fixed loopback HTTP destinations, does not follow redirects
and never puts credentials in argv or output. Cleanup checks the container label,
removes that exact container and deletes the temporary directory on success or
failure. The acquired digest-pinned image remains in Docker's cache.

## Proof and limits

The real provider must accept a CI private-key JWT assertion, issue a signed
access token with the exact enrolled test subject, CI caller, API audience and
five-minute maximum lifetime, and deny a wrong signing key and password grant.
The Python cryptographic observation check is not the control-plane verifier.
The protocol fixture also submits the actual Keycloak human login form, then
redeems an authorization code using PKCE S256 and a private-key-authenticated
login client. It validates callback state/issuer and the signed ID-token nonce,
subject and audience before using the initial access token. That token targets
only the delegate, never the API. A separate private-key-authenticated delegate
uses supported standard exchange for the same subject and the API audience only.
No exchanged refresh token is allowed. Password grants and machine credentials
for human clients, wrong PKCE, code replay, ID-token substitution, and exchange
by the login/CI client must fail. Code replay runs last because providers may
revoke credentials derived from a replayed code.

The user/password, login key and delegate key are fresh and distinct from CI and
TLS keys. Browser-protocol cookies/assertions/tokens stay in memory. The user
password is only in the private imported realm file and HTTPS form POST. The
fixture disables proxies and automatic redirects, bounds responses, validates the
same-origin login form action, and captures the fixed callback without contacting
it. This is an HTTP protocol test, not a browser or Console session implementation.
No credential or user is retained; no permanent issuer identity is selected.

Only non-secret check names, the image and proof limits reach the JSON receipt.
No credentials, tokens, private keys or generated receipt are committed. This
fixture does not activate public serving or complete control-plane #356. Real
control-plane conformance, rotation and BFF browser/shared
pilot gates remain explicit. See [ADR 0074](decisions/0074-qualify-disposable-keycloak.md).

The client attributes and subject mapper follow the pinned upstream sources:
[exchange controls](https://github.com/keycloak/keycloak/blob/26.8.2/server-spi-private/src/main/java/org/keycloak/protocol/oidc/OIDCConfigAttributes.java)
and [immutable subject mapping](https://github.com/keycloak/keycloak/blob/26.8.2/services/src/main/java/org/keycloak/protocol/oidc/mappers/SubMapper.java).
Expectations are declared before execution; observed output never sets subject,
audience, caller or token-type expectations. A machine-only receipt retains the
human gate until every declared human check has passed.
