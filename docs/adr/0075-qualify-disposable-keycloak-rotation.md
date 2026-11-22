# ADR 0075: Qualify disposable Keycloak signing-key rotation

- Status: Proposed
- Date: 2026-11-14

## TL;DR

- Mutate keys only inside the already authorized disposable fixture realm.
- A separate fixture service account has the required realm-management privileges.
- Verify new issuance, passive overlap and retirement against actual fresh JWKS.
- Keep Go cache/refresh/outage and BFF admission as explicit subsequent gates.

## Decision and boundaries

Extend the existing immutable Keycloak 26.8.2 fixture under control-plane issue
#356 and accepted ADR 0046. Environment composition owns the provider's realm,
client and key lifecycle; control-plane owns verification/cache behavior. Do not
put provider administration into the BFF or the Go verifier.

A new private signing key authenticates a separate fixture-only service account.
Assign only the required `view-realm` and `manage-realm` roles. These are coarse
realm privileges: they are not key-only authorization, and this is not a
production credential pattern. The standard Admin API scope/audience remain
mandatory. CI and BFF profiles retain their original scope/role boundaries.

Use the supported Admin REST component operations on the exact observed key
provider after verifying its type and realm parent. Create a higher-priority RSA
provider, explicitly passivate the old one, then retire it only after overlapping
verification and new-key issuance pass. Reauthenticate the fixture administrator
under the new key before deleting the old provider. Never alter an existing host,
shared realm, persistent identity or production issuer.

## Alternatives and consequences

Synthetic JWKS rotation cannot establish actual provider issuance/lifecycle.
Shelling out with an administrator bearer risks leaking it through argv. A master
administrator would add unnecessary authority. Use fixed-origin TLS REST in
memory and a realm-local service account instead; preserve all existing fixture
bounds, private storage, disabled logs and owned cleanup. No dependency is added.

The provider gate cannot retire the Go cache gate. Keep NOT_ADMITTED; the next
control-plane slice must select this fixture by immutable pin and qualify bounded
refresh, key retirement and unavailable/stale trust with the actual Go adapters.

## Verification and rollback

Offline TDD rejects unchanged active IDs, absent overlap, incomplete retirement,
unsafe component paths and premature receipt claims. The explicit real gate must
also preserve all machine/human/exchange checks. Destroy the owned container and
private directory; retain no credential or generated receipt in Git. Rollback
reverts this fixture extension without touching application state or issuer trust.

References: pinned [Admin API authentication](https://github.com/keycloak/keycloak/blob/26.8.2/services/src/main/java/org/keycloak/services/resources/admin/AdminRoot.java),
[scope provisioning](https://github.com/keycloak/keycloak/blob/26.8.2/services/src/main/java/org/keycloak/protocol/oidc/OIDCLoginProtocolFactory.java),
and [component operations](https://github.com/keycloak/keycloak/blob/26.8.2/services/src/main/java/org/keycloak/services/resources/admin/ComponentResource.java).
