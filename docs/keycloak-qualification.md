# Disposable Keycloak machine identity qualification

## TL;DR

- Preview offline; run the fixture only with explicit `--execute` authorization.
- The image lock selects official Keycloak 26.8.2 by Linux amd64 manifest digest.
- Test-only TLS and client keys, realm and database are fresh for each invocation.
- Passing machine checks still emits `NOT_ADMITTED`: human exchange and provider/BFF gates remain.

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
The login/delegate client shapes have no usable credentials in this slice;
no real human login or exchange is claimed.

Only non-secret check names, the image and proof limits reach the JSON receipt.
No credentials, tokens, private keys or generated receipt are committed. This
fixture does not activate public serving or complete control-plane #356. Real
human/delegate tokens, control-plane conformance, rotation and BFF browser/shared
pilot gates remain explicit. See [ADR 0074](decisions/0074-qualify-disposable-keycloak.md).
