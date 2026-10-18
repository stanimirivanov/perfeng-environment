# Staged release readiness and operational health

## TL;DR

- `perfeng-release-readiness-v1` observes the selected control-plane release and the PostgreSQL/SeaweedFS foundation using native Argo resource conditions.
- It reuses reviewed-input preflight, verifies the selected lock again, checks release identity/fresh rollout observations and probes the three existing operational endpoints.
- A successful `result` is versioned readiness evidence, not promotion approval, complete platform health or benchmark qualification.
- The workflow is staged in the separately synced release-workflows Application. Existing `task status`, `task smoke` and the manual M04 cutover remain supported.

## Execution and authority

```text
reviewed request + pinned selection -> cluster/input preflight
  -> named StatefulSet/Application/Deployment readiness conditions
  -> Service observation -> selected-release verification + bounded endpoint probes
  -> PerfengReleaseReadiness v1 result
```

The parent accepts the same four parameters as [preflight](release-preflight-workflow.md): `request-revision`, `request-path`, `request-sha256` and `selection-revision`. The digest parameter uses the `sha256:<64 lowercase hexadecimal characters>` form. Git inputs remain exact reviewed commits; credentials come from `release-git-read`. The verifier reads the selected `deployments/local-control-plane.json`, checks its bytes against the preflight digest and derives image, chart/package revisions and runtime ConfigMap identities from that lock. It does not carry a second hardcoded release digest list.

Argo resource templates perform named `get` operations with success conditions and a 300-second Pod deadline per observation. Missing resources fail immediately; a reported degraded/missing Application fails explicitly. The parent bounds the whole operation at 1,200 seconds, including referenced preflight and artifact retrieval. It shares the existing `perfeng-local-external-state` mutex with preparation. That mutex does not serialize host commands, Argo CD reconciliation or other controller activity.

The dedicated `release-readiness` identity has only named-resource reads: the control-plane Application in `argocd`, the two StatefulSets, control-plane Deployment and Service in `perf-platform`, the `kube-system` namespace UID, and the existing Git-read Secret. It may create/patch its Argo task results. The pinned Argo v4.1.5 executor's resource condition implementation uses named GET polling, so no list/watch or workload-write grant is needed. Operational HTTP uses the existing internal Service and requires no Kubernetes proxy permission. It does not use Argo's HTTP agent or add a controller.

The Application-read Role/Binding is in `release-workflows-bootstrap/readiness-application-access.yaml` and is applied through the explicit bootstrap channel. The staged Application cannot create RBAC in `argocd`; its Project destinations remain unchanged. Apply that bootstrap grant before submitting readiness. The other permissions remain under the staged Application's existing namespaces. The GET behavior is implemented by the [pinned executor](https://github.com/argoproj/argo-workflows/blob/v4.1.5/workflow/executor/resource.go).

The verifier is a standalone standard-library program mounted from immutable `perfeng-readiness-verifier-v1` in `argo-workflows`. It runs in the already pinned Python image as a non-root user with dropped capabilities, a read-only root filesystem and scratch `/tmp`. It performs no Kubernetes calls, subprocess orchestration or wait loops. The ConfigMap is applied before versioned child templates, then the parent. Its file is covered by the published-version guard; changing it after publication requires a new versioned file/ConfigMap/template reference.

## Evidence and limits

The final check rejects mismatched Application desired/compared sources and resolved revisions, conditions or an unfinished/failed operation; wrong Deployment image, runtime/registry ConfigMaps or credential references; and a Service whose type, selector or HTTP port differs from the reviewed shape. Each workload must have one desired/current/ready/updated replica and a current observed generation. StatefulSet revision identities must agree; the Deployment must also be fully available. The three fixed HTTP paths must return HTTP 200, `livez` and `readyz` must return exactly `ok` plus newline, and metrics must contain the exact readiness metric line. Requests have a ten-second timeout, a one-MiB response bound, disabled environment proxies and no redirects. Response bodies are not included in evidence.

The [v1 result schema](../deployments/release-readiness-result-v1.schema.json) binds the request digest/commit, selection commit, cluster UID, deployment revision/image and observed Application, Service and workload UIDs/generations. The output is written only after every check and probe succeeds. Failed steps retain their Workflow/node evidence and produce no successful final result.

These are sequential observations over a bounded interval, not an atomic release transaction. Foundation checks establish named rollout readiness, not backup recoverability or application-specific database/object-store authorization. Deployment image/configuration selection does not prove every Pod image ID. Metrics-server TLS, fresh node usage, benchmark authority, migration history, policy availability, performance Runs, smoke acceptance and scientific qualification remain separate checks. A green result neither activates a release nor waives those checks.

## Disposable-kind acceptance and recovery

Use an isolated disposable fixture with the pinned Workflows substrate, bootstrap Project and staged Application. Complete the explicit M04 preparation/manual sync first; this workflow will not enable a paused control plane or trigger Argo CD refresh/sync. Submit using the [preflight command](release-preflight-workflow.md#disposable-kind-verification), replacing its `--from` value with `workflowtemplate/perfeng-release-readiness-v1` and retaining the exact reviewed request bytes for that cluster.

Record the Workflow UID and final `result`. Repeat the same input and compare request/release identity; new Workflow evidence may observe later generations or UIDs after an explicitly authorized rollout. Exercise an unhealthy/missing Application, stale observed generation, wrong image/configuration reference and failing operational response on separate disposable fixtures. Each must stop without success evidence or repair. Prove the dedicated identity cannot patch/sync an Application, create Jobs/Secrets or modify workloads.

After failure, inspect the named failing step and existing owner diagnostics. Correct the confirmed prerequisite through its owning deployment/recovery procedure, then submit a new Workflow. Do not relax the release input, mutate a published verifier, delete retained evidence or let this workflow repair the cluster.

Offline gates are `make workflow-lint` and `make validate`; the latter renders and checks both Workflow and Job ConfigMap references against the staged Project. Unit tests cover identity/freshness failures, endpoint bounds/redirect rejection and output publication after successful verification. These checks do not establish live parity or retire the host path.
