# ADR-0028: Publish adoption request boundary fixtures

- Status: Proposed
- Date: 2026-10-23
- Milestone: M06 — Production Readiness
- Amends: ADR 0020's one generated request file per temporary commit

## TL;DR

- The owner approved two additional deterministic CI requests in the same owned temporary commit/ref.
- Prove changed bytes are not an exact retry and upgrade intent cannot use adoption as promotion.
- Keep existing scenarios, credential rights, production entrypoints and ledger records unchanged.

## Context and decision

ADR 0015 binds retry identity to exact bytes and separates adoption from
promotion. The owner approved extending ADR 0020's publication from one to three
request files in the same child commit. Keep ADR 0026's ref names, ownership and
expected-SHA cleanup. These generated fixtures are not reviewed releases.

Preserve the original request's serialization/path. Append one newline for
changed-request: parsed JSON is identical but its digest differs. Upgrade-request
changes only expectedCurrent to the original digest, serialized deterministically.
Validate all three against the unchanged v1 schema before external publication.
Bind each exact byte digest/path to the common request commit and selection.
Publish exactly three intended tree entries; journal intent before creating
the single run/scenario-owned ref. Never move an existing ref.

After successful baseline/recovery/concurrent retries, submit both new inputs
to fresh unchanged complete adoption parents, before retention and destructive
receipt tests. Require schema-valid nested Git preflight, correct request
identity/path/locks, actual readiness, healthy selected manual root and native
ledger reads. Changed bytes must hit the existing-pointer conflict; upgrade
intent must hit the non-initial-adoption refusal. Only plan may fail, with main
exit 2 and the exact owning diagnostic. No result or downstream worker may exist.

Original receipt/pointer bytes, UIDs and provenance must equal their accepted
baseline. Each candidate receipt must remain absent in native observations and
a direct API read. Keep workload/data retention and six child skips. Quota
exhaustion or unrelated readiness errors cannot count as either refusal.
Continue the existing retention and destructive identity-refusal cases.

## Authority, validation and rollback

No new job, scenario, ref, credential rights, controller, production template,
verifier, schema or operating command is introduced. The temporary commit now
contains two additional CI request blobs. Tokens remain repository-scoped, not
path/ref-scoped. Raw ledger/logs remain private; upload existing Workflow
evidence, request inputs/parameters and safe refusal reports. Those reports
never attest successful adoption or promotion.

Run focused publisher/schema/refusal/CLI tests, shell syntax, make validate and
Workflow lint. Live proof runs after merge in both existing trusted-main
scenarios. Rollback removes additional fixtures/cases; ref cleanup and cluster
teardown remain unchanged. Initial-create races, ambiguous responses, actual
promotion/advancement, retained recovery, full-tree health and measured script
retirement remain separate gates.
