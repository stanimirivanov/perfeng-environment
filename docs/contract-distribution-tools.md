# Contract distribution verifier qualification

## TL;DR

- Prepare the exact Linux x86-64 verifier outside this repository.
- Run the qualification gate with the pinned Python interpreter and verifier.
- The public fixture exercises offline signature verification and certificate identity policy.
- A passing fixture qualifies the verifier path for this platform. Perfeng publisher admission belongs to the hosted publication task.

## Prepare and qualify

Use a reviewed checkout as the source of the pins in
[the tools lock](../contracts/distribution-tools.lock.json). The checkout and
Python environment are bootstrap trust inputs. The gate checks the interpreter
version and executable digest. Prepare the interpreter and its standard library
through the trusted repository toolchain setup before running the gate.

Acquire the Linux amd64 archive of GitHub CLI v2.103.3 from its
[upstream release](https://github.com/cli/cli/releases/tag/v2.103.3).
Compare its SHA-256 to the reviewed archive pin before extracting its binary to
a private local directory. Pass the prepared binary by absolute path.

```sh
make contracts-tools-check CONTRACTS_VERIFIER=/absolute/path/to/gh
```

The check uses the GitHub CLI source fixture and trust root from revision
`63837fb778d6a39f02be0ce76ff89f2bc8d74bdc`. See the
[upstream fixture source](https://github.com/cli/cli/tree/63837fb778d6a39f02be0ce76ff89f2bc8d74bdc/pkg/cmd/attestation/test/data)
and [fixture licence](../tests/fixtures/contract-distribution/LICENSE).
The fixture's repository, source revision, source ref, reusable workflow and
signer revision are independent policy pins. They apply only to this fixture.

The gate verifies the executable and root bytes before copying them into a
private temporary directory. The subprocess uses an empty local configuration,
an explicit trust root and an on-disk attestation bundle. It inherits no GitHub
credentials. The positive case must pass before the gate checks that changed
repository, source and workflow identities each fail. Temporary files are
removed after the check.

## Scope and recovery

The recorded platform is Linux x86-64 with the pinned CPython 3.12.14 build.
A missing or different binary or trust root blocks qualification. Restore the
exact approved inputs or review a new set of pins and rerun qualification.
There is no automatic download, system CLI upgrade or fallback. This gate
does not install artifacts or authorize a Perfeng publisher. Reverting this
additive tooling has no effect on existing consumers.

## Inspect a local distribution

Pass the reviewed policy separately from the downloaded release material. It
contains the exact `manifestSha256`, the publisher run's `sourceDigest` and
`sourceRef` (`refs/heads/main`), and the reviewed `signerWorkflow` (repository
and workflow path) and `signerDigest` (workflow revision). The publisher run
revision is distinct from the historical Git revision named inside a
distribution. The inspector fixes both the publisher and signer repository
to `stanimirivanov/perfeng-contracts`. Prepare these policy pins through
independent review once the publisher is admitted.

```sh
uv run --locked --offline python -m scripts.contracts_inspect \
  --policy /absolute/path/to/reviewed-policy.json \
  --manifest /absolute/path/to/promoted.json \
  --archive /absolute/path/to/archive.tar.gz \
  --bundle /absolute/path/to/manifest-attestation.jsonl \
  --verifier /absolute/path/to/gh
```

The inspector verifies the attestation over the exact promoted manifest bytes,
then checks the archive hash, member inventory and member hashes. The manifest
schema is a byte-identical copy of `distribution/manifest.schema.json` from
`perfeng-contracts` revision `aae9004ec1e204d52df6afc7c7d36839c0eaa2f7`.
Inspection bounds compressed and expanded sizes before parsing the tar
stream. The reference profile admits local JSON Pointers and relative
references to other declared JSON members. A root identifier, when present,
must match its canonical `https://perfeng.dev/` member URI. Nested identifiers,
named fragments and network retrieval are unsupported.

Success prints a verified member inventory and leaves the inputs untouched.
The output is informational and cannot authorize reopening changed paths
for installation. The manifest binds the admission record's digest.
The inspector leaves interpretation of that record to the separate
admission protocol. Run `make contracts-inspect-check` for the local inspection
fixtures.
