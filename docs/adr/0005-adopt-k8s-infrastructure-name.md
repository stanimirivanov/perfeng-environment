# ADR-0005: Adopt the k8s-infrastructure repository name

- Status: Accepted
- Date: 2026-09-16
- Milestone: M06 — Production Readiness
- Deciders: Project owner
- Supersedes: Repository-name portion of ADR 0004
- Superseded by:

## TL;DR

- Use the created repository name `k8s-infrastructure` instead of the longer
  provisional name in ADR 0004.
- Add it to the Perfeng source workspace and editor workspace.
- Treat workspace membership as developer checkout composition, not Argo CD
  deployment authorization.
- Restrict the local AppProject to repositories referenced by its child
  Applications; `k8s-infrastructure` is not a Perfeng workload source.
- Transfer no cluster, Argo CD or state ownership in this decision.

## Context

ADR 0004 selected a project-neutral repository for Kubernetes substrates and
proposed the name `kubernetes-platform-infrastructure`. The repository was
created as `k8s-infrastructure` and its initial boundary records that name.
Perfeng workspace composition must now discover the checkout consistently.

The existing GitOps validator derived the local AppProject `sourceRepos` from
every entry in `workspace.json`. That made source-development convenience an
authorization input. Adding a generic infrastructure checkout would therefore
grant the Perfeng AppProject permission to use it as workload desired state,
even though the repository owns the substrate beneath Argo CD and must not own
Perfeng Applications.

## Decision

Adopt `k8s-infrastructure` as the repository name. Add its credential-free
GitHub URL, sibling path and initial `main` branch to `workspace.json`, and add
the same folder to `perfeng.code-workspace`.

Workspace membership describes source repositories used for development. It
does not authorize Argo CD sources. The local AppProject source allowlist is
instead the exact set of repository URLs referenced by its reviewed child
Applications. Validation rejects missing, duplicate or additional source
repositories.

The current child Applications require only `perfeng-environment` and
`perfeng-k8s`. The previously allowed component repositories are removed from
the AppProject because no child Application reads desired state from them.
`k8s-infrastructure` is deliberately not added.

This decision registers and names the repository only. It does not import kind
or Argo CD code, select an infrastructure release, change the kubeconfig or
transfer ownership of any live resource.

## Alternatives considered

### Rename the repository to the provisional ADR 0004 name

- Benefits: exact textual agreement with the earlier decision.
- Costs and risks: repository recreation or rename churn without a meaningful
  responsibility improvement.
- Reason not selected: `k8s-infrastructure` is concise and retains the intended
  project-neutral scope.

### Add every workspace repository to the AppProject

- Benefits: preserves the previous derivation and makes future child sources
  require no permission update.
- Costs and risks: grants unused desired-state authority and incorrectly
  authorizes the substrate repository as a workload source.
- Reason not selected: developer workspace membership is not a security
  boundary and least privilege requires an explicit deployment allowlist.

### Leave the infrastructure repository outside the workspace

- Benefits: no workspace or GitOps validator change.
- Costs and risks: bootstrap, status and the editor workspace omit a repository
  required by the accepted extraction sequence.
- Reason not selected: source-workspace composition belongs here under ADR 0003.

## Consequences

### Positive

- Bootstrap and editor composition use the actual repository name.
- Infrastructure extraction remains visible in the central roadmap.
- Adding a source checkout cannot silently broaden Argo CD authorization.
- The AppProject expresses the minimum repositories needed by current child
  Applications.

### Negative

- A future child Application sourced from another repository must update the
  AppProject allowlist and validation in the same reviewed change.
- The created repository must have its foundation merged to `main` before a
  fresh workspace bootstrap can clone a usable default branch.

### Neutral or follow-up

- ADR 0004 remains the historical source of the extraction decision; only its
  provisional repository name is superseded.
- The next extraction change imports local kind lifecycle code with history.

## Compatibility and migration

Existing checkouts are never fetched, switched or reset. Workspace bootstrap
will report a checkout on a feature branch or with local changes without
modifying it. Missing workspaces clone the new repository from `main`.

When this revision is reconciled, Argo CD removes unused AppProject source
permissions. Current Applications remain valid because all their sources are
retained. Rollback restores the prior project manifest; it does not affect
application data or cluster identity.

## Security and operations

- No credential is added to workspace configuration.
- The existing owner-scoped read credential remains unchanged.
- The AppProject authorization surface is narrowed from all workspace
  repositories to the two current desired-state sources.
- No cluster command or external write is performed by this change itself.
- After merge, GitOps health must confirm the root and child Applications remain
  synchronized and healthy.

## Validation

- Workspace validation reports eight repositories and matching VS Code folders.
- GitOps validation accepts exactly the repositories used by child
  Applications and rejects `k8s-infrastructure` as an unreferenced extra.
- Existing local manifests render successfully offline.
- Unit tests cover the project-neutral repository name and the authorization
  boundary.
- Post-merge GitOps health confirms that permission tightening does not disrupt
  reconciliation.

## References

- [ADR 0003: Centralize the project roadmap](0003-centralize-project-roadmap.md)
- [ADR 0004: Separate Kubernetes infrastructure](0004-separate-kubernetes-infrastructure.md)
- [`k8s-infrastructure` repository](https://github.com/stanimirivanov/k8s-infrastructure)
