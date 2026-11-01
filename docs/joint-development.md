# Joint Perfeng, Argus and Argus SRE development

## TL;DR

- `workspace.json` inventories ten source repositories; `perfeng.code-workspace`
  mirrors the same named folders and relative paths.
- Perfeng owns performance execution and analysis; Argus owns test decisions;
  Argus SRE owns incident investigation and supervised remediation.
- Run each repository's pinned toolchain and checks from that repository.
- Workspace composition does not select releases or grant runtime authority.
- Always qualify milestone references with the product name.

## Workspace and bootstrap

Keep `perfeng-environment`, the six other `perfeng-*` component repositories,
`k8s-infrastructure`, `argus` and `argus-sre` as named siblings. The actual SRE
repository is `argus-sre`, not `argis-sre`. Paths resolve relative to the manifest,
so the parent directory may have any name and may move between machines.
No submodules, absolute machine paths or copied product sources are required.

From `perfeng-environment`, these standard-library commands need no dependency
installation and perform no cluster or credential operations:

```sh
python3 scripts/workspace.py validate
python3 scripts/workspace.py status
python3 scripts/workspace.py bootstrap --dry-run
```

Validation compares the manifest and editor folders. Status checks local Git
origins and reports branches, commits and dirty state without fetching. Dry-run
preflights checkout conflicts and reports missing repositories without cloning.

After configuring GitHub access outside Git, explicitly clone only missing
repositories and open the workspace:

```sh
python3 scripts/workspace.py bootstrap
code perfeng.code-workspace
```

Bootstrap leaves existing checkouts, branches, commits and local edits untouched;
it does not pull or switch them. A wrong origin or occupied destination blocks
bootstrap before cloning starts. A failed clone retains its partial directory
for inspection rather than deleting potentially useful state.

The `source.branch: main` entries are initial clone defaults. They are not
immutable release selections, deployment approval or instructions to reset an
existing issue branch. Deployment locks and evidence pins retain their separate
validation and review boundaries.

## Product and contract ownership

| Owner | Responsibility | Integration boundary |
|:--|:--|:--|
| Perfeng components | Workloads, performance Runs, measurement quality, SLO/regression analysis and approved baselines | Public Perfeng contracts owned in `perfeng-contracts` |
| Argus | Change understanding, test catalog/impact/selection, constrained test maintenance and consumption of performance evidence | Argus-owned versioned contracts and pinned Perfeng public contracts |
| Argus SRE | Incident investigation and supervised remediation | Independently owned public evidence contracts; no runtime composition is introduced here |
| `k8s-infrastructure` | Project-neutral infrastructure lifecycle | Reviewed infrastructure releases, independently selected by product environments |
| `perfeng-environment` | Source workspace and Perfeng environment composition | No authority over another product's private tables, credentials or release lifecycle |

Source proximity does not authorize private package imports or cross-product
SQL access. Products own their schemas, state, credentials, deployment selections,
rollback and releases. Runtime integrations use public versioned interfaces and
immutable evidence, not sibling filesystem paths. Selection, execution completion,
performance verdicts and remediation approval retain distinct meanings.

Read each repository's `AGENTS.md`, `CONTRIBUTING.md` and documentation map before
working there. Apply its issue-first workflow and preserve unrelated changes.
This workspace guide does not override product policies.

## Per-repository toolchains and checks

Use a separate terminal or task-local tool environment for each repository.
Inspect its pins before running setup; do not globally upgrade another product's
Go, Node, pnpm or Python installation to make versions match.

| Repository | Version authority | Existing checks from that repository root |
|:--|:--|:--|
| `perfeng-environment` | `.python-version`, `pyproject.toml`, `uv.lock` | `make check`, `make verify` |
| `argus` | `go.mod`, `.node-version`, `package.json`, `pnpm-lock.yaml`, pinned tool modules | `make doctor`, `make verify`, `make validate`; `make db-validate` for database integration |
| `argus-sre` | `.python-version`, `Makefile`; currently a dependency-free Python scaffold | `make doctor`, `make validate` |
| Other Perfeng components and infrastructure | Their own manifests, lockfiles and contributor guides | Their own documented Make targets and acceptance tiers |

Argus DB/browser/DBOS integration and Perfeng image/kind checks have separate
resource and isolation requirements. Workspace bootstrap installs no toolchains,
starts no services and runs no product tests. A successful workspace validation
proves inventory consistency, not product health or end-to-end integration.

## Product-qualified milestones

Use the published product name and exact milestone title in issues and reports.
Numbers are local to each product: **Perfeng M06 - Production Readiness** differs
from **Argus M06 - Validated adaptation** and **Argus SRE M06 - Performance
evidence integration**.

The joint integration track coordinates **Argus M09 - Perfeng integration** with
**Perfeng M07 - Argus integration**. The approved plan's `M09-PR1` is a coordination
slice ID; its Perfeng environment issue belongs to Perfeng M07. This does not
renumber Argus milestones or imply Argus SRE milestone acceptance. GitHub owns
live milestone/issue assignments; each product's roadmap owns its intended gates.

## Workspace troubleshooting

If validation reports folder drift, compare `workspace.json` with
`perfeng.code-workspace`: the names, relative paths and order must agree. Update
both together through review; keep personal editor state outside these shared
files. An extra, missing or renamed folder fails validation.

For `wrong-origin` or `conflict`, inspect the destination and preserve its work.
Do not delete it or rewrite its remote as an automatic repair. Coordinate any
move outside bootstrap. Existing dirty issue branches are expected and remain
untouched. Status is read-only; it does not reconcile remote updates.

If tools disagree with a product's pins, use `make doctor` where available and
that product's setup guide. Do not change dependency locks just to validate the
workspace. Reverting workspace entries removes editor/bootstrap membership;
it neither deletes existing checkouts nor reverses any product deployment.
