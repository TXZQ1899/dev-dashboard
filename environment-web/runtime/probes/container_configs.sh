#!/usr/bin/env bash
# Read Tomcat configuration through the process root, keeping paths container-relative.
set -uo pipefail
export LC_ALL=C
pid=$1; expected=$2; mode=$3; shift 3
ticks() { sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}'; }
[[ "$pid" =~ ^[1-9][0-9]*$ && -n "$expected" && "$(ticks)" == "$expected" ]] || exit 3
prefix=/proc/$pid/root
host=$(stat -L -c '%d:%i' / 2>/dev/null) || exit 4
root_id=$(stat -L -c '%d:%i' "$prefix" 2>/dev/null) || exit 4
if [[ "$mode" == detect ]]; then
  if [[ "$host" == "$root_id" ]]; then printf 'HOST_ROOT\n'; else printf 'CONTAINER_ROOT\n'; fi
else
  [[ "$host" != "$root_id" ]] || exit 4
  safe_path() {
    local path=$1 part current=$prefix
    [[ "$path" == /* && "$path" != / && "$path" != *//* ]] || return 1
    IFS=/ read -r -a parts <<< "${path#/}"
    for part in "${parts[@]}"; do
      [[ -n "$part" && "$part" != . && "$part" != .. ]] || return 1
      current=$current/$part
      [[ ! -L "$current" ]] || return 1
    done
  }
  b64() { base64 | tr -d '\n'; }
  if [[ "$mode" == discover-webapps ]]; then
    count=0
    for appbase in "$@"; do
      if ! safe_path "$appbase"; then printf 'WARNING\t容器应用目录包含符号链接或无效路径，未采集\n'; continue; fi
      [[ -d "$prefix$appbase" ]] || continue
      if [[ ! -r "$prefix$appbase" || ! -x "$prefix$appbase" ]]; then
        printf 'WARNING\t容器应用目录不可读\n'; continue
      fi
      for app in "$prefix$appbase"/*; do
        if [[ -L "$app" ]]; then printf 'WARNING\t容器展开应用目录是符号链接，未采集\n'; continue; fi
        [[ -d "$app" ]] || continue
        for name in WEB-INF config; do
          logical=${app#"$prefix"}/$name
          if ! safe_path "$logical"; then printf 'WARNING\t容器应用配置目录包含符号链接，未采集\n'; continue; fi
          [[ -d "$prefix$logical" ]] || continue
          count=$((count+1))
          if ((count > 128)); then printf 'WARNING\t容器展开应用配置目录超过 128 项，未继续发现\n'; break 3; fi
          printf 'APP_ROOT\t%s\n' "$(printf '%s' "$logical" | b64)"
        done
      done
    done
  elif [[ "$mode" == list ]]; then
    kind=$1; shift
    [[ "$kind" == config ]] || exit 2
    for ((i=0; i<${#config_prune[@]}; i++)); do
      if [[ "${config_prune[i]}" == -path ]]; then ((i+=1)); config_prune[i]=$prefix${config_prune[i]}; fi
    done
    for root in "$@"; do
      if ! safe_path "$root"; then
        printf 'WARNING\t容器配置目录包含符号链接或无效路径，未采集\n'; continue
      fi
      if [[ ! -d "$prefix$root" ]]; then continue; fi
      if [[ ! -r "$prefix$root" || ! -x "$prefix$root" ]]; then
        printf 'WARNING\t容器配置目录不可读\n'; continue
      fi
      errors=$(mktemp) || exit 2
      encoded=$(printf '%s' "$root" | b64)
      printf 'ROOT\tconfig\t%s\nFILE_BATCH_BEGIN\tconfig\t%s\n' "$encoded" "$encoded"
      while IFS= read -r -d '' file; do
        logical=${file#"$prefix"}
        safe_path "$logical" || continue
        stat --printf='%Y\t%s\n' -- "$file" 2>/dev/null | while IFS=$'\t' read -r modified size; do
          printf '%s\t%s\t%s\0' "$modified" "$size" "$logical"
        done
      done < <(find "$prefix$root" -maxdepth 6 "${config_prune[@]}" -type f \( -name '*.xml' -o -name '*.properties' -o -name '*.yml' -o -name '*.yaml' -o -name '*.conf' \) -print0 2>"$errors") | sort -z -nr | awk -v 'RS=\0' -v 'ORS=\0' 'NR <= 1001' | base64
      printf '\nFILE_BATCH_END\n'
      [[ ! -s "$errors" ]] || printf 'WARNING\t容器中部分配置目录无法完整列出\n'
      rm -f -- "$errors"
    done
  elif [[ "$mode" == snapshot || "$mode" == config ]]; then
    if [[ "$mode" == config ]]; then set -- preview "$1" "$2"; fi
    while (($# >= 3)); do
      id=$1; file=$2; root=$3; shift 3
      [[ "$id" == preview || "$id" =~ ^[0-9a-f]{32}$ ]] || exit 5
      if ! safe_path "$root" || ! safe_path "$file" || [[ "$file" != "$root/"* || ! -f "$prefix$file" || ! -r "$prefix$file" ]]; then
        if [[ "$id" == preview ]]; then exit 4; fi
        printf 'SNAPSHOT_ERROR\t%s\n' "$id"; continue
      fi
      if [[ "$id" == preview ]]; then
        printf 'CONTENT_BEGIN\n'; head -c 262144 -- "$prefix$file" | base64; printf '\nCONTENT_END\n'
      else
        data=$(head -c 2097153 -- "$prefix$file" | b64) || exit 5
        printf 'SNAPSHOT\t%s\t%s\n' "$id" "$data"
      fi
    done
    (($# == 0)) || exit 5
  else exit 2
  fi
fi
[[ "$(ticks)" == "$expected" ]] || exit 3
printf 'FILES_DONE\n'
