# ADR-0034: Bound disposable acceptance fixture lifetimes

- Status: Proposed
- Date: 2026-10-25
- Milestone: M06 — Production Readiness

## TL;DR

- The owner approved a 60-minute budget for the existing five trusted-main jobs.
- Keep the same S3 observer alive for that bounded interval, subject to owned cleanup.
- Preserve workload/controller deadlines and retain only sanitized startup diagnostics.

## Context and decision

Run 40191776524 passed image, migration, SQL grants and server parity. Two
scenarios later tried to execute the S3 observer after its 900-second lifetime;
three others stopped at Redis rollout before its cause could be observed.
The composed acceptance had outgrown its shared fixture lifetime and approached
the outer 30-minute job limit before full adoption acceptance.

The owner explicitly approved extending the existing jobs to 60 minutes and the
same observer's active deadline and sleep to 3600 seconds. No new job, scenario,
temporary Git ref or credential right is introduced. Existing run/attempt/scenario
ownership, main-only guard and cluster/ref teardown remain mandatory. The same
observer identity and mounted credential are retained; no implicit replacement
or credential rotation is introduced.

Keep Redis, other controller rollouts and production operations at their current
deadlines. A rollout failure must retain its original exit code even when
diagnostic collection fails. Inspect private Pod status and bounded relevant
container logs, publish only allowlisted status fields and fixed categories,
then remove private diagnostic files. Raw logs, arbitrary messages, environment,
arguments and credentials must not enter artifacts. Failed diagnostics produce
only a constant unavailable result.

## Security, cost and rollback

This extends the maximum runtime and fixture credential exposure of the existing
disposable jobs as approved by the owner. It does not change token rights or
recipients, installed workload security settings or retained environments.
Read-only diagnostics use the existing administrative fixture context. Private
files stay outside uploaded results; successful teardown removes the cluster.
Rollback restores the earlier limits and removes diagnostics, reintroducing the
known observer lifetime mismatch until acceptance is restructured.

## Verification and limits

Check the observer deadline and sleep against the owning workflow's actual budget.
Exercise the report CLI with secret-like text and unknown status values; require
safe output, cleanup and original failure preservation. Run the full repository
gate and shell syntax check. Live proof remains post-merge under the main-only
guard. Redis's startup cause remains unknown; this decision does not authorize
speculative image, credential, readiness-deadline or production changes, nor
claim adoption, promotion or script retirement.
