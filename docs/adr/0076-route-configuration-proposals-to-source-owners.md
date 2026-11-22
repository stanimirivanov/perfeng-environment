# ADR 0076: Route configuration proposals to source owners

- Status: Accepted
- Date: 2026-11-22
- Milestone: M05 — Operator Visibility and Diagnostics
- Deciders: Workspace owner
- Supersedes: None
- Superseded by: None

## TL;DR

- Service and component owners maintain authored configuration in their reviewed Git locations.
- Environment owns the proposal routing map. Kubernetes packaging owns assembled Registry Bundles.
- Publication and runtime admission remain separate authority checks.

## Context

The Console’s planned configuration proposal flow needs to review service registrations, test suites and policies through Git. A single Console action can refer to configuration whose source owner, publisher and runtime authority differ.

## Decision

Each service or component owns the authored configuration for its domain. Environment records the reviewed repository and path for each supported proposal type and its publication route. Console proposes changes only through that reviewed routing. The designated publisher records immutable publication evidence, and `perfeng-k8s` assembles reviewed inputs into versioned Runtime Registry Bundles. Environment selects bundle artifacts for a deployment, and Control Plane checks live applicability and access.

## Alternative and trade-off

Centralizing authored configuration in `perfeng-k8s` would simplify target routing and couple service review to the packaging repository. Owner-local source keeps approval close to the affected service and makes ownership explicit. It requires maintained route mappings and published provenance between authored and assembled inputs.

## Compatibility and migration

Existing bundle and deployment selection remain in place. Each source route requires explicit review before enablement. Route authorization grants permission to propose a Git change, while publication, environment selection and Run admission retain their own checks.

## Validation

Proposal routing must refuse unknown or unmapped inputs and unexpected repository or path changes. A published selection must be traceable to exact reviewed source, publication and bundle identities. Existing historical Runs must retain the inputs they used.
