#!/usr/bin/env bash
# Inspect only the JAR actually named in the live process command line.
set -uo pipefail
export LC_ALL=C
pid=$1; expected=$2
ticks() { sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}'; }
[[ "$pid" =~ ^[1-9][0-9]*$ && -n "$expected" && "$(ticks)" == "$expected" ]] || exit 3
mapfile -d '' -t argv < "/proc/$pid/cmdline" || exit 4
archive=''
for ((i=0; i<${#argv[@]}; i++)); do
  if [[ ${argv[i]} == -jar && $((i+1)) -lt ${#argv[@]} ]]; then archive=${argv[i+1]}; break; fi
done
if [[ -z "$archive" || "${archive,,}" != *.jar ]]; then printf 'FILES_DONE\n'; exit 0; fi
if [[ "$archive" != /* ]]; then archive="$(readlink -f -- "/proc/$pid/cwd")/$archive"; fi
archive=$(readlink -f -- "$archive")
if [[ ! -f "$archive" || ! -r "$archive" ]]; then printf 'WARNING\tJAR 文件不存在或不可读\nFILES_DONE\n'; exit 0; fi
java=$(readlink -f -- "/proc/$pid/exe")
jar="${java%/*}/jar"
if [[ ! -x "$jar" ]]; then jar=$(command -v jar || true); fi
if [[ -z "$jar" || ! -x "$jar" ]]; then printf 'WARNING\t目标服务器没有可用的 jar 命令，无法读取包内配置\nFILES_DONE\n'; exit 0; fi
tmp=$(mktemp -d) || exit 5
trap 'rm -rf -- "$tmp"' EXIT
if ! (ulimit -f 4096; timeout 20 "$jar" tf "$archive" > "$tmp/list" 2>/dev/null); then
  printf 'WARNING\tJAR 内容列表读取失败或超过限制\nFILES_DONE\n'; exit 0
fi
count=0; total=0
while IFS= read -r member; do
  # Restrict extraction to ordinary classpath configuration paths; never extract nested JARs.
  [[ "$member" =~ ^(BOOT-INF/classes/|classes/)?[A-Za-z0-9_./-]+\.(yml|yaml|properties|xml|conf)$ ]] || continue
  [[ "/$member/" != *'/../'* && "/$member/" != *'/./'* && "$member" != /* ]] || continue
  if [[ $(grep -Fxc -- "$member" "$tmp/list") != 1 ]]; then printf 'WARNING\tJAR 包含重复配置路径，已跳过\n'; continue; fi
  count=$((count+1))
  if ((count > 100)); then printf 'WARNING\tJAR 配置超过 100 项，已停止读取\n'; break; fi
  mkdir -p "$tmp/extract"
  if ! (cd "$tmp/extract" && ulimit -f 4096 && timeout 10 "$jar" xf "$archive" "$member" >/dev/null 2>&1); then
    printf 'WARNING\tJAR 内配置读取失败或超过 2 MiB 限制\n'; rm -rf -- "$tmp/extract"; continue
  fi
  file="$tmp/extract/$member"
  if [[ -f "$file" && ! -L "$file" && $(stat -c %s -- "$file") -le 2097152 ]]; then
    size=$(stat -c %s -- "$file")
    if ((total + size > 16777216)); then printf 'WARNING\tJAR 配置超过 16 MiB 总量限制\n'; break; fi
    total=$((total+size))
    printf 'JAR_CONFIG\t%s\t%s\t%s\n' "$(printf '%s' "$archive" | base64 | tr -d '\n')" "$(printf '%s' "$member" | base64 | tr -d '\n')" "$(base64 < "$file" | tr -d '\n')"
  else
    printf 'WARNING\tJAR 内配置不可读或超过 2 MiB 限制\n'
  fi
  rm -rf -- "$tmp/extract"
done < "$tmp/list"
[[ "$(ticks)" == "$expected" ]] || exit 3
printf 'FILES_DONE\n'
