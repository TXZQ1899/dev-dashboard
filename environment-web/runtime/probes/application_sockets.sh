#!/usr/bin/env bash
# Executed on the inspected Linux asset through JumpServer, never on the web host.
set -uo pipefail
export LC_ALL=C TZ=UTC
mode=$1
selector=$2
tmp=$(mktemp -d) || exit 2
trap 'rm -rf -- "$tmp"' EXIT
b64() { base64 | tr -d '\n'; }
ticks() { sed 's/^.*) //' "/proc/$1/stat" 2>/dev/null | awk '{print $20}'; }
clock() { date -u +%s.%N; }
socket_output() {
  # Keep endpoint/state/identity/owners and the two counters consumed by the
  # report. cwnd/rtt/skmem/etc. can otherwise dominate the PTY transfer.
  awk '/^[^ \t]/{print;next} {c="";for(i=1;i<=NF;i++) if($i ~ /^bytes_(acked|received):[0-9]+$/) c=c " " $i;if(c!="") print c}' "$1"
}
count=${6:-1}
period=${7:-0.2}
listen_filter=${5:-}
[[ "$count" =~ ^[1-5]$ && "$period" == 0.2 ]] || exit 2
# Metadata is reused only while the PID start counter is unchanged, within this
# short batch. Each frame still proves the process identity independently.
declare -A process_ticks=([seed]=0) process_exes=([seed]='') process_namespaces=([seed]='') process_versions=([seed]=0) process_catalog=([seed]='')
next_version=0
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
  if [[ "$(readlink "/proc/$namespace_pid/ns/net")" != "$(readlink /proc/self/ns/net)" ]]; then
    command -v nsenter >/dev/null || exit 5
    ss_command=(nsenter -t "$namespace_pid" -n -- ss)
  fi
