#!/usr/bin/env bash
# Solo UI (management chart: ClickHouse + OTel collector + UI) with two products on:
#   agentgateway  -> http://localhost:4000/age/  (tracing, cost management, budgets)
#   mesh (Istio)  -> http://localhost:4000/ie/graph  (service graph from ztunnel/waypoint metrics)
# The tracing policies that feed it are part of setup.yaml (manifests/80-tracing.yaml).
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/00-env.sh
require_licenses
echo ">> context $(kubectl config current-context)"

# `cluster` must equal Istio's cluster ID (CLUSTER_NAME, set in scripts/01-istio.sh), or the service graph shows
# every workload twice. Check:
#   kubectl -n istio-system get deploy istiod -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="CLUSTER_ID")].value}' 

echo ">> management chart ${MGMT_VERSION} (UI, telemetry collector, ClickHouse)"
helm upgrade -i management oci://us-docker.pkg.dev/solo-public/solo-enterprise-helm/charts/management \
  -n "${AGW_NS}" --create-namespace --version "${MGMT_VERSION}" \
  --set cluster="${CLUSTER_NAME}" \
  --set products.mesh.enabled=true \
  --set products.agentgateway.enabled=true \
  --set products.agentgateway.features.cost-management=true \
  --set service.type=ClusterIP \
  --set-string licensing.licenseKey="${AGENTGATEWAY_LICENSE_KEY}"

echo ">> wait"
kubectl -n "${AGW_NS}" rollout status statefulset/management-clickhouse-shard0 --timeout=300s | tail -1
kubectl -n "${AGW_NS}" rollout status statefulset/solo-enterprise-telemetry-collector --timeout=300s | tail -1   # a StatefulSet once the mesh product is on
kubectl -n "${AGW_NS}" rollout status deploy/solo-enterprise-ui --timeout=300s | tail -1

echo ">> open: kubectl -n ${AGW_NS} port-forward svc/solo-enterprise-ui 4000:80   ->  http://localhost:4000/age/  and  http://localhost:4000/ie/graph"
