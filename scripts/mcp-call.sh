#!/usr/bin/env bash
# mcp-call.sh <deployment-in-castellan-agents> <bearer-token> <tool> [json-args]
# initialize, then tools/call through the waypoint; prints the JSON-RPC result or the gateway's error.
set -uo pipefail
DEP=${1:?deployment}; TOK=${2:?token}; TOOL=${3:?tool}; ARGS=${4:-{\}}
URL=http://castellan-ops.castellan-tools.svc.cluster.local/mcp
kubectl -n castellan-agents exec deploy/"$DEP" -- env TOK="$TOK" URL="$URL" TOOL="$TOOL" ARGS="$ARGS" sh -c '
  H1="Content-Type: application/json"; H2="Accept: application/json, text/event-stream"; A="Authorization: Bearer $TOK"
  curl -s -m 20 "$URL" -H "$H1" -H "$H2" -H "$A" -D /tmp/h -o /dev/null \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"probe\",\"version\":\"0\"}}}"
  SID=$(grep -i mcp-session-id /tmp/h | cut -d" " -f2 | tr -d "\r")
  curl -s -m 20 "$URL" -H "$H1" -H "$H2" -H "$A" ${SID:+-H "mcp-session-id: $SID"} \
    -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"$TOOL\",\"arguments\":$ARGS}}" -w "\nHTTP %{http_code}\n"' 2>/dev/null \
  | sed -e 's/^event: message$//' -e 's/^data: //' | grep -v '^$'
