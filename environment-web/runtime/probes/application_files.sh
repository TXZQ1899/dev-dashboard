#!/usr/bin/env bash
# Bounded, read-only application file access; paths come from the server catalogue.
set -uo pipefail
export LC_ALL=C
pid=$1; expected=$2; mode=$3; shift 3
ticks() { sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}'; }
[[ "$pid" =~ ^[1-9][0-9]*$ && -n "$expected" && "$(ticks)" == "$expected" ]] || exit 3
b64() { base64 | tr -d '\n'; }
if [[ "$mode" == list || "$mode" == list-match ]]; then
  errors=$(mktemp) || exit 2
  trap 'rm -f -- "$errors"' EXIT
  kind=$1; shift
  pattern='*'; depth=6
  if [[ "$mode" == list-match ]]; then pattern=$1; shift; depth=1; fi
  for input in "$@"; do
    root=$(readlink -f -- "$input")
    [[ -d "$root" && "$root" == /* && "$root" != / ]] || continue
    encoded_root=$(printf '%s' "$root" | b64)
    printf 'ROOT\t%s\t%s\n' "$kind" "$encoded_root"
    filter=(-type f -name "$pattern")
    if [[ "$kind" == config ]]; then
      filter=("${config_prune[@]}" "${filter[@]}" \( -name '*.xml' -o -name '*.properties' -o -name '*.yml' -o -name '*.yaml' -o -name '*.conf' \))
    fi
    groups=(config)
    if [[ "$kind" == log ]]; then groups=(current history); fi
    for group in "${groups[@]}"; do
      if [[ "$kind" == log ]]; then
        log_filter "$group" "$pattern"
        filter=("${log_prune[@]}" -type f -name "$pattern" "${log_names[@]}")
        if [[ "$group" == current ]]; then filter+=("${log_text_test[@]}"); fi
      fi
      printf 'FILE_BATCH_BEGIN\t%s\t%s\n' "$kind" "$encoded_root"
      find "$root" -regextype posix-extended -maxdepth "$depth" "${filter[@]}" -printf '%T@\t%s\t%p\0' 2>"$errors" | sort -z -nr | awk -v 'RS=\0' -v 'ORS=\0' 'NR <= 1001' | base64
      printf '\nFILE_BATCH_END\n'
      [[ ! -s "$errors" ]] || printf 'WARNING\t部分目录无法完整列出，请检查读取权限\n'
    done
  done
elif [[ "$mode" == config || "$mode" == log ]]; then
  file=$1; root=$2
  [[ "$(readlink -f -- "$file")" == "$file" && "$file" == "$root/"* && -f "$file" && -r "$file" ]] || exit 4
  if [[ "$mode" == log ]]; then
    [[ "$file" =~ ^$current_log_regex$ ]] || exit 4
    bash -c '[[ ! -s "$1" ]] || head -c 4096 -- "$1" | grep -Iq ""' _ "$file" || exit 4
  fi
  printf 'CONTENT_BEGIN\n'
  if [[ "$mode" == config ]]; then
    head -c 262144 -- "$file" | base64
    rc=$?
  else
    tail -n 100 -- "$file" | tail -c 262144 | base64
    rc=$?
  fi
  printf '\nCONTENT_END\n'
  ((rc == 0)) || exit 5
else
  exit 2
fi
[[ "$(ticks)" == "$expected" ]] || exit 3
printf 'FILES_DONE\n'
