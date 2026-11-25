# ADR 0077: Separate harness execution from command-route review

- Status: Accepted
- Date: 2026-11-25
- Milestone: M05 — Operator Visibility and Diagnostics
- Deciders: Workspace owner
- Supersedes: None
- Superseded by: None

## TL;DR

- The check manifest grants the harness authority to run selected engineering checks.
- A separate baseline records the ownership and callable definitions of reviewed command routes.
- Maintainers approve changes to the manifest, baseline, guard, fixtures, and gate wiring.

## Context

Perfeng Environment has contributor checks, application scripts, disposable
fixture entrypoints, and live operations. Contributors need an offline way to
spot new routes and definition changes while keeping each route's established
owner and invocation.

## Decision

Keep [execution registration](../../harness/checks.toml) and [route review
records](../../harness/command-routes.toml) in separate files. Register the
command-route guard as an offline engineering check. Use exact identities,
owners, purposes, and callable definitions in the review records.

The guard inspects tracked and non-ignored untracked files in the working tree.
A clean checkout gives CI the same scan boundary. Explicit interpreter
invocation can establish a route even when the file mode is non-executable.
A program sent to a Pod for separate execution can be a route of its own.
Ordinary imported helpers remain with the caller. For syntax outside the
supported forms, use a recognizable declaration or a narrowly tested rule
bound to the exact declaration.

The repository maintainer explicitly signs off on baseline, manifest, guard,
fixture expectation, and gate changes in the PR. The
[engineering guide](../development/harness.md#reviewing-command-routes) details
the supported detection forms and review process.

## Alternative and trade-off

A shared inventory would put executable checks and application or operational
review records in one file. Separate files make changes to dispatch authority
visible and leave operational routes under their native owners. A callable
check can appear in both files because its execution registration and its
review record serve different purposes. Reviewers need to check both when a
change affects both.

## Rollout

Start with offline script and module review. The Ubuntu validation status can
become required after the baseline and fixtures pass through the hosted job
and the maintainer separately authorizes the ruleset write. The exact-base,
read-only PR metadata check remains independent.
