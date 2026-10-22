#!/usr/bin/env bash
# Executed on the inspected Linux asset through JumpServer, never on the web host.
set -uo pipefail
export LC_ALL=C TZ=UTC
mode=$1
selector=$2
tmp=$(mktemp -d) || exit 2
trap 'rm -rf -- "$tmp"' EXIT
b64() { base64 | tr -d '\n'; }
# Bound every external read: a stale cwd/exe mount must not stall the sample.
bounded() { local limit=$1; shift; timeout -s KILL -k 1 "$limit" "$@"; }
command -v ss >/dev/null && command -v timeout >/dev/null || exit 6
ticks() { bounded 2 sed 's/^.*) //' "/proc/$1/stat" 2>/dev/null | awk '{print $20}'; }
find_sockets() { bounded 5 find "/proc/$1/fd" -maxdepth 1 -lname 'socket:*' -printf '%l\n' 2>/dev/null; }
ps_row() {
  # One ps snapshot replaces a ps process per PID when several processes are
  # involved. The targeted form is kept for one or two PIDs so the full
  # process table is never enumerated for a single application.
  if ((pid_count <= 2)); then
    bounded 5 ps -p "$1" -ww -o "$PS_FIELDS" 2>/dev/null | b64
    return
  fi
  if [[ ! -s "$tmp/ps" ]]; then
    # Older procps on CentOS 7 mis-parses `ps -eo <field=,...>` (it returns
    # only the PID column); `ps ax -o` is equivalent and works there.
    bounded 5 ps ax -o "$PS_FIELDS" >"$tmp/ps.new" 2>/dev/null && mv "$tmp/ps.new" "$tmp/ps" ||
      rm -f "$tmp/ps.new"
  fi
  awk -v p="$1" '$1==p' "$tmp/ps" 2>/dev/null | b64
}
clock() { date -u +%s.%N; }
socket_output() {
  # Keep endpoint/state/identity/owners and the two counters consumed by the
  # report. cwnd/rtt/skmem/etc. can otherwise dominate the PTY transfer.
  awk '/^[^ \t]/{print;next} {c="";for(i=1;i<=NF;i++) if($i ~ /^bytes_(acked|received):[0-9]+$/) c=c " " $i;if(c!="") print c}' "$1"
}
# The console moves text at a low rate, and repeated socket rows and process
# commands dominate a batch. Each completed section is compressed before it
# reaches the PTY; the client restores the exact former text and parses it
# unchanged. When gzip/base64 is unavailable the section is sent verbatim.
compressed=0
command -v gzip >/dev/null && command -v base64 >/dev/null && compressed=1
emit_blob() {
  if ((compressed)); then
    encoded=$(gzip -9 -c "$1" 2>/dev/null | base64 2>/dev/null | tr -d '\n' | fold -w 512)
    if [[ -n "$encoded" ]]; then
      printf 'CHECK_BLOB_BEGIN\t%s\n%s\nCHECK_BLOB_END\n' "$2" "$encoded"
      return
    fi
  fi
  cat "$1"
}
count=${6:-1}
period=${7:-0.2}
listen_filter=${5:-}
[[ "$count" =~ ^[1-5]$ && "$period" == 0.2 ]] || exit 2
# Metadata is reused while the PID start counter and network namespace are
# unchanged, within this short batch. Each frame still proves the identity.
declare -A process_ticks=([seed]=0) process_namespaces=([seed]='') process_versions=([seed]=0) process_catalog=([seed]='')
next_version=0
PS_FIELDS='pid=,ppid=,user:32=,etimes=,lstart=,comm=,%cpu=,%mem=,rss=,vsz=,args='
# Bash 4.2 treats an empty array as unset under `set -u`. Keep this command
# nonempty even when the inspected process shares our network namespace.
ss_command=(ss)
namespace_pid=${3:-}
expected_ticks=${4:-}
if [[ "$mode" == target ]]; then namespace_pid=$selector; fi
if [[ -n "$namespace_pid" ]]; then
  [[ "$namespace_pid" =~ ^[1-9][0-9]*$ && -d "/proc/$namespace_pid" ]] || exit 3
  before=$(ticks "$namespace_pid")
  [[ -n "$before" ]] || exit 4
  [[ -z "$expected_ticks" || "$before" == "$expected_ticks" ]] || exit 3
  # Network namespace is distinct from the PID namespace; preserve host PIDs.
  if [[ "$(bounded 2 readlink "/proc/$namespace_pid/ns/net")" != "$(bounded 2 readlink /proc/self/ns/net)" ]]; then
    command -v nsenter >/dev/null || exit 5
    ss_command=(nsenter -t "$namespace_pid" -n -- ss)
  fi
