#!/usr/bin/env bash
# Read only Docker networking metadata on the managed peer, never Config/Env.
set -uo pipefail
export LC_ALL=C TZ=UTC
if ! command -v docker >/dev/null; then
  printf 'CHECK_DOCKER_STATUS\tnot_available\n'
  exit 0
fi
tmp=$(mktemp -d) || exit 2
trap 'rm -rf -- "$tmp"' EXIT
if [[ -n "${1:-}" ]]; then
  [[ "$1" =~ ^[0-9a-f]{64}$ ]] || exit 2
  printf '%s\n' "$1" >"$tmp/ids"
elif ! timeout 8 docker ps --no-trunc -q >"$tmp/ids" 2>/dev/null; then
  printf 'CHECK_DOCKER_STATUS\tfailed\n'
  exit 0
fi
if (( $(wc -l <"$tmp/ids") > 64 )); then
  printf 'CHECK_DOCKER_STATUS\tlimit\n'
  exit 0
fi
ids=$(cat "$tmp/ids")
if [[ -z "$ids" ]]; then
  printf 'CHECK_DOCKER_STATUS\tcomplete\n'
  exit 0
fi
# IDs originate from docker ps and are checked before expansion.
for container_id in $ids; do
  [[ "$container_id" =~ ^[0-9a-f]{64}$ ]] || exit 2
done
format=$'{{.Id}}\t{{json .Name}}\t{{.State.Pid}}\t{{json .NetworkSettings.Ports}}\t{{range .NetworkSettings.Networks}}{{.IPAddress}} {{.GlobalIPv6Address}} {{end}}'
if ! timeout 8 docker inspect --type=container --format "$format" $ids >"$tmp/rows" 2>/dev/null; then
  printf 'CHECK_DOCKER_STATUS\tfailed\n'
  exit 0
fi
while IFS=$'\t' read -r container_id name pid ports addresses; do
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || continue
  start=$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}')
  netns=$(readlink "/proc/$pid/ns/net" 2>/dev/null)
  if [[ -z "$start" || -z "$netns" ]]; then
    printf 'CHECK_WARNING\t容器进程或网络空间不可读，未确认容器归属\n'
    continue
  fi
  printf 'CHECK_DOCKER\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$container_id" "$name" "$pid" "$start" "$netns" "$ports" "$addresses"
done <"$tmp/rows"
printf 'CHECK_DOCKER_STATUS\tcomplete\n'
