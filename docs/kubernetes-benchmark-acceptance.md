# Accept the local Kubernetes benchmark decision loop

## TL;DR

- This is an explicit **mutating** acceptance exercise for the dedicated local
  integration cluster, not a production benchmark or a CI dependency.
- Finish [runtime activation](kubernetes-runtime-deployment.md) first; do not
  bypass a failed migration, missing policy, mismatched cluster or missing metrics.
- `task kubernetes:acceptance` previews without processes, network or writes.
- Live execution submits bounded benchmark Runs and deliberately restarts one
  verified controller Pod during cancellation cleanup. Reserve an exclusive window.
- A missing baseline needs explicit `--approve-baseline` consent; existing
  incompatible or retired records are never overwritten.
- Only a new `PASSED` receipt proves the whole exercise. A failure retains the
  journal, Runs, cleanup holds, client Pod and artifacts for inspection.

## 1. Complete the deployment prerequisites

Use an ordinary Bash terminal in `perfeng-environment`. Keep Docker Desktop
running. Finish every activation check in the
[runtime deployment guide](kubernetes-runtime-deployment.md#activate-after-merge),
including GitOps health, execution registry health, checkout smoke and all six
qualification checks. Source merge alone does not prove that activation succeeded.
The control-plane Application remains manually synced; this exercise does not
enable automatic sync, migrate a database, install metrics or provision Secrets.

Keep `perfeng-k8s`, `perfeng-control-plane` and `perfeng-contracts` beside this
repository with the selected Git objects available. Run:

```sh
make release-selection-check
task kubernetes:acceptance
```

The first command checks pinned integration offline. The second is a plan only.
Do not run the benchmark recipe directly or disable admission checks to work
around a prerequisite. The promoted target incarnation and warm pause image must
match the selected bundle. A recreated cluster requires reviewed requalification
and promotion, not an edited receipt or substituted UID.

## 2. Identify the actual candidate source

Set `$candidateRevision` to the reviewed **40-character upstream source commit
for the pause candidate image** selected by the bundle. Obtain it from that
image's verified build/source provenance; validate its relationship to the
selected immutable image before using it. The tool validates syntax, not the
truth of this operator-supplied provenance assertion.

Do not substitute this repository's HEAD, the kube-burner wrapper revision, the
control-plane revision, a tag, or an invented SHA. The image is fixed by the
registry; this argument cannot select another candidate image. If its actual
source revision is not yet known, stop at preview and resolve that evidence first.

## 3. Reserve the operator window and execute

Stop unrelated submissions and wait for active Runs and reservations to finish.
Do not run another acceptance, sync, deployment, benchmark or restart concurrently.
Read-only database checks refuse unrelated active Runs/holds, but are **not** an
inter-operator lock and cannot prevent a new submission racing the check.

On the first exercise, explicitly authorize creation, qualification and approval
of the policy-selected integration baseline:

```sh
task kubernetes:acceptance -- --execute --confirm perfeng-local \
  --candidate-git-sha $candidateRevision --approve-baseline
```

After that exact baseline is approved, omit `--approve-baseline` to require
read-only reuse. Omitting it while the baseline is missing/pending refuses before
submitting a reference Run. An existing approved version must match the source
artifact, actual software identity, workload, environment, dataset and observed
qualification. A conflict or retired version requires deliberate baseline review,
not overwriting, auto-retirement or a new unreviewed policy version.

`--approve-baseline` is permission to apply the documented automated eligibility
checks, not a claim that a person has reviewed measurements captured later. The
baseline must have accepted measurement-scoped evidence, full artifact integrity,
PASS report quality, at least the policy's ten actual samples and a finite observed
CV. The tool records that CV; it does **not** invent a variability ceiling or
establish statistical significance. This baseline is integration-only.

The sequence is:

1. Verify the exact deployed release, verified-TLS metrics, fresh all-PASS target
   qualification, and absence of unrelated active work. No repairs are attempted.
2. Create a bounded retained client Pod in `perf-platform`. Its API container
   references the existing operator-token Secret. A separate AWS CLI container
   references the existing restricted uploader Secret and issues only HEAD/GET.
   Tokens and credential values never enter host memory or command arguments.
3. Complete a reference Run (or inspect the exact existing source). Read and
   checksum all nine registered artifacts, verify stored S3 metadata, pinned
   product schemas, raw/normalized provenance, measurement eligibility and report
   quality. Approve the exact baseline only with the additional explicit consent.
4. Submit a cancellation Run. Observe its real, database-bound execution Job UID
   before cancelling. Wait for its durable cleanup fence; do not manufacture a
   cancellation window by suspending workloads or changing their recipe.
5. Verify the controller's selected Deployment and Pod/ReplicaSet ownership chain,
   then delete **only that Pod** with UID/resourceVersion preconditions and normal
   graceful termination. Wait for actual deletion and a ready replacement UID.
   No Deployment template, GitOps state, PVC or credential is changed.
6. Observe the same reservation, namespace UID and plan across restart. Require
   ABORTED, the retained zero-Pod quota, actual executor/workload/authority removal,
   and a database cleanup attestation released after its two-minute drain boundary.
7. Re-read the unchanged approved baseline. Submit a new candidate Run to prove
   target reuse, verify its artifacts and cleanup, and require an evaluated
   regression decision referencing that exact baseline artifact. Observe-mode
   PASS **or FAIL** is a valid evaluated decision; INCONCLUSIVE is not success.

Live commands share a 3,400-second budget, with smaller per-phase/request budgets
and progress messages. Prior offline source verification has its own tool limits.
The retained client has a one-hour Kubernetes deadline; it is
not an unattended agent. The storage credential is the existing restricted
uploader identity (which can write Run objects), **not** a newly provisioned
read-only identity; the acceptance implementation itself performs only reads.
Database inspection uses existing in-Pod PostgreSQL credentials and server-enforced
read-only transactions with statement/lock timeouts. No new privileges are granted.

## 4. Inspect the outcome or recover an interrupted exercise

Progress prints the current phase and accepted Run IDs. Ignored `results/` holds
a create-only `m04-acceptance-<session>-journal.jsonl` containing safe identities,
request/idempotency intent and checkpoint evidence. Success additionally creates
`m04-acceptance-<session>.json` with `schema=m04-kubernetes-acceptance/v1`,
`status=PASSED`, integration purpose, exact selections, Run IDs, baseline, restart
UIDs, artifact references and regression outcome. It is not an admission token.
The success receipt also binds the journal's exact size and SHA-256 so its
checkpoint trail can be checked independently of the summary.
Review receipts before sharing; operational identifiers are not credentials but
may still be project-sensitive.

No final receipt is written on failure. Do not treat a completed reference, ready
Pod, successful API cancel request, source test, or generic `ABORTED` state as
proof of the whole exercise. M04 remains in progress until live acceptance and
the deployment gates pass and their evidence is reviewed.

If baseline creation/transition was interrupted, inspect the journal and the
existing source Run/baseline. Once active work and cleanup holds are resolved,
an explicit retry may reuse that source:

```sh
task kubernetes:acceptance -- --execute --confirm perfeng-local \
  --candidate-git-sha $candidateRevision --reference-run $referenceRunId \
  --approve-baseline
```

Set `$referenceRunId` to the actual recorded completed reference, not an example.
Only byte-identical, compatible pending transitions can resume; a changed
qualification, revision conflict or different source is refused. Uncertain Run
submission is never blindly retried: the journal records the idempotency key
before submission; inspect it through the retained API client/operator workflow
before starting another session. This tool does not automatically resume a whole
partially completed exercise or infer an unknown Run ID.

If cancellation completed before the execution/fence window was observed, that
session fails rather than claiming active restart recovery. Inspect its actual
state and wait for all holds before a deliberate retry. On timeout, Ctrl+C,
controller failure or stale UID/RV, retained state is the recovery starting point.
Never force-release a reservation, remove namespace/quota tombstones, force-delete
a Pod, rewrite a baseline or rotate credentials to obtain a green receipt.
The test creates no automatic cleanup mechanism; later retention/removal needs
separate ownership-aware review. See [troubleshooting](troubleshooting.md#m04-benchmark-acceptance).
