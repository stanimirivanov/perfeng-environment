# ADR-0073: Retain immutable distributions and defer registry admission

- Status: Accepted
- Date: 2026-11-11
- Milestone: M06 — Production Readiness
- Deciders: Repository owner
- Supersedes: The SR-31/SR-32 sequencing for a negative admission decision only
- Superseded by:

## TL;DR

- Option 0 remains the supported contract-distribution architecture.
- Option C is not admitted. The pinned registry failed required SR-29 checks.
- Defer SR-31 recovery qualification without treating it as passed or waived.
- SR-32 records this negative decision without waiting for recovery qualification.
- Future admission requires new reviewed evidence and a separate decision.

## Context

The approved SR plan delivered immutable signed distributions before considering
Option C, a stateful registry for JSON Schema with bundled OpenAPI retained
separately. It originally made SR-32 depend on passing SR-29, SR-30 and SR-31
receipts. That order prevents recording a final no-go when the first admission
gate fails. The owner approved recording the failure and deferring SR-31.

SR-29's [merged qualification source](https://github.com/stanimirivanov/perfeng-contracts/blob/be0cf327637566dc7023480e6d44e491de5d8f75/docs/development/registry-qualification.md)
records `NOT_ADMITTED` for AxonOps `v0.4.0-mcp-phase7`, upstream commit
`1e0f16ddfb3c9ce191ec320b293c4e3874886b46`. The
[candidate lock](https://github.com/stanimirivanov/perfeng-contracts/blob/be0cf327637566dc7023480e6d44e491de5d8f75/tests/qualification/axonops/candidate.lock.json)
pins archive SHA-256 `835528d3b4cf8fa0d8ffbbfc293bac4fd37ad47112ff3b2b817e6b75dd71f5ef`
and executable SHA-256 `1be477a143990a686a33d82a36ba325d024da8e8be890599767d8c29cbf167b5`.

The replay covered 61 JSON Schema instances and seven OpenAPI documents across
current and historical profiles. Current and historical registrations failed,
including regex-dependent schemas. Conditional tightening was incorrectly
approved as compatible. Formatting-distinct equivalent schemas lost independently
retrievable exact bytes. Cross-subject references passed. Native OpenAPI failed,
but that diagnostic is not an Option C admission requirement. The
[gate implementation](https://github.com/stanimirivanov/perfeng-contracts/blob/be0cf327637566dc7023480e6d44e491de5d8f75/scripts/registry_qualification.py)
requires current corpus, historical corpus, references, JSON compatibility and
byte identity to pass. Closing [issue #99](https://github.com/stanimirivanov/perfeng-contracts/issues/99)
does not change the failed result.

SR-30's [merged evidence](https://github.com/stanimirivanov/perfeng-environment/pull/376)
reports passing OpenBao 2.7.2 JWT/KV v2 and Agent checks with synthetic workload
and CI identities, private-file delivery, restart rotation and token revocation.
The [qualification boundary](https://github.com/stanimirivanov/perfeng-environment/blob/22e630bbf4d278f2fcc562d1e76d4aded1fcdd72/docs/openbao-qualification.md)
excludes actual projected tokens/TokenReview, GitHub OIDC discovery and hosted
identity, registry reload, TLS and persistent recovery. That local success does
not repair SR-29 or establish production readiness. This ADR records existing
evidence, without claiming a new qualification run or a result for other versions.

## Decision

Retain Option 0 as the supported architecture. Contracts remain authored in
`perfeng-contracts` and distributed as reviewed immutable signed artifacts.
Consumers retain independently reviewed contract-family and materializer-tool
pins, verify publication identity and integrity, and use prepared offline inputs.
Keep supported historical distributions and rollback selections available.
Candidate publication does not promote a consumer selection.

Record Option C as **NO-GO / DEFERRED**, with SR-29 **NOT_ADMITTED**. Defer SR-31
instead of spending recovery-qualification effort on this failed candidate.
SR-32 records failed admission now. It does not grant admission, deploy a
registry or retire any supported distribution path. The
[plan amendment](../roadmap/contract-distribution-plan.md) defines the negative
decision's dependencies and retains the prerequisites for positive admission.

## Alternatives

Proceeding with SR-31 now adds stateful recovery work without repairing schema
acceptance, compatibility or byte preservation. Waiving failed checks would
change consumer meaning and trust guarantees. Neither alternative is approved.
A different candidate or constrained adapter may be proposed later, but must
qualify against the same required capabilities before recovery work proceeds.

## Consequences and compatibility

No public schema, API, consumer pin, offline resource or artifact format changes.
Option 0 retains its publication and retention obligations. Option C remains
future work, rather than a prerequisite for completing supported distribution.
SR-31 is explicitly deferred, not implemented. M06 remains deferred for a real
non-local deployment requirement and is not declared production-ready.

## Security and operations

This decision changes no deployment, credential, persistent data or release.
Reopening requires owner-approved scope, a pinned candidate, passing revision-bound
SR-29 evidence, explicit coverage of the SR-30 identity/delivery limitations, and
passing SR-31 recovery evidence before a positive SR-32 decision. New deployment
or consumer migration still needs a separately approved implementation plan.
Supersede this ADR to change the decision. A documentation revert cannot admit
Option C or authorize infrastructure changes.

## Validation

Check source references and the amended dependencies. Run `make docs-check`,
`make verify` and `make validate`, reporting environmental limitations honestly.
These checks validate the documentation change. They are not registry admission
receipts and cannot convert `NOT_ADMITTED` into a passing result.
