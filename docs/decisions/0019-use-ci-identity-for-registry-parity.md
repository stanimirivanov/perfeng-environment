# ADR-0019: Use the ephemeral CI identity for registry parity

- Status: Proposed
- Date: 2026-10-21
- Milestone: M06 — Production Readiness
- Supersedes: None

## TL;DR

- The owner selected `GITHUB_TOKEN` with repository package access for disposable registry integration.
- Request `packages: read` only for the isolated CI job, alongside `contents: read`.
- Package owners must grant the environment repository Actions Read access to private packages.
- Keep credentials ephemeral/private; manifest access is distinct from image downloads and release acceptance.

## Decision and boundaries

The owner explicitly selected the ephemeral repository identity rather than a
stored PAT or deferring this gate. Exact k6/analysis manifests were anonymously
readable during investigation; kube-burner rejected anonymous access. A Git
checkout alone does not establish registry authorization.

The `maintenance-verifier` job requests only `contents: read` and `packages: read`.
Keep the same-repository/GitHub-hosted guards, private kubeconfig, non-persistent
checkout credentials and narrow artifact directory. No package write, visibility
change, long-lived token or new production permission is added.

Convert `GITHUB_ACTOR` and the existing private token file into an immutable
disposable Docker-config Secret with the production name/type/ownership labels.
The CI actor is distinct from retained-environment operator credential admission.
Generate before plaintext cleanup and environment removal; encoded Secret files
remain private and are removed by the exit handler. No credentials enter
Workflow parameters, arguments, raw uploaded logs or result JSON.

Package owners configure **Read** under **Manage Actions access** for
`stanimirivanov/perfeng-environment` on each private selected package. Workflow
YAML cannot establish that grant. This change does not alter package settings
or host authentication. Missing access must fail with guidance, never cause an
anonymous fallback or an automatic credential/permission repair.

## Verification, recovery and limits

The unchanged child must return all three pinned digests with zero worker exits
on first/repeat/corrected execution. A separate invalid credential must fail its
owning Job with an authorization signal. Only fixture administration replaces
test Secrets; the Workflow identity cannot mutate them or delete Jobs. Preserve
all four Job UIDs and compare complete Secret objects within each attempt.

`crane digest` establishes manifest/digest access, not layer downloads or a
guaranteed complete manifest-byte hash check as performed by the retained host
verifier. No retirement or full-parent acceptance is claimed. This CI identity
does not replace non-local secret delivery, submission admission or per-user
authorization. Reverting the job permission/fixture changes no retained resources.

Run focused tests, `make validate`, `make workflow-lint` and the disposable kind
job. Offline tests cannot prove package-side authorization; CI must establish it.
See the [fixture guide](../release-workflows-integration.md#execution-registry-manifest-access).

References: GitHub's [package permissions](https://docs.github.com/en/packages/learn-github-packages/about-permissions-for-github-packages)
and [workflow access settings](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility#ensuring-workflow-access-to-your-package).
