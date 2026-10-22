# ADR-0023: Authorize private sources for disposable Argo CD acceptance

- Status: Proposed
- Date: 2026-10-22
- Milestone: M06 — Production Readiness

The later owner-approved [ADR 0024](0024-authorize-environment-source-for-foundation-parity.md)
extends the configured repository URLs for foundation Application parity. The
single-URL restriction below describes this original control-plane slice.

## TL;DR

- The owner approved the existing contents-read token for the pinned infrastructure checkout and an ephemeral exact-URL `perfeng-k8s` Argo CD repository Secret.
- Extend ADR 0022 only for trusted-main disposable CI; retained-environment restrictions remain.
- Prove actual manual reconciliation and real drift/correction before complete readiness-parent composition.

## Context and decision

Actual server and foundation chart acceptance leave the Application boundary
unproven: earlier CI uses a minimal CRD without an Argo CD controller. A real
repository server must read private `perfeng-k8s`. The repository job token cannot
read this sibling Git repository. ADR 0022 explicitly kept the source token
outside the cluster. The owner approved this additional exposure, including the
selected private `k8s-infrastructure` bootstrap checkout, instead of requiring
a newly provisioned dedicated credential.

Reuse the existing infrastructure validator, exact clean commit and its
versioned official Argo CD Kustomize bootstrap. Install after prior provider/server
acceptance so earlier paused-Application tests remain unchanged. Mark the root
`skip-reconcile` before installation. Manually sync only the selected control-plane
Application, preserving its exact two sources, destination and disabled automatic
sync. Do not copy controller manifests or change deployment technology.

Use native operations with pinned revisions and unique case markers. Require
fresh successful completion, exact compared sources/revisions, `Synced`/`Healthy`
and no conditions. Run the unchanged production Application observer twice.
Change the live Service target port, require controller-produced `OutOfSync`
while drift remains, then explicitly sync and observe correction. Never write
Application status. Generation may change across operation/status updates.

## Security and operations

Only two pinned checkout actions and a dedicated staging step receive the source
token. Checkouts disable persisted credentials, submodules and LFS. Staging uses
an exclusive private runner file; acceptance receives no source-token environment
variable. Create one immutable `repository` Secret for the exact `perfeng-k8s`
URL, not a wildcard `repo-creds` template. Argo CD reads this Secret.

This **does not narrow the token's underlying rights**. A compromised runner or
Argo CD component could exercise those existing rights. No stored credential,
permission grant or package setting changes. Existing `GITHUB_TOKEN` permissions
and approved cluster Secrets remain separate and unchanged. PR CI receives no
source credential; no retained-cluster use is authorized.

Keep credentials, Secret snapshots and raw diagnostics outside artifacts. Remove
runner credential files after Secret creation, on harness exit and in an always
cleanup step; cluster teardown removes the repository Secret. Do not upload
repository-server logs. The selected bootstrap has its normal authority only
inside owned kind. Disable root/automatic reconciliation, pruning, forced apply
and resource replacement. Preserve resource UIDs, PVC bindings, ConfigMap/Secret
data, schema/roles and database/object-store sentinels.

## Verification, rollback and limitations

Offline tests cover source refusal, credential configuration/staging, stale or
overridden operations and incomplete convergence. Full validation/render and
offline Workflow lint remain mandatory. Live reconciliation runs only after
merge in trusted-main; offline checks are not controller acceptance evidence.

References: [repository Secrets](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/#repositories),
[skip reconciliation](https://argo-cd.readthedocs.io/en/stable/user-guide/skip_reconcile/)
and [selected operation types](https://github.com/argoproj/argo-cd/blob/v3.5.2/pkg/apis/application/v1alpha1/types.go).

Rollback removes this CI phase, checkout and credential staging without touching
retained environments. Complete readiness/adoption parents, foundation Application
adoption, root activation, promotion, recovery/export, production authorization/HA
and script retirement remain separate. Verifiers remain two modules/708 physical
lines; no host entrypoint is retired.
