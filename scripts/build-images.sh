#!/usr/bin/env bash
# Build and push the two demo images (multi-arch) to your own registry, then point the demo at them:
#   REGISTRY=registry.example.com/agent-mesh ./scripts/build-images.sh
#   export AGENT_IMAGE=... MCP_IMAGE=...     (printed at the end)
# Not needed if you use the published images (the defaults in scripts/00-env.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${REGISTRY:?set REGISTRY, e.g. ghcr.io/<you>/agent-mesh-demo}"
AGENT_TAG=${AGENT_TAG:-v0.3.5}; MCP_TAG=${MCP_TAG:-v0.1.0}; PLATFORMS=${PLATFORMS:-linux/amd64,linux/arm64}
docker buildx build --platform "$PLATFORMS" -t "$REGISTRY/castellan-assistant:$AGENT_TAG" --push agent
docker buildx build --platform "$PLATFORMS" -t "$REGISTRY/castellan-ops-mcp:$MCP_TAG" --push mcp-server
echo "export AGENT_IMAGE=$REGISTRY/castellan-assistant:$AGENT_TAG"
echo "export MCP_IMAGE=$REGISTRY/castellan-ops-mcp:$MCP_TAG"
