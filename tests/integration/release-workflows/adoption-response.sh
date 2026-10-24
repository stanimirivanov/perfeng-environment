#!/usr/bin/env bash
# Only the approved third trusted-main scenario may adapt the receipt transport.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == response-loss ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -z ${GITHUB_TOKEN:-} ]] || exit 2
declare -F k a adoption_case adoption_records > /dev/null || exit 2

PYTHONPATH=. uv run --locked --offline python "$source_dir/adoption_response.py" template "$fixture" "$results" \
  > "$fixture/adoption-response.template.json"
argo lint --offline --kinds=workflowtemplates gitops/release-workflows "$fixture/adoption-response.template.json"
k create -f "$fixture/adoption-response.template.json"
response_workflow=$(a submit --from workflowtemplate/perfeng-adoption-response-ci \
  --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
# A native Suspend immediately after the real planner is the only setup barrier.
response_deadline=$((SECONDS + 240))
while ((SECONDS < response_deadline)); do
  k -n argo-workflows get workflow "$response_workflow" -o json > "$fixture/adoption-response-barrier.workflow.json"
  if jq -e 'any(.status.nodes[]?; .displayName=="receipt-response-barrier" and .type=="Suspend" and .phase=="Running")' \
    "$fixture/adoption-response-barrier.workflow.json" > /dev/null; then break; fi
  jq -e '.status.phase==null or .status.phase=="" or .status.phase=="Pending" or .status.phase=="Running"' "$fixture/adoption-response-barrier.workflow.json" > /dev/null
  sleep 2
done
PYTHONPATH=. uv run --locked --offline python "$source_dir/adoption_response.py" expected "$fixture" "$results" \
  > "$fixture/ledger-response-expected.json"
adoption_records "$fixture/adoption-response-before.records.json"
jq -e 'length==0' "$fixture/adoption-response-before.records.json" > /dev/null

uv run --locked --offline python - "$fixture" "$source_dir" <<'PY'
import json, sys
from pathlib import Path
directory, source = map(Path, sys.argv[1:])
sys.path.insert(0, str(source))
from ledger_response_proxy import ADDRESS, certificate
certificate(directory, "ledger-response-proxy.argo-workflows.svc")
config = dict(apiVersion="v1", kind="Config", clusters=[dict(name="fault", cluster={"server": ADDRESS, "certificate-authority": "/fault/ca.crt"})],
    users=[dict(name="caller", user={"tokenFile": "/var/run/secrets/kubernetes.io/serviceaccount/token"})],
    contexts=[dict(name="fault", context=dict(cluster="fault", user="caller", namespace="argo-workflows"))], **{"current-context":"fault"})
(directory / "ledger-response-client.json").write_text(json.dumps(config))
PY
k -n argo-workflows create configmap ledger-response-proxy \
  --from-file="proxy.py=$source_dir/ledger_response_proxy.py" \
  --from-file="expected.json=$fixture/ledger-response-expected.json" \
  --from-file="client.json=$fixture/ledger-response-client.json" --from-file="ca.crt=$fixture/ledger-response.crt"
k -n argo-workflows create secret tls ledger-response-proxy-tls \
  --cert="$fixture/ledger-response.crt" --key="$fixture/ledger-response.key"
k create -f "$source_dir/ledger-response-proxy.yaml"
k -n argo-workflows wait pod/ledger-response-proxy --for=condition=Ready --timeout=90s
a resume "$response_workflow"
adoption_case response-loss response-loss "$response_workflow"
# The failure must have stored only a receipt. Fresh production submission has
# no fixture template reference or transport override; keep the proxy as evidence.
adoption_case recovered success
jq -S '.[]|select(.immutable==true)' "$fixture/adoption-response-loss.records.json" > "$fixture/adoption-response.receipt.json"
jq -S '.[]|select(.immutable==true)' "$fixture/adoption-recovered.records.json" > "$fixture/adoption-recovered.receipt.json"
cmp "$fixture/adoption-response.receipt.json" "$fixture/adoption-recovered.receipt.json"
