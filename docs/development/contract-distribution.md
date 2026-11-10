# Update the benchmark evidence schema

## TL;DR

- Prepare the exact signed history and tool releases named by the [distribution lock](../../contracts/distribution-restore.lock.json).
- Preview the offline update before applying it. The update path owns the checked-in benchmark schema.
- Run immutable Git source admission separately to confirm the selected upstream bytes.

Download the retained assets into a private directory with `tool/` and
`history/` subdirectories. Obtain the independently pinned verifier as described
in [the verifier guide](../contract-distribution-tools.md). Use the tool named
by `updateToolRelease` for this workflow. The historical tracer and rollback
checks continue to use their own retained tool selection.

```sh
lock=contracts/distribution-restore.lock.json
gh release download "$(jq -er .updateToolRelease "$lock")" \
  --repo stanimirivanov/perfeng-environment --dir "$assets/tool"
gh release download "$(jq -er .historyRelease "$lock")" \
  --repo stanimirivanov/perfeng-contracts --dir "$assets/history"

make contracts-update-check CONTRACTS_UPDATE_ASSETS="$assets" CONTRACTS_VERIFIER="$verifier"
uv run --locked --offline python -m scripts.contracts_update \
  --assets "$assets" --verifier "$verifier" --execute
make release-selection-check CONTRACTS_ROOT=../perfeng-contracts
```

The preview and execution both verify the publisher, archive and selection.
Execution replaces only the benchmark schema with its exact approved bytes.
Running it against a matching checkout reports `unchanged`. A future schema
revision needs a reviewed change to the runtime and distribution pins before
running the updater. Restore matching pins and bytes together when reverting a
schema selection. Retain the exact signed distributions needed by
[the cold restore gate](../contract-distribution-tools.md#rehearse-cold-recovery).
