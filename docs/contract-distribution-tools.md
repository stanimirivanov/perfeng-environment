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