fi
command -v ss >/dev/null && command -v timeout >/dev/null || exit 6
printf 'CHECK_CLOCK_BEGIN\t%s\n' "$(clock)"
batch_deadline=$((SECONDS+5))
collect_sample() {
capture_begin=$(clock)
if [[ "$mode" == target ]]; then
  # ss130716 has no -H. Headers are ignored by the PID filter / parser.
  [[ -r "/proc/$selector/fd" && -x "/proc/$selector/fd" ]] || exit 7
  find "/proc/$selector/fd" -maxdepth 1 -lname 'socket:*' -printf '%l\n' 2>/dev/null >"$tmp/fd-before" ||
    printf 'CHECK_WARNING\t部分目标文件描述符未能读取，仅关联前后均已核验的 socket\n'
  # -p scans every process on the host. The selected PID's live FD/inode
  # inventory proves ownership without that expensive, host-wide PID scan.
  timeout 12 "${ss_command[@]}" -antnie >"$tmp/all" 2>"$tmp/err"
  rc=$?
  find "/proc/$selector/fd" -maxdepth 1 -lname 'socket:*' -printf '%l\n' 2>/dev/null >"$tmp/fd-after" ||
    printf 'CHECK_WARNING\t部分目标文件描述符未能读取，仅关联前后均已核验的 socket\n'
  [[ -r "/proc/$selector/fd" && -x "/proc/$selector/fd" ]] || exit 7
  awk 'NR==FNR{before[$0]=1;next} before[$0]{gsub(/[^0-9]/, ""); print}' "$tmp/fd-before" "$tmp/fd-after" >"$tmp/inodes"
  # Only return the selected process and its TCP sockets, including ss -i detail.
  awk 'NR==FNR{owned[$1]=1;next} /^[^ \t]/{keep=0;for(i=1;i<=NF;i++) if($i ~ /^ino:/ && owned[substr($i,5)]) keep=1} keep' "$tmp/inodes" "$tmp/all" >"$tmp/sockets"
  awk -v pid="$selector" '/^[^ \t]/{for(i=1;i<=NF;i++) if($i ~ /^ino:[0-9]+$/) printf "CHECK_INODE_OWNER\t%s\t%s\n", substr($i,5), pid}' "$tmp/sockets"
  pids=$selector
elif [[ "$mode" == peer || "$mode" == listeners ]]; then
  socket_options=-antpie
  if [[ "$mode" == listeners ]]; then socket_options=-lntpie; fi
  timeout 12 "${ss_command[@]}" "$socket_options" "$selector" >"$tmp/sockets" 2>"$tmp/err"
  rc=$?
  # Bound metadata enumeration and PTY transfer on the inspected host itself.
  awk -v count_file="$tmp/socket-count" '/^[^ \t]/{if($1=="State" || $1=="Netid"){keep=0;next};n++;keep=(n<=400)} keep {print} END{print n+0 > count_file}' "$tmp/sockets" >"$tmp/limited" || exit 8
  mv "$tmp/limited" "$tmp/sockets"
  if (($(cat "$tmp/socket-count") > 400)); then
    printf 'CHECK_WARNING\t对端单次采样超过 400 条连接，超出部分未继续核验\n'
  fi
  pids=$(grep -oE 'pid=[0-9]+' "$tmp/sockets" | cut -d= -f2 | sort -un | head -40)
  if [[ "$mode" == listeners ]] && (($(grep -oE 'pid=[0-9]+' "$tmp/sockets" | sort -u | wc -l) > 40)); then
    printf 'CHECK_WARNING\t监听进程超过 40 个，超出部分未继续核验\n'
  fi
else
  exit 2
fi
capture_end=$(clock)
printf 'CHECK_OBSERVED\t%s\t%s\n' "$capture_begin" "$capture_end"
if [[ -n "$namespace_pid" ]]; then
  printf 'CHECK_NAMESPACE\t%s\n' "$(readlink "/proc/$namespace_pid/ns/net")"
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
elif [[ -n "$listen_filter" ]]; then
  timeout 12 "${ss_command[@]}" -lntp "$listen_filter" >"$tmp/listen" 2>"$tmp/err"
else
  if [[ "$mode" == target ]]; then
    # The same enumeration already includes the selected process's listeners.
    awk '/^[^ \t]/{keep=($1=="LISTEN" || ($1 ~ /^tcp/ && $2=="LISTEN"))} keep' "$tmp/sockets" >"$tmp/listen"
  else
    timeout 12 "${ss_command[@]}" -lntp >"$tmp/listen" 2>"$tmp/err"
  fi
fi
listen_rc=$?
if [[ "$mode" == target || "$mode" == listeners ]]; then listen_rc=$rc; fi
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
for pid in $pids; do
  start=$(ticks "$pid")
  current_exe=$(readlink "/proc/$pid/exe" 2>/dev/null | b64)
  current_netns=$(readlink "/proc/$pid/ns/net" 2>/dev/null | b64)
  if [[ -n "$start" && "${process_ticks[$pid]:-}" == "$start" && "${process_exes[$pid]:-}" == "$current_exe" && "${process_namespaces[$pid]:-}" == "$current_netns" ]]; then
    printf 'CHECK_PROC_REF\t%s\t%s\t%s\n' "$pid" "$start" "${process_versions[$pid]}"
    continue
  fi
  row=$(ps -p "$pid" -ww -o pid=,ppid=,user:32=,etimes=,lstart=,comm=,%cpu=,%mem=,rss=,vsz=,args= 2>/dev/null | b64)
  cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null | b64)
  exe=$current_exe
  netns=$current_netns
  finish=$(ticks "$pid")
  if [[ -z "$start" || "$start" != "$finish" || -z "$row" || -z "$netns" ]]; then
    printf 'CHECK_WARNING\tprocess changed during inspection\n'
    continue
  fi
  process_ticks[$pid]=$start
  process_exes[$pid]=$exe
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
  if ((sample_index > 0 && SECONDS >= batch_deadline)); then break; fi
  printf 'CHECK_SAMPLE_BEGIN\n'
  collect_sample
  sample_rc=$?
  printf 'CHECK_SAMPLE_END\n'
  ((sample_rc == 0)) || exit "$sample_rc"
  if ((sample_index+1 < count)); then
    # Schedule from capture start, rather than adding another full pause after
    # a slow ss/proc query. Slow hosts retain their measured observation gaps.
    delay=$(awk -v start="$capture_begin" -v finish="$(clock)" -v period="$period" 'BEGIN {d=period-(finish-start); printf "%.3f", (d>0?d:0)}')
    sleep "$delay" || exit 2
  fi
done
# Long Java commands can block a PTY writer. Send each validated identity only
# once, after socket sampling, rather than stalling every capture with it.
for ((catalog_index=1; catalog_index<=next_version; catalog_index++)); do
  printf 'CHECK_PROC_VERSION\t%s\t%s\n' "$catalog_index" "${process_catalog[$catalog_index]}"
done
printf 'CHECK_CLOCK_END\t%s\n' "$(clock)"
