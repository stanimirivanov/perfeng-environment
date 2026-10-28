#!/usr/bin/env bash
# Shared fixture-only setup and private snapshots; sourcing performs no API writes.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2

maintenance_install() {
  k apply -f gitops/release-workflows/maintenance-resources-v1.yaml
  k apply -f gitops/release-workflows/release-maintenance-pause-v1.yaml
  k apply -f gitops/release-workflows/rbac/maintenance-pause
  k apply -f gitops/release-workflows-bootstrap/maintenance-pause-application-access.yaml
  k apply -f gitops/release-workflows-bootstrap/maintenance-pause-ledger-access.yaml
}

maintenance_snapshot() {
  local prefix=$1
  k -n argocd get application perfeng-local-root perfeng-local-control-plane -o json > "$prefix.applications.json"
  k -n perf-platform get deployment control-plane -o json > "$prefix.deployment.json"
  k -n perf-platform get pods -l app.kubernetes.io/instance=control-plane,app.kubernetes.io/name=perfeng-control-plane -o json > "$prefix.server-pods.json"
  k get namespace kube-system -o json > "$prefix.namespace.json"
  adoption_records "$prefix.records.json"
  root_child_snapshot "$prefix.children.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" children \
    "$prefix.children.json" "$fixture/root-tree.yaml" "$fixture/root-children-before.json"
  server_inputs_snapshot "$prefix.secrets.json"
  server_config_snapshot "$prefix.config.json"
  k -n argo-workflows get secret release-git-read -o json | jq -S . > "$prefix.git.json"
}
