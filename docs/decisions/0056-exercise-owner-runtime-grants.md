# ADR-0056: Exercise complete owner-only runtime grants on the paused CI fixture

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-29
- Milestone: M06 — Production Readiness
- Extends: [ADR 0055](0055-stage-native-runtime-grants.md)

## TL;DR

- Replace only the initial scenario's first/repeat administrator grant calls with the unchanged owner-only parent.
- Reuse pinned first-install inputs and the already installed schema while bootstrap Applications/Deployment stay paused.
- Require real ordered parent execution, two distinct retained native Jobs, runtime TCP probes and unchanged surrounding state.
- Keep all other scenarios and excess-DELETE injection/administrator correction unchanged.
- Heavy verification stays on PR CI; complete-parent live proof first runs after merge under the existing main-only guard.

## Decision and boundaries

The owner approved this disposable SQL acceptance scope. Install the staged
runtime-grants templates, immutable SQL ConfigMap and approved authority only
in the existing trusted-main initial job. Use the existing pinned request and
migration-url; submit the unchanged parent twice. Require actual preflight,
exact-lock selection, shared forward-only/0018 intent, fresh quiescence and the
fixed native grant Job in order. No production manifest or credential changes.

Each run must preserve the paused bootstrap Applications/Deployment, ready
foundation Pods, services, StatefulSets, ConfigMaps, Secrets, PVCs/PVs and cluster
identity. Require the complete selected schema before execution and unchanged
schema/relations OIDs, ledger checksums/timestamps, table data, sentinel and SQL
roles/passwords afterward. Raw resource and credential evidence stays private.
The two Jobs must match the published owner-only manifest, SQL version and
request provenance, with no administrator Secret, API token, retries or deletion.

Keep all five existing runtime TCP probe cases. First/repeat successful probes
must establish the exact privilege matrix and identical ACLs; missing grants
and deliberately excessive DELETE must still refuse. Keep the existing fixture
administrator's deliberate DELETE grant and correction. The other five scenarios
retain all their existing administrator calls; no new job, scenario, request/ref,
credential, right or CI budget is introduced.

## Failure, retention and verification

Failure stops before the next grant/probe or server setup; never fall back to an
administrator grant in the initial first/repeat path. Retain failed evidence and
leave workloads paused. No automatic repair, Job replacement, retry or recovery.
Publish bounded outcomes only after both parents and the existing phase retention
gates pass. Recheck unchanged source identities, both successful Jobs/Workflows
and repeat ACLs at phase completion and at the end of the full initial scenario.

Deterministic tests reject wrong authority/order, changed native declarations,
SQL/configuration, data/credential/storage drift and missing/replaced retained
Jobs. Execute extracted shell coordinators to prove fail-stop sequencing and all
six scenarios' routing. Focused offline checks run locally; heavy full/cross-platform
checks run on PR CI. Live parent execution remains post-merge main-only.

This proves a paused bootstrap fixture, not a real adopted application's grant
transition, admission, ambiguous-commit recovery, non-local TLS or multi-user
security. No host entrypoint is retired and the production verifier budget remains
two modules/708 lines. Operational cutover remains a separate decision.
