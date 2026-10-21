#!/usr/bin/env bash
# Create a private temporary archive; caller transfers it in bounded chunks and removes it.
set -uo pipefail
export LC_ALL=C
pid=$1; expected=$2; cutoff=$3; group=$4; shift 4
[[ "$group" == all || "$group" == history ]] || exit 2
ticks() { sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}'; }
[[ "$pid" =~ ^[1-9][0-9]*$ && "$(ticks)" == "$expected" ]] || exit 3
tmp=$(mktemp -d /tmp/envscope-logs.XXXXXXXXXX) || exit 2
keep=0
trap '((keep == 1)) || rm -rf -- "$tmp"' EXIT
chmod 700 "$tmp"
count=0; total=0
: > "$tmp/files"
while (($# >= 2)); do
  root=$1; pattern=$2; shift 2
  [[ "$(readlink -f -- "$root")" == "$root" && -d "$root" && "$root" != / ]] || exit 4
  depth=6
  if [[ "$pattern" != '*' ]]; then depth=1; fi
  log_filter "$group" "$pattern"
  find "$root" -regextype posix-extended -maxdepth "$depth" "${log_prune[@]}" -type f -name "$pattern" "${log_names[@]}" \
    \( ! -regex "$current_log_regex" -o "${log_text_test[@]}" \) \
    -newermt "@$cutoff" -printf '%p\0' >> "$tmp/files" 2>"$tmp/find-error"
  [[ ! -s "$tmp/find-error" ]] || exit 4
done
sort -zu "$tmp/files" -o "$tmp/files" || exit 4
: > "$tmp/relative"
while IFS= read -r -d '' file; do
  [[ -r "$file" ]] || exit 4
  size=$(stat -c %s -- "$file") || exit 4
  count=$((count+1)); total=$((total+size))
  (( count <= 10000 && total <= 536870912 )) || exit 7
  printf '%s\0' "${file#/}" >> "$tmp/relative"
done < "$tmp/files"
((count > 0)) || exit 8
# --null makes -T names literal, including spaces, newlines and leading dashes.
timeout 120 tar -czf "$tmp/logs.tar.gz" -C / --null -T "$tmp/relative" 2>"$tmp/tar-error"
rc=$?
# tar exits 1 when a live log changes while being read. Keep the valid archive, flag it.
((rc == 0 || rc == 1)) || exit 5
[[ "$(ticks)" == "$expected" ]] || exit 3
printf 'ARCHIVE\t%s\t%s\t%s\t%s\t%s\n' "$tmp/logs.tar.gz" "$(stat -c %s "$tmp/logs.tar.gz")" "$(sha256sum "$tmp/logs.tar.gz" | cut -d' ' -f1)" "$count" "$rc"
keep=1
