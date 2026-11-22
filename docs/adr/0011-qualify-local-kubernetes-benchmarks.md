# ADR-0011: Qualify local Kubernetes benchmarks without changing infrastructure

- Status: Proposed
- Date: 2026-10-13
- Milestone: M04 — Kubernetes Benchmark Decision Loop
- Deciders: Repository owner
- Supersedes: None

## TL;DR

- Define an explicitly integration-only local kind profile and read-only preflight.
- Pin the generated contracts schema locally for offline validation.
- Preserve UNKNOWN/REJECTED captures rather than replacing absent usage with zero.
- Separate stable identity, transient observations and execution authority.

## Context and decision

M04 needs actual qualification before benchmark dispatch. Infrastructure health
does not establish interference or measurement eligibility. M03 is deferred
pending a suitable Windows host and need not block Kubernetes integration.

Use an environment-owned versioned profile and contracts 0.22.0 preflight
evidence. Verify local Docker, named kind credentials and API before resource
reads. Collect only non-secret node facts, active namespace columns and fresh
Metrics API windows with bounded deadlines. Preserve every required check and
unknown observations. Persist a descriptor plus immutable completion receipt
under ignored results. Identity binds profile/image bytes and cluster incarnation,
not changing usage. Preflight does not reserve targets or approve baselines.

## Alternatives and consequences

Readiness alone cannot prove interference. Zero fallback hides missing evidence.
Implicit metrics-service installation expands ownership/security authority.
A mandatory production laboratory would unnecessarily block functional integration.
These alternatives are excluded. Missing metrics can legitimately leave the
cluster UNKNOWN pending an explicitly reviewed observation source.

Use maintained MIT-licensed jsonschema instead of implementing Draft 2020-12.
Its replacement boundary is one local adapter. Integration budgets remain safety
checks, not scientific guarantees or proof of host isolation.

## Compatibility, security and rollback

Deployment locks, runtime bundles, existing operator aggregates, credentials and
workloads remain unchanged. The task is opt-in; schema adoption is not runtime
promotion. No Secret reads, mutation, downloads, rotation, TLS bypass or telemetry
installation. Profile changes need reviewed versions, not receipt rewrites.
Rollback stops future use and retains state. M03 acceptance remains deferred.

## Verification

`make verify`, `make validate`, pinned-source checks and explicit read-only live
acceptance cover false/unknown outcomes, source integrity, safety and retention.
