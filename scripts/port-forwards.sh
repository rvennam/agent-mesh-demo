#!/usr/bin/env bash
# port-forwards.sh start|stop|status  -- resilient port-forwards for the demo tabs.
# Each forward runs in a retry loop, so a pod restart or a dropped connection re-establishes it within a second.
#   8080 chat (ingress)   8081 shadow agent (ingress)   8180 keycloak   4000 solo ui
set -uo pipefail
PIDFILE=/tmp/castellan-pf.pids
start() {
  stop >/dev/null 2>&1
  : > "$PIDFILE"
  fwd() { # fwd <ns> <svc> <ports...>
    local ns=$1 svc=$2; shift 2
    ( while true; do kubectl -n "$ns" port-forward "svc/$svc" "$@" >/dev/null 2>&1; sleep 1; done ) &
    echo $! >> "$PIDFILE"
  }
  fwd castellan-ingress   castellan-ingress  8080:8080 8081:8081
  fwd castellan-idp       keycloak           8180:8080
  fwd agentgateway-system solo-enterprise-ui 4000:80
  sleep 3; status
}
stop() {
  [ -f "$PIDFILE" ] && while read -r pid; do pkill -P "$pid" 2>/dev/null; kill "$pid" 2>/dev/null; done < "$PIDFILE"
  pkill -f 'kubectl -n castellan-ingress port-forward' 2>/dev/null
  pkill -f 'kubectl -n castellan-idp port-forward' 2>/dev/null
  pkill -f 'kubectl -n agentgateway-system port-forward svc/solo-enterprise-ui' 2>/dev/null
  rm -f "$PIDFILE"; echo "port-forwards stopped"
}
status() {
  for p in 8080 8081 8180 4000; do printf "localhost:%s -> %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code}' -m 3 localhost:$p/ || echo down)"; done
}
case "${1:-status}" in start) start;; stop) stop;; status) status;; *) echo "usage: $0 start|stop|status"; exit 1;; esac
