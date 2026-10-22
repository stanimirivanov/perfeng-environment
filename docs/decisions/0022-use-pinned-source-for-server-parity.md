# ADR-0022: Use pinned private deployment source for server parity

- Status: Proposed
- Date: 2026-10-22
- Milestone: M06 — Production Readiness

## TL;DR

- The owner approved the existing contents-read token for one additional trusted-main checkout of the selected private `perfeng-k8s` commit.
- Keep checkout credentials unpersisted and out of the disposable cluster; retain existing job token permissions.
- Render the actual Helm chart and immutable Kustomize package rather than maintaining a server manifest copy.
- Prove native server observations and operational HTTP behavior; leave full readiness, Argo CD reconciliation and retained-environment deployment separate.

## Context and decision

Selected-image migration and runtime grants are now separate CI prerequisites.
The next boundary needs the actual released server, its chart and its mounted
runtime package. These sources are private. The repository-scoped job token
cannot read the sibling repository. The existing validation workflow already
uses `PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN` for this exact selected commit.
The owner explicitly approved using it for a pinned checkout in the trusted-main
acceptance job, with `persist-credentials: false` and no forwarding to the cluster.

The checkout action alone receives that secret input. Disable submodules/LFS,
pin the full selected revision, verify origin/HEAD and clean worktree, and check
the selected chart/bundle/package before rendering. Use the existing reviewed
Application Helm values and checksum-verified Helm version. Refuse inventory,
image or immutable ConfigMap byte mismatches. Do not rebuild the server, alter
its command or publish replacement packages.

## Security and operations

The source credential may cover other repositories; a pinned checkout does not
attenuate its underlying rights. This decision adds an exposure boundary to a
trusted main-only runner, not a new stored token or permission grant. It is not
passed as job environment, Git artifact credential, Secret, Helm value or
artifact. Checkout does not persist Git credentials. Existing `GITHUB_TOKEN`
contents-write/package-read permissions and its approved cluster Secrets remain
unchanged. Do not confuse those two credentials.

Fixture administration composes transient runtime/authentication/pull Secrets
from existing disposable credentials and applies the selected chart/package.
Preserve their identities across attempts. The real server uses the chart's
existing Job-controller RBAC. The HTTP main container masks the default API token
mount and asserts the token is absent; its Argo executor retains the ordinary
task-result identity. Kubernetes admission skips token injection into containers
that already mount that path, as implemented in the
[selected Kubernetes source](https://github.com/kubernetes/kubernetes/blob/v1.35.0/plugin/pkg/admission/serviceaccount/admission.go#L423).
This is container isolation, not a token-free Workflow Pod.

Only the owned GitHub-hosted disposable kind cluster is affected. Applications
remain paused fixtures without an Argo CD controller. Capture safe projections,
worker exits, Pod image/UID observations and constant HTTP outcomes; keep raw
logs and credential snapshots private. No retained deployment is authorized.

## Acceptance, rollout and limitations

First/repeat runs must execute unchanged native Deployment/Service observer
children and the installed production HTTP adapter. Stop the real server by
scaling the fixture Deployment to zero and waiting for its Pod to disappear.
A fresh HTTP-only attempt must refuse at the exact owning endpoint boundary.
Explicitly scale up, then require native/HTTP success, the original Deployment
and Service identities and a new server Pod. Preserve schema/ledger/OIDs,
sentinel and credentials. Keep every Workflow until normal owned teardown.

The selected server's `/readyz` reports static service availability, not database
or object-storage recovery. Passing these probes does not establish workload/API
acceptance, foundation chart/storage behavior, Argo CD convergence or complete
readiness-parent admission. No synthetic observations stand in for those gates.
Production templates/verifier budget and supported host commands remain intact.

Live acceptance runs after merge in the existing trusted-main job; offline tests
and render/lint are not live evidence. Rollback removes this CI phase and checkout
without changing retained environments. No promotion, backup/export/recovery or
script-retirement credit is introduced.
