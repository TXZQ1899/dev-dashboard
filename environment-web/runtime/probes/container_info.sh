#!/usr/bin/env bash
# Read selected Docker metadata or one bounded directory; never read file contents.
set -uo pipefail
export LC_ALL=C
pid=$1; expected=$2; id=$3; mode=$4; shift 4
[[ "$pid" =~ ^[1-9][0-9]*$ && "$expected" =~ ^[0-9]+$ && "$id" =~ ^[0-9a-f]{64}$ ]] || exit 2
ticks() { sed 's/^.*) //' "/proc/$1/stat" 2>/dev/null | awk '{print $20}'; }
verify() {
  [[ "$(ticks "$pid")" == "$expected" ]] || return 1
  local init current parent n
  init=$(timeout 12 docker inspect --type container --format '{{.State.Pid}}' "$id" 2>/dev/null) || return 1
  [[ "$init" =~ ^[1-9][0-9]*$ ]] || return 1
  current=$pid
  for ((n=0; n<128; n++)); do
    [[ "$current" == "$init" ]] && return 0
    parent=$(sed 's/^.*) //' "/proc/$current/stat" 2>/dev/null | awk '{print $2}')
    [[ "$parent" =~ ^[0-9]+$ && "$parent" -gt 1 && "$parent" != "$current" ]] || return 1
    current=$parent
  done
  return 1
}
verify || exit 3
if [[ "$mode" == info ]]; then
  fmt=$'{{json .Id}}\t{{json .Name}}\t{{json .Config.Image}}\t{{json .Image}}\t{{json .State.Pid}}\t{{json .Mounts}}'
  data=$(timeout 12 docker inspect --type container --format "$fmt" "$id" 2>/dev/null) || exit 4
  printf 'INFO\t%s\n' "$(printf '%s' "$data" | base64 | tr -d '\n')"
elif [[ "$mode" == browse ]]; then
  source=$1; relative=$2; offset=$3
  [[ "$offset" =~ ^[0-9]+$ && "$offset" -le 1000000 ]] || exit 2
  [[ "$source" == /* && "$relative" != /* ]] || exit 4
  path=$source
  if [[ -n "$relative" ]]; then
    IFS=/ read -r -a parts <<< "$relative"
    for part in "${parts[@]}"; do
      [[ -n "$part" && "$part" != . && "$part" != .. ]] || exit 4
      path=${path%/}/$part
      [[ ! -L "$path" ]] || exit 4
    done
  fi
  [[ "$(readlink -f -- "$source")" == "$source" && "$(readlink -f -- "$path")" == "$path" && -r "$path" ]] || exit 4
  errors=$(mktemp) || exit 2
  trap 'rm -f -- "$errors"' EXIT
  printf 'FILES_BEGIN\n'
  if [[ -d "$path" && -x "$path" ]]; then
    find "$path" -mindepth 1 -maxdepth 1 -printf '%y\t%s\t%T@\t%f\0' 2>"$errors" | sort -z | awk -v 'RS=\0' -v 'ORS=\0' -v start="$offset" 'NR > start && NR <= start+201' | base64
  elif [[ -f "$path" && -z "$relative" ]]; then
    if [[ "$offset" == 0 ]]; then find "$path" -maxdepth 0 -printf '%y\t%s\t%T@\t%f\0' 2>"$errors" | base64; fi
  else exit 4
  fi
  printf '\nFILES_END\n'
  [[ ! -s "$errors" ]] || printf 'WARNING\t部分目录项无法读取\n'
else exit 2
fi
verify || exit 3
printf 'FILES_DONE\n'
