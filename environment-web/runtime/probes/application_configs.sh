#!/usr/bin/env bash
# Bounded reads of process-derived application directories. Raw contents are
# transient input to the container parser and are never persisted in reports.
set -uo pipefail
export LC_ALL=C
count=0
total=0
for root in "$@"; do
  [[ "$root" == /* && "$root" != / && -d "$root" ]] || continue
  while IFS= read -r -d '' file; do
    count=$((count+1))
    if ((count>20)); then printf 'CONFIG_WARNING\tfile limit reached\n'; exit 0; fi
    size=$(stat -c %s -- "$file" 2>/dev/null) || { printf 'CONFIG_WARNING\tstat failed\n'; continue; }
    if ((size>131072 || total+size>1048576)); then printf 'CONFIG_WARNING\tconfiguration size limit reached\n'; continue; fi
    if [[ ! -r "$file" ]]; then printf 'CONFIG_WARNING\tconfiguration unreadable\n'; continue; fi
    total=$((total+size))
    printf 'CONFIG_FILE\t'
    printf '%s' "$file" | base64 | tr -d '\n'
    printf '\t'
    head -c 131072 -- "$file" | base64 | tr -d '\n'
    printf '\n'
  done < <(find "$root" -maxdepth 4 "${config_prune[@]}" -type f \( -name '*.properties' -o -name '*.yml' -o -name '*.yaml' -o -name '*.xml' \) -print0 2>/dev/null)
done
printf 'CONFIG_DONE\n'
