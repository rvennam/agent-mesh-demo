#!/usr/bin/env bash
# Create a local kind cluster for the demo. Needs about 4 CPUs and 10 GB of memory for Docker.
#   ./scripts/kind-up.sh            create the cluster (name: agent-mesh) and switch kubectl to it
#   ./scripts/kind-up.sh delete     delete it
set -euo pipefail
NAME=${KIND_CLUSTER:-agent-mesh}
if [ "${1:-}" = delete ]; then kind delete cluster --name "$NAME"; exit; fi
kind create cluster --name "$NAME" --wait 120s --config - <<YAML
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
YAML
kubectl get nodes
