#!/bin/bash
# Read-only: only selected process cgroups, CRI list metadata and PID-owned TCP listeners.
# The CRI JSON is filtered on the node; full inspect/env are never transferred.

count=0
missing=0
hz=$(getconf CLK_TCK 2>/dev/null || printf 100)
boot=$(awk '/^btime /{print $2}' /proc/stat)
for pid in "$@"; do
  case "$pid" in *[!0-9]*|'') continue;; esac
  cg=$(cat "/proc/$pid/cgroup" 2>/dev/null) || { missing=$((missing+1)); continue; }
  case "$cg" in *kubepods*) ;; *) continue;; esac
  a=$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}')
  ns=$(readlink "/proc/$pid/ns/net" 2>/dev/null)
  b=$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}')
  [ -n "$a" ] && [ "$a" = "$b" ] && [ -n "$ns" ] || continue
  epoch=0
  case "$boot:$hz:$a" in *[!0-9:]*|::*|*:0:*) ;; *) epoch=$((boot+a/hz));; esac
  printf 'K8S_PROC\t%s\t%s\t%s\t%s\t%s\t%s\n' "$pid" "$a" "$b" "$(printf %s "$cg" | base64 | tr -d '\n')" "$ns" "$epoch"
  count=$((count+1))
  if [ "$count" -le 64 ] && command -v nsenter >/dev/null 2>&1 && command -v ss >/dev/null 2>&1; then
    out=$(timeout 3 nsenter -t "$pid" -n -- ss -lntpH 2>/dev/null)
    rc=$?
    after=$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}')
    if [ "$rc" = 0 ] && [ "$a" = "$after" ] && [ "$ns" = "$(readlink "/proc/$pid/ns/net" 2>/dev/null)" ]; then
      printf '%s\n' "$out" | awk -v p="$pid" '$0 ~ ("pid=" p "[,)]") {x=$4; port=x; sub(/^.*:/,"",port); sub(/:[^:]*$/,"",x); if(port ~ /^[0-9]+$/) printf "K8S_LISTENER\t%s\t%s\t%s\n",p,x,port}'
      printf 'K8S_LISTENER_STATUS\t%s\tobserved\n' "$pid"
    else printf 'K8S_LISTENER_STATUS\t%s\tunknown\n' "$pid"; fi
  fi
done
if [ "$count" -gt 0 ]; then
if command -v crictl >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
  c=$(timeout 8 crictl ps -o json 2>/dev/null)
  p=$(timeout 8 crictl pods -o json 2>/dev/null)
  if [ -n "$c" ] && [ -n "$p" ]; then
    export K8S_C="$c" K8S_P="$p"
    python3 -c 'import os,json,base64
try:
 c=json.loads(os.environ["K8S_C"]).get("containers",[]); p=json.loads(os.environ["K8S_P"]).get("items",[]); pods={x.get("id"):x.get("metadata",{}) for x in p}; out=[]
 for x in c:
  m=pods.get(x.get("podSandboxId"),{}); out.append(dict(id=x.get("id"),podUid=m.get("uid"),podName=m.get("name"),namespace=m.get("namespace"),containerName=(x.get("metadata") or {}).get("name"),image=(x.get("image") or {}).get("image")))
 print("K8S_META\t"+base64.b64encode(json.dumps(out).encode()).decode())
except Exception: pass' 2>/dev/null
    unset K8S_C K8S_P
  fi
fi
fi
printf 'K8S_MISSING\t%s\n' "$missing"
printf 'K8S_END\n'