fi
command -v ss >/dev/null && command -v timeout >/dev/null || exit 6
printf 'CHECK_CLOCK_BEGIN\t%s\n' "$(clock)"
# Sampling and transfer have separate budgets. Metadata reads and PTY output
# after a capture must not consume the sampling budget, otherwise a host with
# a slow post-capture phase silently loses frames (and its observation window).
sample_budget=${8:-5}
transfer_budget=${9:-22}
[[ "$sample_budget" =~ ^[1-9][0-9]*$ && "$transfer_budget" =~ ^[1-9][0-9]*$ ]] || exit 2
transfer_deadline=$((SECONDS+transfer_budget))
capture_total=0.000
truncated_reason=
collect_sample() {
capture_begin=$(clock)
if [[ "$mode" == target ]]; then
  # ss130716 has no -H. Headers are ignored by the PID filter / parser.
  [[ -r "/proc/$selector/fd" && -x "/proc/$selector/fd" ]] || exit 7
  find_sockets "$selector" >"$tmp/fd-before" ||
    printf 'CHECK_WARNING\t部分目标文件描述符未能读取，仅关联前后均已核验的 socket\n'
  # -p scans every process on the host. The selected PID's live FD/inode
  # inventory proves ownership without that expensive, host-wide PID scan.
  # After the first frame the selected process's local ports are known, so the
  # table can be read for those ports only. A new socket always introduces a
  # new FD inode, therefore an unchanged inode set cannot hide one behind the
  # filter; any unseen inode (or an oversized port list) falls back to a full
  # scan so newly built connections are never lost to the optimization.
  ss_filter=
  if [[ -s "$tmp/local-ports" ]] && (($(wc -l <"$tmp/local-ports") <= 64)) &&
     ! sort -u "$tmp/fd-before" | comm -23 - <(sort -u "$tmp/inodes-known") | grep -q .; then
    ss_filter='( '"$(awk 'NR>1{printf " or "}{printf "sport = :"$1}' "$tmp/local-ports")"' )'
  fi
  if [[ -n "$ss_filter" ]]; then
    bounded 12 "${ss_command[@]}" -antnie "$ss_filter" >"$tmp/all" 2>"$tmp/err"
  else
    bounded 12 "${ss_command[@]}" -antnie >"$tmp/all" 2>"$tmp/err"
  fi
  rc=$?
  find_sockets "$selector" >"$tmp/fd-after" ||
    printf 'CHECK_WARNING\t部分目标文件描述符未能读取，仅关联前后均已核验的 socket\n'
  [[ -r "/proc/$selector/fd" && -x "/proc/$selector/fd" ]] || exit 7
  awk 'NR==FNR{before[$0]=1;next} before[$0]{gsub(/[^0-9]/, ""); print}' "$tmp/fd-before" "$tmp/fd-after" >"$tmp/inodes"
  # Only return the selected process and its TCP sockets, including ss -i detail.
  awk 'NR==FNR{owned[$1]=1;next} /^[^ \t]/{keep=0;for(i=1;i<=NF;i++) if($i ~ /^ino:/ && owned[substr($i,5)]) keep=1} keep' "$tmp/inodes" "$tmp/all" >"$tmp/sockets"
  awk -v pid="$selector" '/^[^ \t]/{for(i=1;i<=NF;i++) if($i ~ /^ino:[0-9]+$/) printf "CHECK_INODE_OWNER\t%s\t%s\n", substr($i,5), pid}' "$tmp/sockets"
  # Remember the verified local ports and inodes for the next frame's filter.
  # The inode set is kept in the same `socket:[N]` form as find_sockets output
  # so the next frame can compare file descriptors directly.
  awk '/^[^ \t]/{for(i=1;i<NF;i++) if($i ~ /^[0-9]+$/ && $(i+1) ~ /^[0-9]+$/){n=split($(i+2),a,":");if(a[n] ~ /^[0-9]+$/ && a[n]+0>0) print a[n]+0;break}}' \
    "$tmp/sockets" | sort -un >"$tmp/local-ports.new"
  mv "$tmp/local-ports.new" "$tmp/local-ports"
  awk 'NR==FNR{before[$0]=1;next} before[$0]{print}' "$tmp/fd-before" "$tmp/fd-after" |
    sort -u >"$tmp/inodes-known"
  pids=$selector
elif [[ "$mode" == peer || "$mode" == listeners ]]; then
  socket_options=-antpie
  if [[ "$mode" == listeners ]]; then socket_options=-lntpie; fi
  bounded 12 "${ss_command[@]}" "$socket_options" "$selector" >"$tmp/sockets" 2>"$tmp/err"
  rc=$?
  # Bound metadata enumeration and PTY transfer on the inspected host itself.
  awk -v count_file="$tmp/socket-count" '/^[^ \t]/{if($1=="State" || $1=="Netid"){keep=0;next};n++;keep=(n<=400)} keep {print} END{print n+0 > count_file}' "$tmp/sockets" >"$tmp/limited" || exit 8
  mv "$tmp/limited" "$tmp/sockets"
  if (($(cat "$tmp/socket-count") > 400)); then
    printf 'CHECK_WARNING\t对端单次采样超过 400 条连接，超出部分未继续核验\n'
  fi
  pids=$(grep -oE 'pid=[0-9]+' "$tmp/sockets" | cut -d= -f2 | sort -un | head -40)
  if (($(grep -oE 'pid=[0-9]+' "$tmp/sockets" | sort -u | wc -l) > 40)); then
    printf 'CHECK_WARNING\t对端进程超过 40 个，超出部分未继续核验 PID 身份\n'
  fi
else
  exit 2
fi
capture_end=$(clock)
printf 'CHECK_OBSERVED\t%s\t%s\n' "$capture_begin" "$capture_end"
if [[ -n "$namespace_pid" ]]; then
  printf 'CHECK_NAMESPACE\t%s\n' "$(bounded 2 readlink "/proc/$namespace_pid/ns/net")"
fi
printf 'CHECK_SOCKETS_BEGIN\n'
socket_output "$tmp/sockets"
printf '\nCHECK_SOCKETS_END\nCHECK_SS_RC\t%s\n' "$rc"
if ((rc != 0)); then
  printf 'CHECK_DIAGNOSTIC\tss\t%s\t' "$rc"
  head -c 2048 "$tmp/err" | b64
  printf '\n'
fi
[[ ! -s "$tmp/err" ]] || printf 'CHECK_WARNING\tss emitted diagnostics\n'
printf 'CHECK_UID\t%s\n' "$(id -u)"
printf 'CHECK_LISTEN_BEGIN\n'
if [[ "$mode" == listeners ]]; then
  cp "$tmp/sockets" "$tmp/listen"
  listen_rc=$rc
elif [[ -n "$listen_filter" ]]; then
  bounded 12 "${ss_command[@]}" -lntp "$listen_filter" >"$tmp/listen" 2>"$tmp/err"
  listen_rc=$?
elif [[ "$mode" == target ]]; then
  # The same enumeration already includes the selected process's listeners.
  awk '/^[^ \t]/{keep=($1=="LISTEN" || ($1 ~ /^tcp/ && $2=="LISTEN"))} keep' "$tmp/sockets" >"$tmp/listen"
  listen_rc=$rc
else
  # The listener table moves slowly compared with a one-second batch. Reusing
  # the first frame's enumeration removes the most expensive fixed cost per
  # frame without changing socket ownership evidence.
  if [[ -s "$tmp/listen-static" ]]; then
    cp "$tmp/listen-static" "$tmp/listen"
    listen_rc=$(cat "$tmp/listen-rc")
  else
    bounded 12 "${ss_command[@]}" -lntp >"$tmp/listen-static" 2>"$tmp/err"
    listen_rc=$?
    cp "$tmp/listen-static" "$tmp/listen"
    printf '%s' "$listen_rc" >"$tmp/listen-rc"
  fi
fi
if [[ "$mode" == peer && -n "$namespace_pid" ]]; then
  pids=$(grep -oE 'pid=[0-9]+' "$tmp/sockets" "$tmp/listen" | sed 's/.*pid=//' | sort -un | head -40)
fi
if [[ "$mode" == target ]]; then
  socket_output "$tmp/listen"
else
  awk -v pids="$(echo "$pids" | tr '\n' ',')" 'BEGIN{n=split(pids,ids,",")} {for(i=1;i<=n;i++) if(ids[i]!="" && index($0,"pid=" ids[i] ",")){print;break}}' "$tmp/listen"
fi
printf 'CHECK_LISTEN_END\nCHECK_LISTEN_RC\t%s\n' "$listen_rc"
if ((listen_rc != 0)); then
  printf 'CHECK_DIAGNOSTIC\tlisten\t%s\t' "$listen_rc"
  head -c 2048 "$tmp/err" | b64
  printf '\n'
fi
pid_count=$(echo "$pids" | tr ' ' '\n' | grep -c .)
for pid in $pids; do
  start=$(ticks "$pid")
  [[ -n "$start" ]] || { printf 'CHECK_WARNING\tprocess changed during inspection\n'; continue; }
  # The start counter proves the process instance. cwd/exe/ps are read only
  # for a new version; ns/net is still checked every frame because setns can
  # move a live process without changing its start counter.
  current_netns=$(bounded 2 readlink "/proc/$pid/ns/net" 2>/dev/null | b64)
  if [[ "${process_ticks[$pid]:-}" == "$start" && -n "$current_netns" &&
        "${process_namespaces[$pid]:-}" == "$current_netns" ]]; then
    printf 'CHECK_PROC_REF\t%s\t%s\t%s\n' "$pid" "$start" "${process_versions[$pid]}"
    continue
  fi
  row=$(ps_row "$pid")
  cwd=$(bounded 2 readlink "/proc/$pid/cwd" 2>/dev/null | b64)
  exe=$(bounded 2 readlink "/proc/$pid/exe" 2>/dev/null | b64)
  netns=$current_netns
  finish=$(ticks "$pid")
  if [[ -z "$start" || "$start" != "$finish" || -z "$row" || -z "$netns" ]]; then
    printf 'CHECK_WARNING\tprocess changed during inspection\n'
    continue
  fi
  process_ticks[$pid]=$start
  process_namespaces[$pid]=$netns
  next_version=$((next_version+1))
  process_versions[$pid]=$next_version
  process_catalog[$next_version]=$(printf 'CHECK_PROC\t%s\t%s\t%s\t%s\t%s\t%s' "$pid" "$start" "$row" "$cwd" "$exe" "$netns")
  printf 'CHECK_PROC_REF\t%s\t%s\t%s\n' "$pid" "$start" "$next_version"
done
if [[ -n "$namespace_pid" ]]; then
  [[ "$before" == "$(ticks "$namespace_pid")" ]] || exit 3
fi
if [[ "$mode" == target ]]; then
  [[ -r "/proc/$selector/fd" && -x "/proc/$selector/fd" ]] || printf 'CHECK_WARNING\tprocess file descriptors inaccessible\n'
fi
}
for ((sample_index=0; sample_index<count; sample_index++)); do
  if ((sample_index > 0)); then
    if ((SECONDS >= transfer_deadline)); then truncated_reason=transfer-budget; break; fi
    if [[ "$(awk -v t="$capture_total" -v b="$sample_budget" 'BEGIN{print (t>=b)?1:0}')" == 1 ]]; then
      truncated_reason=sample-budget
      break
    fi
  fi
  {
    printf 'CHECK_SAMPLE_BEGIN\n'
    collect_sample
    sample_rc=$?
    printf 'CHECK_SAMPLE_END\n'
  } >"$tmp/frame"
  ((sample_rc == 0)) || exit "$sample_rc"
  # A completed frame is sent as one compressed block so a killed probe still
  # delivers every earlier frame.
  emit_blob "$tmp/frame" "sample-$sample_index"
  capture_total=$(awk -v t="$capture_total" -v s="$capture_begin" -v e="$capture_end" \
    'BEGIN{printf "%.3f", t+(e-s)}')
  if ((sample_index+1 < count)); then
    # Schedule from capture start, rather than adding another full pause after
    # a slow ss/proc query. Slow hosts retain their measured observation gaps.
    delay=$(awk -v start="$capture_begin" -v finish="$(clock)" -v period="$period" 'BEGIN {d=period-(finish-start); printf "%.3f", (d>0?d:0)}')
    sleep "$delay" || exit 2
  fi
done
if [[ -n "$truncated_reason" ]]; then
  # Explicit truncation keeps a slow post-capture phase or transfer budget
  # distinguishable from a probe timeout in the report.
  printf 'CHECK_TRUNCATED\t%s\t%s\t%s\n' "$truncated_reason" "$sample_index" "$count"
fi
# Long Java commands can block a PTY writer. Send each validated identity only
# once, after socket sampling, rather than stalling every capture with it.
: >"$tmp/catalog"
for ((catalog_index=1; catalog_index<=next_version; catalog_index++)); do
  printf 'CHECK_PROC_VERSION\t%s\t%s\n' "$catalog_index" "${process_catalog[$catalog_index]}" >>"$tmp/catalog"
done
emit_blob "$tmp/catalog" catalog
printf 'CHECK_CLOCK_END\t%s\n' "$(clock)"
