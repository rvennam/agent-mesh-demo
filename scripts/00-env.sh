# Source this file: . scripts/00-env.sh
# Settings for the install scripts and the notebook. Secrets come from your shell, not from this file.
# Everything runs against your CURRENT kubectl context.

# Name of this cluster in the mesh. Used as the Istio cluster ID, the SPIFFE trust domain
# (spiffe://<CLUSTER_NAME>/ns/<ns>/sa/<sa>) and the cluster name shown in the Solo UI.
export CLUSTER_NAME=${CLUSTER_NAME:-agent-mesh}

# Kubernetes platform: "gke" needs istio-cni's GKE settings; anything else (kind, EKS, AKS, ...) uses the defaults.
# Auto-detected from the first node's providerID when unset.
if [ -z "${PLATFORM:-}" ]; then
  case "$(kubectl get nodes -o jsonpath='{.items[0].spec.providerID}' 2>/dev/null)" in
    gce://*) PLATFORM=gke ;;
    kind://*) PLATFORM=kind ;;
    *) PLATFORM=default ;;
  esac
fi
export PLATFORM

# Solo distribution of Istio (ambient)
export ISTIO_VERSION=${ISTIO_VERSION:-1.31.0}
export ISTIO_IMAGE=${ISTIO_VERSION}-solo
export ISTIO_REPO=us-docker.pkg.dev/soloio-img/istio
export ISTIO_HELM_REPO=us-docker.pkg.dev/soloio-img/istio-helm

# Solo Enterprise for agentgateway and the Solo UI
export AGENTGATEWAY_VERSION=${AGENTGATEWAY_VERSION:-v2026.9.1}
export MGMT_VERSION=${MGMT_VERSION:-0.5.8}
export AGW_NS=agentgateway-system

# Demo namespaces
export AGENTS_NS=castellan-agents
export TOOLS_NS=castellan-tools
export INGRESS_NS=castellan-ingress

# Demo images (multi-arch, built from agent/ and mcp-server/ by .github/workflows/images.yaml).
# To use your own registry: scripts/build-images.sh, then set these two before running the notebook.
export AGENT_IMAGE=${AGENT_IMAGE:-ghcr.io/rvennam/agent-mesh-demo/castellan-assistant:v0.3.5}
export MCP_IMAGE=${MCP_IMAGE:-ghcr.io/rvennam/agent-mesh-demo/castellan-ops-mcp:v0.1.0}

# Keycloak, the in-cluster stand-in for the enterprise IdP. Fictional demo-only values; the realm is
# imported from manifests/05-keycloak.yaml. Never reuse these outside this demo.
export KC_REALM=castellan
export KC_INTERNAL_URL=http://keycloak.castellan-idp.svc.cluster.local:8080          # what the ingress and STS use
export KC_BROWSER_URL=http://localhost:8180                                          # what the browser uses (port-forward)
export KC_ISSUER="${KC_BROWSER_URL}/realms/${KC_REALM}"
export OIDC_CLIENT_ID=castellan-assistant
export OIDC_CLIENT_SECRET=castellan-assistant-secret
export DEMO_USER_READER=avery.analyst
export DEMO_USER_ADMIN=morgan.admin
export DEMO_USER_PASSWORD=Castellan-Demo-2026

# Licenses are only needed by the install scripts (scripts/install.sh); the notebook needs OPENAI_API_KEY.
export SOLO_ISTIO_LICENSE_KEY=${SOLO_ISTIO_LICENSE_KEY:-${GLOO_MESH_LICENSE_KEY:-}}
require_licenses() {
  : "${SOLO_ISTIO_LICENSE_KEY:?set SOLO_ISTIO_LICENSE_KEY (Solo distribution of Istio, Enterprise)}"
  : "${AGENTGATEWAY_LICENSE_KEY:?set AGENTGATEWAY_LICENSE_KEY (Solo Enterprise for agentgateway)}"
}
