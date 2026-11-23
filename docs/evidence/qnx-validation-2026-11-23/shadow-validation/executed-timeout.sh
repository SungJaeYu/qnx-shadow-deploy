#!/bin/sh
# Observe SEND and REPLY blocking with the existing binaries and a 250 ms fault.
set -eu
[ "$(uname -s)" = QNX ] || exit 2
cd /system/bin/shadowdeploy
out=/data/shadow-validation
for kind in send reply; do
    log="$out/timeout-$kind.jsonl"
    [ ! -e "$log" ] || { echo "Refusing to overwrite $log" >&2; exit 2; }
    scenario=promote
    [ "$kind" != reply ] || scenario=reject-delay
    ./shadow-demo --worker ./shadow-worker --scenario "$scenario" --samples 4 \
        --period-ms 1000 --deadline-ms 150 --qualify 3 --probation 8 \
        > "$log" 2> "$out/timeout-$kind.stderr" &
    broker=$!
    trap 'kill -TERM "$broker" 2>/dev/null || :; wait "$broker" || :' EXIT
    tries=0
    until grep -q '"sequence":1,' "$log"; do
        tries=$((tries + 1))
        [ "$tries" -lt 100 ] || exit 1
        sleep 0.01
    done
    candidate=$(head -n 1 "$log" | sed 's/.*"candidate_pid":\([0-9]*\).*/\1/')
    if [ "$kind" = send ]; then
        kill -STOP "$candidate"
        pidin -p "$candidate" > "$out/timeout-send-stopped.threads"
    fi
    # The second acquisition is one second after the first. Inspect its wait.
    sleep 1.02
    pidin -p "$broker" > "$out/timeout-$kind.threads"
    if wait "$broker"; then code=0; else code=$?; fi
    trap - EXIT
    echo "$kind $code" >> "$out/timeout-exits.txt"
    [ "$code" -eq 0 ] || exit 1
done
pidin ar > "$out/timeout-finished.processes"
echo 'PASS timeout runs completed; verify kernel states and trace assertions on the host'
