# Release request files

## TL;DR

- Operational requests require explicit review and the actual target cluster UID.
- `ci-preflight-fixture-v1.json` is a CI fixture with an all-zero UID. It cannot
  admit a real kind cluster and is not an operational release request.
- Its selection pins a separate reviewed Git commit and that commit's exact
  lock bytes. Preserve this historical fixture when later selections change.
- The test wrapper supplies its synthetic UID only to the identity child;
  the production preflight reads the real UID and must reject this file.

See the [request contract](../../docs/release-request.md) and
[Git preflight CI boundary](../../docs/release-workflows-integration.md#git-schema-and-preflight-identity).
Do not replace the fixture UID with a live UID, reuse its `expectedCurrent: null`
for an existing environment, or treat child evidence as release approval.
