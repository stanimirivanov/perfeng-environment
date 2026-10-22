# Main-only real chart/PVC fixture. Registration has no setup side effects.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && ${database_pod:-} == postgres-0 ]] || exit 2
declare -F k a sql > /dev/null || exit 2

postgres_chart_connect() {
  local label=$1 job
  job=$(k create -f "$source_dir/postgres-chart-client.yaml" -o json | jq -er .metadata.name)
  k -n perf-platform wait --for=condition=Complete "job/$job" --timeout=150s
  k -n perf-platform get job "$job" -o json > "$results/postgres-chart-$label.client.json"
  jq -e '.status.succeeded==1' "$results/postgres-chart-$label.client.json" > /dev/null
}
postgres_chart_native_ready() {
  jq -e '.name=="postgres" and .namespace=="perf-platform" and (.uid|length)>0 and
    (.generation|type)=="number" and .generation>=1 and .generation==.observedGeneration and .desired==1 and .replicas==1 and .ready==1 and .updated==1 and
    (.currentRevision|length)>0 and .currentRevision==.updateRevision' "$1" > /dev/null
}
postgres_chart_observe() {
  local label=$1 workflow
  workflow=$(a submit "$source_dir/observer-workflow.yaml" --entrypoint postgres -o json | jq -er .metadata.name)
  timeout 630s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow"
  a get "$workflow" -o json > "$results/postgres-chart-$label.workflow.json"
  jq -e '.status.phase=="Succeeded"' "$results/postgres-chart-$label.workflow.json" > /dev/null
  jq -er '.status.nodes[.metadata.name].outputs.parameters[] | select(.name=="postgres") | .value' \
    "$results/postgres-chart-$label.workflow.json" > "$results/postgres-chart-$label.observation.json"
  postgres_chart_native_ready "$results/postgres-chart-$label.observation.json"
}
postgres_chart_storage() {
  k -n perf-platform get pvc data-postgres-0 -o json \
    | jq -e '{uid:.metadata.uid,volume:.spec.volumeName,phase:.status.phase,class:.spec.storageClassName} |
      select(.phase=="Bound" and .class=="standard" and (.uid|length)>0 and (.volume|length)>0)' > "$1"
}
postgres_chart_setup() {
  local pv before_uid after_uid name uid
  echo 'PostgreSQL chart: first install and retained-PVC Pod restart'
  k get storageclass standard -o json \
    | jq -e '.provisioner=="rancher.io/local-path" and .volumeBindingMode=="WaitForFirstConsumer"' > /dev/null
  k label node perfeng-local-control-plane workload=control-plane
  helm template postgres charts/postgres --namespace perf-platform > "$fixture/postgres-chart.yaml"
  uv run --locked --offline python - "$fixture/postgres-chart.yaml" <<'PY'
import sys, yaml
from pathlib import Path
objects = list(yaml.safe_load_all(Path(sys.argv[1]).read_text()))
assert {(o['kind'],o['metadata']['namespace'],o['metadata']['name']) for o in objects} == {
    ('StatefulSet','perf-platform','postgres'),('Service','perf-platform','postgres')}
assert len(objects)==2
PY
  k create -f "$fixture/postgres-chart.yaml"
  k -n perf-platform rollout status statefulset/postgres --timeout=240s
  k -n perf-platform wait --for=condition=Ready "pod/$database_pod" --timeout=120s
  postgres_chart_connect first
  k apply -f gitops/release-workflows/rbac/readiness
  postgres_chart_observe first
  postgres_chart_storage "$results/postgres-chart-first.storage.json"
  pv=$(jq -er .volume "$results/postgres-chart-first.storage.json")
  k get pv "$pv" -o json | jq '{uid:.metadata.uid,name:.metadata.name,claim:.spec.claimRef.uid}' > "$results/postgres-chart-first.pv.json"
  k -n perf-platform get service postgres -o json | jq -S . > "$results/postgres-chart-first.service.json"
  k -n perf-platform get secret perfeng-postgres-auth -o json | jq -S . > "$fixture/postgres-chart-secret-before.json"
  before_uid=$(k -n perf-platform get pod "$database_pod" -o jsonpath='{.metadata.uid}')
  # This canary lives outside perfeng, which must stay empty for migrator parity.
  sql -d postgres <<'SQL'
CREATE TABLE public.chart_ci_canary (id integer PRIMARY KEY, value text NOT NULL);
INSERT INTO public.chart_ci_canary VALUES (1,'postgres-chart-retention-v1');
SQL
  sql -d postgres -c "SELECT 'public.chart_ci_canary'::regclass::oid::text || '|' || value FROM public.chart_ci_canary WHERE id=1;" > "$results/postgres-chart-first.canary.txt"
  # Configure only causal CI logging; no change to the released chart Pod template.
  sql -c "ALTER SYSTEM SET log_line_prefix = 'user=%u database=%d app=%a ';"
  sql -c 'SELECT pg_reload_conf();' > /dev/null
  k -n perf-platform rollout restart statefulset/postgres
  k -n perf-platform rollout status statefulset/postgres --timeout=240s
  k -n perf-platform wait --for=condition=Ready "pod/$database_pod" --timeout=120s
  postgres_chart_connect restarted
  after_uid=$(k -n perf-platform get pod "$database_pod" -o jsonpath='{.metadata.uid}')
  [[ -n $before_uid && -n $after_uid && $before_uid != "$after_uid" ]]
  postgres_chart_observe restarted
  jq -s -e '.[0].uid==.[1].uid and .[1].generation>.[0].generation and .[0].currentRevision!=.[1].currentRevision' \
    "$results/postgres-chart-first.observation.json" "$results/postgres-chart-restarted.observation.json" > /dev/null
  postgres_chart_storage "$results/postgres-chart-restarted.storage.json"
  cmp "$results/postgres-chart-first.storage.json" "$results/postgres-chart-restarted.storage.json"
  k get pv "$pv" -o json | jq '{uid:.metadata.uid,name:.metadata.name,claim:.spec.claimRef.uid}' > "$results/postgres-chart-restarted.pv.json"
  cmp "$results/postgres-chart-first.pv.json" "$results/postgres-chart-restarted.pv.json"
  k -n perf-platform get service postgres -o json | jq -S . > "$results/postgres-chart-restarted.service.json"
  cmp "$results/postgres-chart-first.service.json" "$results/postgres-chart-restarted.service.json"
  k -n perf-platform get secret perfeng-postgres-auth -o json | jq -S . > "$fixture/postgres-chart-secret-after.json"
  cmp "$fixture/postgres-chart-secret-before.json" "$fixture/postgres-chart-secret-after.json"
  sql -d postgres -c "SELECT 'public.chart_ci_canary'::regclass::oid::text || '|' || value FROM public.chart_ci_canary WHERE id=1;" > "$results/postgres-chart-restarted.canary.txt"
  cmp "$results/postgres-chart-first.canary.txt" "$results/postgres-chart-restarted.canary.txt"
  [[ $(sql -c 'SHOW log_line_prefix;') == 'user=%u database=%d app=%a ' ]]
  jq -n --arg before "$before_uid" --arg after "$after_uid" '{before:$before,after:$after}' > "$results/postgres-chart-pod-uids.json"
  for label in first restarted; do
    name=$(jq -er .metadata.name "$results/postgres-chart-$label.workflow.json") uid=$(jq -er .metadata.uid "$results/postgres-chart-$label.workflow.json")
    [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
  done
  echo 'PostgreSQL chart/PVC restart passed; retained-environment recovery remains unproven.'
}

postgres_application_state() {
  local prefix=$1 pv
  postgres_chart_storage "$prefix.storage.json"
  pv=$(jq -er .volume "$prefix.storage.json")
  k get pv "$pv" -o json | jq -S '{uid:.metadata.uid,name:.metadata.name,claim:.spec.claimRef.uid}' > "$prefix.pv.json"
  k -n perf-platform get service postgres -o json | jq -S '{uid:.metadata.uid,spec}' > "$prefix.service.json"
  k -n perf-platform get statefulset postgres -o json \
    | jq -S '{uid:.metadata.uid,selector:.spec.selector,service:.spec.serviceName,claims:.spec.volumeClaimTemplates,
      images:[.spec.template.spec.containers[].image],secrets:[.spec.template.spec.volumes[]? | .secret.secretName // empty]}' > "$prefix.statefulset.json"
  external_secrets "$prefix.external.json"
  server_inputs_snapshot "$prefix.server.json"
  k -n argocd get secret ci-perfeng-environment-repository -o json | jq -S . > "$prefix.repository.json"
  role_snapshot "$prefix.roles.json"
  runtime_acl_snapshot "$prefix.acl.json"
  migration_snapshot "$prefix.schema.json"
  sql -d postgres -c "SELECT 'public.chart_ci_canary'::regclass::oid::text || '|' || value FROM public.chart_ci_canary WHERE id=1;" > "$prefix.canary.txt"
  sql -c "SELECT 'public.migration_ci_sentinel'::regclass::oid::text || '|' || value FROM public.migration_ci_sentinel WHERE id=1;" > "$prefix.sentinel.txt"
  k -n argocd get applications perfeng-local-root perfeng-local-control-plane -o json \
    | jq -S '[.items[] | {uid:.metadata.uid,spec,skip:.metadata.annotations["argocd.argoproj.io/skip-reconcile"]}] | sort_by(.uid)' > "$prefix.other-applications.json"
}

postgres_application_acceptance() {
  local revision application_uid label field first_pod before_pod name uid
  declare -F argocd_sync external_secrets server_inputs_snapshot role_snapshot runtime_acl_snapshot migration_snapshot > /dev/null || return 2
  echo 'PostgreSQL Application: adopt the prepared chart and retained database'
  revision=$(jq -er '."selection-revision"' "$fixture/parent-parameters.json")
  PYTHONPATH=. uv run --locked --offline python "$source_dir/argocd_inputs.py" postgres "$revision" "$fixture/postgres-application.json"
  postgres_application_state "$fixture/postgres-application-before"
  before_pod=$(k -n perf-platform get pod "$database_pod" -o jsonpath='{.metadata.uid}')
  cmp "$results/postgres-chart-restarted.canary.txt" "$fixture/postgres-application-before.canary.txt"
  k create -f "$fixture/postgres-application.json"
  application_uid=$(k -n argocd get application perfeng-local-postgres -o jsonpath='{.metadata.uid}')
  k -n argocd wait application/perfeng-local-postgres --for="jsonpath={.status.sync.revision}=$revision" --timeout=240s
  k -n argocd get application perfeng-local-postgres -o json \
    | jq -e '(.status.conditions // [] | length)==0 and (.status.sync.status=="Synced" or .status.sync.status=="OutOfSync")' > /dev/null
  for label in first repeat corrected; do
    if [[ $label == corrected ]]; then
      k -n perf-platform patch service postgres --type=json \
        -p '[{"op":"test","path":"/spec/ports/0/name","value":"postgres"},{"op":"replace","path":"/spec/ports/0/name","value":"pg-ci-drift"}]'
      k -n argocd annotate application perfeng-local-postgres argocd.argoproj.io/refresh=hard --overwrite
      k -n argocd wait application/perfeng-local-postgres --for=jsonpath='{.status.sync.status}'=OutOfSync --timeout=180s
      k -n argocd get application perfeng-local-postgres -o json \
        | jq -e '.spec.syncPolicy.automated=={enabled:false,prune:false,selfHeal:false} and .operation==null and
          ([.status.resources[] | select(.kind=="Service" and .name=="postgres" and .status=="OutOfSync")]|length)==1' > /dev/null
      [[ $(k -n perf-platform get service postgres -o jsonpath='{.spec.ports[0].name}') == pg-ci-drift ]]
      printf '%s\n' 'PostgreSQL Service drift observed without automatic repair' > "$results/postgres-application-drift.outcome.txt"
    fi
    argocd_sync perfeng-local-postgres "$label"
    k -n argocd get application perfeng-local-postgres -o json > "$fixture/postgres-application-current.json"
    PYTHONPATH=. uv run --locked --offline python "$source_dir/argocd_inputs.py" postgres-converged \
      "$fixture/postgres-application-current.json" "$application_uid" "$label" "$revision"
    jq '{uid:.metadata.uid,spec,sync:.status.sync,health:.status.health,phase:.status.operationState.phase,case:.status.operationState.operation.info}' \
      "$fixture/postgres-application-current.json" > "$results/postgres-application-$label.json"
    k -n perf-platform rollout status statefulset/postgres --timeout=240s
    k -n perf-platform wait --for=condition=Ready "pod/$database_pod" --timeout=120s
    postgres_chart_connect "adopt-$label"
    postgres_chart_observe "adopt-$label"
    postgres_application_state "$fixture/postgres-application-$label"
    for field in storage pv service statefulset external server repository roles acl schema other-applications; do
      cmp "$fixture/postgres-application-before.$field.json" "$fixture/postgres-application-$label.$field.json"
    done
    for field in canary sentinel; do
      cmp "$fixture/postgres-application-before.$field.txt" "$fixture/postgres-application-$label.$field.txt"
    done
    if [[ $label == first ]]; then
      first_pod=$(k -n perf-platform get pod "$database_pod" -o jsonpath='{.metadata.uid}')
    else
      [[ $(k -n perf-platform get pod "$database_pod" -o jsonpath='{.metadata.uid}') == "$first_pod" ]]
    fi
  done
  jq -n --arg before "$before_pod" --arg after "$first_pod" '{before:$before,after:$after}' > "$results/postgres-application-pod-uids.json"
  for label in first repeat corrected; do
    name=$(jq -er .metadata.name "$results/postgres-chart-adopt-$label.workflow.json")
    uid=$(jq -er .metadata.uid "$results/postgres-chart-adopt-$label.workflow.json")
    [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
    name=$(jq -er .metadata.name "$results/postgres-chart-adopt-$label.client.json")
    uid=$(jq -er .metadata.uid "$results/postgres-chart-adopt-$label.client.json")
    [[ $(k -n perf-platform get job "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
  done
  k -n argocd get application perfeng-local-root -o json \
    | jq -e '.metadata.annotations["argocd.argoproj.io/skip-reconcile"]=="true" and .spec.syncPolicy.automated.enabled==false and .operation==null' > /dev/null
  [[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
  echo 'PostgreSQL Application adoption, native readiness, TCP access and state retention passed; root remains skipped.'
}
