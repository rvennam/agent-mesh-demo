#!/usr/bin/env bash
# Install Solo Enterprise for agentgateway with Istio integration and the built-in STS.
# Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/00-env.sh
require_licenses
echo ">> context $(kubectl config current-context)"

# Token service (STS) settings: manifests/02-sts-token-exchange-values.yaml

echo ">> CRDs ${AGENTGATEWAY_VERSION}"
helm upgrade -i enterprise-agentgateway-crds \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway-crds \
  --version "${AGENTGATEWAY_VERSION}" -n "${AGW_NS}" --create-namespace

echo ">> control plane (istio.autoEnabled -> waypoint GatewayClass; tokenExchange -> STS on :7777)"
helm upgrade -i enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  --version "${AGENTGATEWAY_VERSION}" -n "${AGW_NS}" \
  --set-string licensing.licenseKey="${AGENTGATEWAY_LICENSE_KEY}" \
  --set istio.autoEnabled=true \
  --set istio.clusterId="${CLUSTER_NAME}" \
  -f manifests/02-sts-token-exchange-values.yaml

echo ">> wait"
kubectl -n "${AGW_NS}" rollout status deploy/enterprise-agentgateway --timeout=180s
kubectl get gatewayclass
kubectl -n "${AGW_NS}" get svc enterprise-agentgateway
