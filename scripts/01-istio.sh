#!/usr/bin/env bash
# Install the Solo distribution of Istio in ambient mode. Idempotent (helm upgrade -i).
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/00-env.sh
require_licenses
echo ">> context $(kubectl config current-context)  platform=${PLATFORM}  cluster=${CLUSTER_NAME}"

echo ">> Gateway API CRDs"
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.1/standard-install.yaml

echo ">> istio-base"
helm upgrade -i istio-base oci://${ISTIO_HELM_REPO}/base \
  -n istio-system --create-namespace --version "${ISTIO_IMAGE}" --wait

# Cluster ID and trust domain both use CLUSTER_NAME, so SPIFFE IDs read spiffe://<CLUSTER_NAME>/ns/<ns>/sa/<sa>
# and the Solo UI shows this cluster by name. ztunnel, agentgateway (istio.clusterId) and the Solo UI (cluster)
# must use the same value. The cluster.local alias keeps default-trust-domain certificates valid.
echo ">> istiod (ambient profile)"
helm upgrade -i istiod oci://${ISTIO_HELM_REPO}/istiod \
  -n istio-system --version "${ISTIO_IMAGE}" --wait \
  --set profile=ambient \
  --set global.tag="${ISTIO_IMAGE}" \
  --set global.hub="${ISTIO_REPO}" \
  --set license.value="${SOLO_ISTIO_LICENSE_KEY}" \
  --set meshConfig.trustDomain="${CLUSTER_NAME}" \
  --set "meshConfig.trustDomainAliases[0]=cluster.local" \
  --set global.multiCluster.clusterName="${CLUSTER_NAME}"

echo ">> istio-cni"
CNI_PLATFORM=""; [ "${PLATFORM}" = gke ] && CNI_PLATFORM="  platform: gke   # GKE: /opt/cni/bin is read-only"
helm upgrade -i istio-cni oci://${ISTIO_HELM_REPO}/cni \
  -n istio-system --version "${ISTIO_IMAGE}" --wait -f - <<VALUES
ambient:
  dnsCapture: true
excludeNamespaces:
  - istio-system
  - kube-system
global:
  hub: ${ISTIO_REPO}
  tag: ${ISTIO_IMAGE}
${CNI_PLATFORM}
profile: ambient
VALUES

echo ">> ztunnel"
helm upgrade -i ztunnel oci://${ISTIO_HELM_REPO}/ztunnel \
  -n istio-system --version "${ISTIO_IMAGE}" --wait -f - <<VALUES
configValidation: true
enabled: true
env:
  L7_ENABLED: "true"
hub: ${ISTIO_REPO}
istioNamespace: istio-system
namespace: istio-system
profile: ambient
multiCluster:
  clusterName: ${CLUSTER_NAME}
proxy:
  clusterDomain: cluster.local
tag: ${ISTIO_IMAGE}
terminationGracePeriodSeconds: 29
variant: distroless
VALUES

echo ">> verify"
kubectl get pods -n istio-system
