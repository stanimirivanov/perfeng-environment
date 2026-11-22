# ADR-0024: Authorize the environment source for foundation Application parity

- Status: Proposed
- Date: 2026-10-22
- Milestone: M06 — Production Readiness

## TL;DR

- The owner approved the existing read token in a second immutable Argo CD repository Secret for the exact `perfeng-environment` Git URL in disposable trusted-main CI.
- Extend ADR 0023's configured source scope; add no credential rights, controller, stored token or PR exposure.
- Start with PostgreSQL Application adoption, preserving database/storage identities while the root remains skipped.

## Context and decision

Actual chart, server, Argo CD and complete readiness-parent acceptance leave
foundation Application ownership and root convergence unproven. PostgreSQL's
existing Application reads its chart from private `perfeng-environment`, whereas
ADR 0023 configures Argo CD repository access only for private `perfeng-k8s`.
The owner approved the existing contents-read token for this second exact URL
instead of requiring another credential. This does not authorize root activation
or retained-environment deployment.

Generate the existing PostgreSQL Application with only two CI overrides: pin
`source.targetRevision` to the request's selected commit and disable automatic
sync/self-healing/pruning. Production manifests retain their existing `main`
source and automation policy. Manually sync the actual chart through the already
selected controller after complete readiness acceptance. Reuse the native sync
adapter, original StatefulSet observer and authenticated PostgreSQL client Job.

Require fresh first/repeat operations with exact compared source/revision,
`Synced`/`Healthy` and no conditions. Change only the live Service port name to
`pg-ci-drift`; numeric PostgreSQL TCP routing remains intact. Require real
`OutOfSync` while the drift persists, then explicitly sync a fresh correction.
Do not fabricate status or repair through a competing Helm deployment.

## Security and state ownership

The same staging step/private file feeds two immutable repository Secrets with
the `repository` label and exact approved URLs. Reject other URLs, prefixes and
alternate protocols. These configuration constraints **do not attenuate the
token's underlying rights**. A compromised runner/controller could still exercise
its existing rights. Job permissions, checkout settings, token permission grants
and the separate approved `GITHUB_TOKEN` uses remain unchanged. PR CI receives
neither source credential. No wildcard `repo-creds` Secret is introduced.

Remove both temporary Secret documents and the private token file after creation
and on harness exit; the existing always cleanup also removes the runner token.
Repository Secrets persist only until owned cluster teardown. Keep Secret/state
snapshots and raw diagnostics private; upload only selected Application status,
native Workflow/client Job evidence and constant drift outcomes.

Adoption may legitimately roll the Pod to the selected template, but must preserve
StatefulSet/Service/PVC/PV identities and binding, claim template/image/Secret
references, credential bytes, roles/ACLs, schema OIDs/migration ledger and database
canary/sentinel OIDs/bytes. Repeat and Service-only correction must not roll the
adopted Pod. Record its before/after UID for later acceptance phases. No prune,
force, replace or implicit credential/storage repair is permitted. Other
Application specs/UIDs remain unchanged and the root stays skipped.

## Validation, rollback and limitations

Offline checks exercise exact source configuration, invalid pins, single/multiple
source native sync requests and refusal of stale, overridden or incomplete
controller evidence. Run full repository validation and Workflow lint. Actual
adoption, TCP access, drift correction and retention execute only after merge in
the existing trusted-main owned kind fixture; offline results are not live proof.

Rollback removes this CI phase and second repository configuration. It does not
alter retained environments. SeaweedFS Application adoption, root activation,
complete release adoption/promotion, recovery, production authorization and host
script retirement remain separate. Published verifiers/templates, chart defaults
and operator commands are unchanged; no retirement credit is introduced.
