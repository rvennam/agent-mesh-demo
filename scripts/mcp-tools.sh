#!/usr/bin/env bash
# mcp-tools.sh <deployment-in-castellan-agents> [bearer-token]
# Lists the MCP tools that the waypoint lets THIS workload (+ this token) see: initialize, then tools/list.
set -uo pipefail
DEP=${1:?deployment}; TOK=${2:-}
URL=http://castellan-ops.castellan-tools.svc.cluster.local/mcp
RAW=$(kubectl -n castellan-agents exec deploy/"$DEP" -- env TOK="$TOK" URL="$URL" sh -c '
  AUTH=""; [ -n "$TOK" ] && AUTH="Authorization: Bearer $TOK"
  INIT=$(curl -s -m 20 "$URL" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "$AUTH" -D /tmp/h -o /tmp/b -w "%{http_code}" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"probe\",\"version\":\"0\"}}}")
  SID=$(grep -i mcp-session-id /tmp/h | cut -d" " -f2 | tr -d "\r")
  echo "INIT=$INIT"; [ "$INIT" = 200 ] || { cat /tmp/b; echo; exit 0; }
  curl -s -m 20 "$URL" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "$AUTH" ${SID:+-H "mcp-session-id: $SID"} \
    -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}" -w "\nLIST=%{http_code}\n"' 2>/dev/null)
python3 - "$DEP" <<'PY' "$RAW"
import sys, re, json
dep, raw = sys.argv[1], sys.argv[2]
init = re.search(r"INIT=(\d+)", raw); lst = re.search(r"LIST=(\d+)", raw)
m = re.search(r"data: (\{.*\})", raw)
tools = []
if m:
    j = json.loads(m.group(1)); tools = [t["name"] for t in j.get("result", {}).get("tools", [])]
    err = j.get("error", {}).get("message")
else:
    err = " ".join(l for l in raw.splitlines() if not l.startswith(("INIT=", "LIST=")))[:120]
status = f"HTTP {init.group(1) if init else '?'}" + (f"/{lst.group(1)}" if lst else "")
print(f"  {dep:<8} {status:<14} tools: {tools if tools else '[]' + (f'  ({err})' if err else '')}")
PY
