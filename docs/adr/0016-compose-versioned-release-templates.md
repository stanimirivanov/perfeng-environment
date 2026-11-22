# ADR-0016: Compose versioned release preparation templates

- Status: Proposed
- Date: 2026-10-18
- Milestone: M06 — Production Readiness
- Deciders: Project owner and performance platform maintainers
- Supersedes: None; implements the release boundary in ADR 0014
- Superseded by: None

## TL;DR

- Keep the external-state parent as the stable v1 release entrypoint and move each concern to a versioned child WorkflowTemplate.
- Mount the unchanged bucket and database scripts from an immutable, versioned ConfigMap.
- Apply dependencies before the parent and reject later in-place edits to published child versions.

## Context

The staged external-state WorkflowTemplate mixed sequencing, selection checks and three embedded Job manifests. Two Jobs also embedded shell payloads. A review of the first pilot identified this as a maintenance and review boundary problem. Argo resolves `templateRef` by object name at run time, so splitting one file also creates a versioning and deployment-order obligation.

## Decision

Keep a namespaced parent WorkflowTemplate as the public release entrypoint. It owns the deadline, mutex, sequence and output names. Put each independent external-state Job and the reusable selection assertion in a versioned child WorkflowTemplate referenced by the parent. Keep the three registry image probes in one Job to retain the single `registry-job` output. Keep the existing preflight v1 template unchanged.

Store the bucket and database shell payloads as standalone files in an immutable, explicitly versioned ConfigMap in `perf-platform`. Build that ConfigMap with Kustomize using a fixed name because Kustomize cannot rewrite names inside an Argo resource `manifest` string. Apply the ConfigMap before the child templates and the parent after both children and preflight through Argo CD sync waves. A changed child or script receives a new versioned name and a reviewed parent reference; do not edit a published v1 object in place. Lint all referring and referred WorkflowTemplates in one offline Argo invocation.

## Alternatives considered

- A generic `run-job` template still carries a security-sensitive manifest as an argument string and does not improve reviewability.
- A build-time generator for three Jobs adds another tool and generated artifact contract before there is enough repetition to justify it.
- A registry `withItems` loop produces multiple Jobs and changes the v1 `registry-job` output from one name to an array.
- A DAG would run independent Jobs concurrently but changes fail-stop behavior and recovery evidence.
- Direct Argo container templates would run in `argo-workflows`, outside the existing Secret namespaces.

## Consequences

The parent is small and its outputs and sequential failure behavior remain stable. The shell payloads can be inspected and tested as files. The cost is more GitOps objects and the need to version child names and ConfigMaps together. There is no per-child ServiceAccount change; all referenced templates still execute under the invoking workflow identity. ClusterWorkflowTemplates are deferred until there is an actual cross-namespace reuse need.

## Compatibility and migration

This only restructures the staged pilot before its operational switch. The v1 entrypoint name, four inputs, four outputs, Job names and namespaces remain unchanged. Existing supported host preparation commands stay in place until disposable-kind acceptance proves first run, repeat, failure and recovery behavior. A later script or template update needs a new version and explicit migration of the parent reference.

## Security and operations

The ConfigMap contains no credentials. Jobs continue to consume named Secrets in their own namespaces without mounting a Kubernetes API token. The parent retains its local mutex and deadline. Only trusted operators may submit the pilot. M06 still needs submission admission, per-operator identities, external Secret delivery and durable evidence retention.

## Validation

Run `make workflow-lint`, `make validate` and `kubectl kustomize gitops/release-workflows`. Verify that the extracted scripts match the former embedded payloads, ConfigMap bytes are LF-only, all child names resolve, and the v1 output contract is unchanged. Live acceptance remains a separate disposable-kind gate.

References: [Argo WorkflowTemplates](https://argo-workflows.readthedocs.io/en/latest/workflow-templates/), [Argo offline lint](https://argo-workflows.readthedocs.io/en/latest/cli/argo_lint/) and [Argo CD sync waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/).
