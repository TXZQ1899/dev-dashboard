
#!/usr/bin/env bash

# Usage:
#   bash check_app_traffic.sh PID PORT [SECONDS]
#
# Example:
#   bash check_app_traffic.sh 4275 8036 300

set -u

PID="${1:-4275}"
PORT="${2:-8036}"
DURATION="${3:-300}"

if [[ ! "$PID" =~ ^[0-9]+$ ||
      ! "$PORT" =~ ^[0-9]+$ ||
      ! "$DURATION" =~ ^[1-9][0-9]*$ ]] ||
   (( 10#$PORT < 1 || 10#$PORT > 65535 )); then
    echo "ERROR: Invalid arguments"
    exit 1
fi

for cmd in ss tcpdump timeout awk tee; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "ERROR: Missing command: $cmd"
        exit 1
    fi
done

if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: Please run as root"
    exit 1
fi

if [[ ! -d "/proc/$PID" ]]; then
    echo "ERROR: PID $PID does not exist"
    exit 1
fi

echo "========================================"
echo "Application Traffic Inspection"
echo "========================================"
echo "PID      : $PID"
echo "PORT     : $PORT"
echo "Duration : $DURATION seconds"
echo "Time     : $(date '+%F %T')"

echo
echo "[1] Checking process"
ps -p "$PID" -o pid,etime,comm,args --no-headers |
    cut -c 1-220

echo
echo "[2] Checking listening socket"

LISTEN=$(ss -H -lntp "( sport = :$PORT )")

echo "$LISTEN"

if [[ -z "$LISTEN" ]] ||
   ! grep -Fq "pid=$PID," <<< "$LISTEN"; then
    echo "ERROR: Port is not owned by PID $PID"
    exit 1
fi

echo
echo "[3] Current established connections"
ss -H -tn state established "( sport = :$PORT )"

echo
echo "[4] Capturing inbound TCP packets..."
echo "Monitoring for $DURATION seconds..."
echo "Packets will be displayed in real time."
echo "========================================"

TMPDIR=$(mktemp -d) || exit 1
trap 'rm -rf "$TMPDIR"' EXIT

# Capture packets, display them and save to file.
# -l: line-buffered output
# -tttt: readable packet timestamps
# -nn: do not resolve hostnames or ports
# -q: concise TCP packet information
# -s 96: limit captured bytes per packet

timeout -s INT "$DURATION" \
    tcpdump -i any -nn -tttt -q -l -s 96 \
    "tcp dst port $PORT" \
    2>"$TMPDIR/tcpdump.err" |
    tee "$TMPDIR/traffic.log"

# Capture the exit status of timeout/tcpdump.
RC=${PIPESTATUS[0]}

if [[ "$RC" -ne 0 && "$RC" -ne 124 ]]; then
    echo
    echo "ERROR: tcpdump failed (exit=$RC)"
    cat "$TMPDIR/tcpdump.err"
    exit 1
fi

echo
echo "========================================"
echo "[5] Analyzing captured traffic"
echo "========================================"

awk '
{
    # Ignore lines that are not packet records
    if ($0 !~ / IP6? /) next

    packets++

    # tcpdump -q outputs TCP payload as "tcp N"
    if (match($0, /tcp [0-9]+/)) {
        field = substr($0, RSTART, RLENGTH)
        sub(/^tcp /, "", field)
        bytes = field + 0

        if (bytes > 0) {
            data_packets++
            data_bytes += bytes

            # Extract source IP from packet output
            for (i = 1; i <= NF; i++) {
                if ($i == ">" && i > 1) {
                    source = $(i-1)

                    # Remove trailing source port
                    sub(/\.[0-9]+$/, "", source)

                    sources[source]++
                    source_bytes[source] += bytes
                    break
                }
            }
        }
    }
}
END {
    printf "Total inbound packets : %d\n", packets
    printf "TCP data packets      : %d\n", data_packets
    printf "TCP payload bytes     : %.0f\n", data_bytes

    print ""
    print "Source IPs (packets with data):"
    printf "  %-40s %10s %12s\n", \
           "SOURCE IP", "PACKETS", "BYTES"

    for (ip in sources) {
        printf "  %-40s %10d %12.0f\n", \
               ip, sources[ip], source_bytes[ip]
    }

    print ""
    if (data_packets > 0) {
        print "RESULT: INBOUND_TCP_DATA_DETECTED"
        print "Inbound TCP payload was detected."
        print "Check whether it is business traffic."
    } else {
        print "RESULT: NO_INBOUND_TCP_DATA_OBSERVED"
        print "No inbound TCP payload observed."
        print "This does NOT prove the app is unused."
    }
}
' "$TMPDIR/traffic.log"

echo
echo "========================================"
echo "Inspection finished: $(date '+%F %T')"
echo "========================================"
