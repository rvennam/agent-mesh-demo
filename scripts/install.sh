#!/usr/bin/env bash
# Install the platform on your CURRENT kubectl context: Solo Istio (ambient), Solo Enterprise for agentgateway
# (with the token service), and the Solo UI. The demo itself (setup.yaml and the policies) is applied by the notebook.
set -euo pipefail
cd "$(dirname "$0")/.."
echo ">> installing into context: $(kubectl config current-context)"
./scripts/01-istio.sh
./scripts/02-agentgateway.sh
./scripts/03-solo-ui.sh
echo ">> platform installed. Next: open agent-mesh-demo.ipynb (Bash kernel) and run it top to bottom."
