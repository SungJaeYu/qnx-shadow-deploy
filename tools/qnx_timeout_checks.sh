#!/bin/sh
# Observe native waits for a stopped receiver and a 250 ms delayed reply.
set -eu
[ "$(uname -s)" = QNX ] || exit 2
cd "${SHADOW_QNX_TARGET_DIR:-/system/bin/shadowdeploy}"
out=${SHADOW_QNX_TRACE_DIR:-/data/shadow-validation}
case "$out" in
    /*) ;;
    *) echo 'SHADOW_QNX_TRACE_DIR must be absolute.' >&2; exit 2 ;;
esac
mkdir -p "$out"
for kind in stopped reply-observed; do
    log="$out/timeout-$kind.jsonl"
    [ ! -e "$log" ] || { echo "Refusing to overwrite $log" >&2; exit 2; }
    scenario=promote
    [ "$kind" != reply-observed ] || scenario=reject-delay
    ./shadow-demo --worker ./shadow-worker --scenario "$scenario" --samples 4 \
        --period-ms 1000 --deadline-ms 150 --qualify 3 --probation 8 \
        > "$log" 2> "$out/timeout-$kind.stderr" &
    broker=$!
    trap 'kill -TERM "$broker" 2>/dev/null || :; wait "$broker" || :' EXIT
    printf '%s\n' "$broker" > "$out/timeout-$kind.broker-pid"
    tries=0
    until grep -q '"sequence":1,' "$log"; do
        tries=$((tries + 1))
        [ "$tries" -lt 100 ] || exit 1
        sleep 0.01
    done
    candidate=$(head -n 1 "$log" | sed 's/.*"candidate_pid":\([0-9]*\).*/\1/')
    if [ "$kind" = stopped ]; then
        kill -STOP "$candidate"
    fi
    # Poll through the second acquisition; one late snapshot can miss the wait.
    sleep 0.75
    tries=0
    states="$out/timeout-$kind.threads"
    : > "$states"
    while [ "$tries" -lt 100 ]; do
        pidin -p "$broker" >> "$states" || break
        if grep -Eq "^[[:space:]]*$broker[[:space:]]+.*[[:space:]]REPLY[[:space:]]+$candidate([[:space:]]|$)" "$states"; then
            break
        fi
        tries=$((tries + 1))
        sleep 0.01
    done
    if wait "$broker"; then code=0; else code=$?; fi
    trap - EXIT
    echo "$kind $code" >> "$out/timeout-exits.txt"
    [ "$code" -eq 0 ] || exit 1
done
pidin ar > "$out/timeout-finished.processes"
echo 'PASS timeout runs completed; verify kernel states and trace assertions on the host'
