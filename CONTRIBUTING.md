# Contributing

Keep changes narrowly scoped, preserve existing local data by default, and use
the repository's preview-first commands before executing an operation.

## Development checks

Run the same checks used by CI before opening a pull request:

```sh
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked ty check
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/workspace.py validate
uv run --locked python scripts/gitops.py validate
```

Tests must use temporary paths and mocked external operations unless the pull
request explicitly introduces an integration test. Do not put credentials,
machine-specific paths or generated local receipts in the repository.

## Documentation

Every long document must contain a `## TL;DR` section near the beginning,
immediately after its title and any status metadata. A document is considered
long when it meets any of these conditions:

- it contains 800 or more words;
- it contains more than five second-level sections;
- it is an architecture, end-to-end workflow or operational guide that readers
  are likely to consult selectively.

The TL;DR must state the document's purpose, intended reader, important decision
or outcome, and the next action where one exists. Prefer three to seven short
bullets. It is a summary and navigation aid, not a second introduction or a
substitute for safety constraints in the detailed procedure.

New and materially changed long documents must comply immediately. Existing
long documents are brought into compliance when next changed or through a
dedicated documentation pull request.

Operational changes must update `docs/troubleshooting.md` when they add or
change a supported command, prerequisite, healthy signal, failure
classification or recovery path. Keep that runbook as a dependency-ordered
navigation layer and leave detailed deployment, credential, persistence and
rollback procedures in their focused guides. Diagnostic commands must default
to read-only operation, use the dedicated kubeconfig and label any mutation or
destructive action explicitly.

Architecture decisions belong in `docs/decisions`. Use a numbered filename,
record the status and date, include alternatives and consequences, and link the
decision from `docs/decisions/README.md`.
