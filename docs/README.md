# Perfeng environment documentation map

## TL;DR

- Start with [CONTRIBUTING.md](../CONTRIBUTING.md), then use this map to read only the guidance relevant to the task.
- This repository owns workspace and environment composition; component behavior and shared contracts stay in their owning repositories.
- Offline repository validation, Helm rendering, pinned cross-repository selection, and live cluster operations are separate verification tiers.
- Update this map whenever a canonical guide moves or a recurring task lacks a clear route.

## Route by task

| Task or changed area | Read first | Verify or consult as needed |
|:--|:--|:--|
| Contributor workflow, ambiguity, or issue preparation | [Contributor guide](../CONTRIBUTING.md) | [Working agreement](../AGENTS.md) |
| Build, CI, repository policy, dependency, or local setup | [Harness guide](development/harness.md), [dependency policy](development/dependency-policy.md) | [Engineering standards](development/engineering-standards.md), `make verify` |
| Workspace composition or sibling checkouts | [Repository README](../README.md) | `scripts/workspace.py`, `workspace.json` |
| Local Kubernetes substrate or Argo CD bootstrap | [Local infrastructure release](local-infrastructure-release.md), [local Kubernetes](local-kubernetes.md) | [ADR index](decisions/README.md), specialized release-selection checks |
| GitOps composition, ownership, or recovery | [Local GitOps](local-gitops.md), [migration completion](gitops-migration-completion.md) | [Troubleshooting](troubleshooting.md), `scripts/gitops.py` |
| PostgreSQL or object storage | [Local PostgreSQL](local-postgres.md), [local object store](local-object-store.md) | [Storage access](storage-access.md), [troubleshooting](troubleshooting.md) |
| Control-plane deployment or runtime inputs | [Local control plane](local-control-plane.md) | Deployment lock and pinned sibling validation |
| k6, browser, or Kubernetes evidence workflows | [Local k6 run](local-k6-run.md), [Windows browser host](windows-browser-host.md), [local kube-burner](local-kube-burner.md) | Relevant tests and focused acceptance guide |
| Milestones or cross-repository sequencing | [Milestones](roadmap/milestones.md) | Relevant ADRs and component repositories |
| Operational failure or live recovery | [Troubleshooting](troubleshooting.md) | The focused operating guide; live commands require explicit authorization |

## Canonical collections

- [Development](development/) contains engineering and harness guidance.
- [Decisions](decisions/README.md) records durable architecture and ownership choices.
- [Roadmap](roadmap/milestones.md) owns checked-in cross-repository sequencing.
- The focused local guides document prerequisites, healthy signals, recovery, and explicit execution boundaries.

## Keeping this map useful

This map routes readers; it does not restate policy. A change MUST update it when a canonical source is added, renamed, moved, or removed. `make docs-check` verifies repository-local links and policy structure, while semantic ownership still requires review.
