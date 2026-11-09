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

## Activate a local projection

Use a dedicated existing directory owned by the current user and writable
only by that user. Prepare a reviewed selection JSON file independently from
the release. It names the expected `sourceRevision` and a `members` object
keyed by every provider member path. Each value names its expected `sha256`
and its relative `destination` in the projection. Pass the separate
publisher policy used by inspection alongside the selection.

```sh
target=$(mktemp -d)
uv run --locked --offline python -m scripts.contracts_materialize --execute \
  --manifest manifest.json --archive archive.tar.gz --bundle bundle.jsonl \
  --policy publisher.json --verifier /absolute/path/to/gh \
  --selection selection.json --target "$target"
```

The command verifies once, then stages the captured member bytes. It prints
the path to the activated generation. The store's regular `current` file
names that immutable generation. A reader should read the pointer once and
use that generation for the entire operation. The installer keeps prior
generations available and refuses to overwrite mismatched files. Competing
writers receive a busy error and can retry after the active writer exits.
Each target and ancestor must be a real directory rather than a symlink.
This Linux implementation uses descriptor-relative directory traversal and
an advisory lock shared by materializer writers. Keep the store under trusted
local control and avoid external writers.

An interruption before pointer replacement leaves the prior generation
active. Interrupted staging may leave an inactive `.stage-*` directory or
a complete unreferenced generation. Keep the active generation in place and
inspect inactive state before any cleanup. Process interruption is covered
by the gate. Power-loss recovery is outside this task. Run
`make contracts-materializer-check` and `make contracts-inspect-check` for
offline verification.

## Materializer tool release

The release archive contains the materializer, its schema and trust inputs,
the pinned GitHub CLI, and the Python interpreter, standard library and locked
packages needed to run it. The build restores its own archive in a temporary
directory and exercises both materializer startup and the offline verifier
qualification fixture there. The runtime requires a compatible Linux x86-64
glibc host and procfs for the materializer's directory ownership checks.

Use a trusted reviewed checkout and the independently qualified verifier to
restore the tool. Obtain the archive and detached Sigstore bundle for the
exact reviewed release. Record the archive SHA-256, `linux-x86_64` platform,
and exact source and signer commit IDs in an independently reviewed JSON file
with fields `archiveSha256`, `platform`, `sourceDigest`, and `signerDigest`.
A digest downloaded alongside the release is insufficient to establish trust.

```sh
uv run --locked --offline python -m scripts.contracts_tool_release restore \
  --archive materializer.tar.gz --bundle materializer.sigstore.jsonl \
  --verifier /absolute/path/to/trusted/gh --pins /absolute/path/to/reviewed-pins.json \
  --target /absolute/path/to/new-tool
/absolute/path/to/new-tool/runtime/bin/python3.12 -IB \
  /absolute/path/to/new-tool/materialize.py --help
```

Restore requires a fresh target under an operator-controlled parent.
After a filesystem interruption use a new target. Retain the archive,
attestation and reviewed pins with the bootstrap verifier and trust inputs
for offline recovery. Keep earlier reviewed releases for rollback.

Before enabling hosted release publication, an administrator must configure
the `contract-tool-attestation` and `contract-tool-publication` environments
with required reviewers and protected-main restrictions, and enable immutable
releases. Set `PERFENG_TOOL_PUBLICATION_ENABLED=true` only after confirming
those protections. Dispatch with the exact reviewed main commit. The
publication job reserves the commit-specific tag, uploads both assets to a
draft, then makes the complete release visible. Investigate interrupted
drafts before any retry. Hosted publication and positive verification of
its provenance require a separate authorized exercise.
