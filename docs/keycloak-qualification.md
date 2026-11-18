# Disposable Keycloak identity qualification

## TL;DR

- Preview offline; run the fixture only with explicit `--execute` authorization.
- The image lock selects official Keycloak 26.8.2 by Linux amd64 manifest digest.
- Test-only TLS and client keys, realm and database are fresh for each invocation.
- Provider rotation/retirement now runs too; Go cache and BFF gates still block admission.

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

## Disposable signing-key lifecycle

After machine and human protocol checks, a separate fresh private-key client
administers this temporary realm only. Its service account has `view-realm` and
`manage-realm`, the pinned release's `kc-admin-api` scope and exact realm Admin
API audience. These are coarse realm-management privileges, not key-only RBAC.
No CI/login/delegate client receives them and no master administrator is created.

The gate checks the initial verified token against the active RS256 metadata,
validates its provider type and parent realm, creates a priority-200 RSA provider,
and explicitly makes the old provider passive. Fresh JWKS must contain both
keys; new issuance must use a different active key. It then obtains administration
under the new key before deleting only the observed old provider. Fresh JWKS
must omit the old key and reject its still-valid token, while old-snapshot
verification proves that refusal was not token expiry. Tokens minted before and
after retirement under the new key must verify. CI must be denied Admin API access.

This is provider lifecycle evidence using real keys, real tokens and real JWKS.
It is not Go-cache retirement, refresh-rate/freshness or outage qualification.
The complete provider checklist changes the pending `real-key-rotation` gate to
`control-plane-real-key-rotation`; partial observations do not. The control-plane
must advance its pinned fixture and rerun its own qualification before claiming
those cache semantics. See [ADR 0075](decisions/0075-qualify-disposable-keycloak-rotation.md).

Only non-secret check names, the image and proof limits reach the JSON receipt.
No credentials, tokens, private keys or generated receipt are committed. This
fixture does not activate public serving or complete control-plane #356. Real
control-plane conformance/cache rotation and BFF browser/shared
pilot gates remain explicit. See [ADR 0074](decisions/0074-qualify-disposable-keycloak.md).

The client attributes and subject mapper follow the pinned upstream sources:
[exchange controls](https://github.com/keycloak/keycloak/blob/26.8.2/server-spi-private/src/main/java/org/keycloak/protocol/oidc/OIDCConfigAttributes.java)
and [immutable subject mapping](https://github.com/keycloak/keycloak/blob/26.8.2/services/src/main/java/org/keycloak/protocol/oidc/mappers/SubMapper.java).
Expectations are declared before execution; observed output never sets subject,
audience, caller or token-type expectations. A machine-only receipt retains the
human gate until every declared human check has passed.

## Ephemeral human code-pair observer — M05-PR52o

The Python execution API accepts an optional keyword-only `observe_code_pair`
callback. `human.qualify` calls it after the existing signed ID/initial-access
checks and bounded candidate-envelope checks, before delegated exchange or code
replay. Its frozen `UnverifiedCodePair` contains only issuer, nonce, the integer
wall-clock millisecond instant immediately before the accepted token POST,
id_token, access_token, expires_in (1..300), and stage=`unverified-code`.
The timestamp includes the POST/response interval, not authorization initiation.
It is captured after client assertion signing. The name deliberately grants no
Console verification, consumed-transaction or session authority.

`execute(observe_code_pair=...)` additionally supplies a frozen `CodePairFixture`
with that pair and the fresh public TLS trust anchor as `tls_ca_pem`. The test-only
self-signed certificate retains its 127.0.0.1 SAN and now declares critical
BasicConstraints CA=true/path_length=0, matching the Console's already-qualified
explicit-CA requirement. No Console TLS check is weakened. No password, refresh
token, assertion, signing key or cookie is part of the handoff.

Both representations disable default repr output. This prevents accidental default
formatting, not deliberate serialization or retention. A trusted consumer must keep
candidates in memory, bound its work, and never log, put them in argv, write them to
files or include them in receipts. Consumers must raise on failed qualification;
return values are ignored and add no checks or admission to the fixture receipt.
Ordinary callback exceptions become a fixed ValueError without their diagnostic
text, abort subsequent protocol steps and retain the existing owned-container and
temporary-directory cleanup. Process control exceptions keep their normal semantics.

The command-line options, preview path and default receipt/check list are unchanged.
A no-op observer still leaves admission NOT_ADMITTED. Offline protocol tests cover
ordering, timing, immutability/redaction, bounds, refused/failed responses, callback
failure, fresh CA constraints and cleanup. This interface-only prerequisite does
not run Keycloak, invoke the Console verifier, or claim a new real-provider receipt.
The next Console slice must pin the merged fixture, supply a bounded consumer and
obtain that actual-provider evidence before production/runtime admission. Revert the
optional API/tests/docs together if needed; no retained credential migration exists.

## Verified public key-phase observer — M05-PR52q

The Python execution API additionally accepts optional `observe_key_phase`.
`rotation.qualify` invokes a synchronous observer with a frozen, repr-redacted
`KeyPhase` containing only phase, issuer, old_key_id and new_key_id. `execute`
wraps it in a separate frozen `RotationFixture` with the same fresh public
`tls_ca_pem`. No token, password, assertion, administrative bearer, private key,
provider component data or raw JWKS crosses this boundary.

The declared sequence is independent of any Console output:

| Phase | Independently established boundary | IDs |
|---|---|---|
| initial | Old token/provider binding, owned RSA component and CI administration denial verified; before creating the new provider | old ID; new ID is None |
| overlap | Old is passive, both IDs are published, both tokens verify and new issuance uses the distinct active ID; before retirement | old and new IDs |
| retired | Fresh JWKS rejects the still-valid old token, accepts the new token and surviving issuance retains the new ID | old and new IDs |

Invalid observations never emit the failed phase. A consumer exception is scrubbed
into a fixed ValueError, stops later lifecycle operations and retains the existing
owned-container/temporary-directory cleanup. Process control exceptions keep their
normal semantics. Consumers must bound their work; return values are ignored and
add no admission/checks. Default command behavior and receipt remain unchanged.

This interface has offline protocol/cleanup evidence only; it is **not a Console
shared-cache rotation receipt**. The next Console slice must pin the merged fixture
and use one persistent consumer through all phases. Its expectations must preserve
the approved freshness policy: a retired provider key may remain usable in an already
fresh cached snapshot until its five-minute limit, then be refused after an expired
snapshot is refreshed. Provider retirement does not guarantee immediate cache revocation.
No clock policy, identity activation, credential deployment or live provider execution
is part of this interface-only change. Roll back the optional observers/wrappers,
tests and this guide together; keep the existing provider checks and NOT_ADMITTED gate.
