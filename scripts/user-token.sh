#!/usr/bin/env bash
# user-token.sh <username>  -> prints a Keycloak ACCESS token for that demo user (password grant, in-cluster).
# For notebook CLI cells and validation only. The browser demo uses the real login at the ingress.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/00-env.sh
USER=${1:?username}
kubectl -n castellan-agents exec deploy/probe -- curl -s "${KC_INTERNAL_URL}/realms/${KC_REALM}/protocol/openid-connect/token" \
  -d grant_type=password -d client_id="${OIDC_CLIENT_ID}" -d client_secret="${OIDC_CLIENT_SECRET}" \
  -d username="${USER}" -d password="${DEMO_USER_PASSWORD}" -d scope="openid profile email" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("access_token") or json.dumps(d))'
