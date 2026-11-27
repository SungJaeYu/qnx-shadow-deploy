#!/bin/sh
# Run one broker and keep fresh trace and child retirement observations.
set -eu
profile=
if [ "${1-}" = --qnx-guest-profile ]; then
    profile=$1
    shift
fi
native=false
if [ "$(uname -s)" = QNX ]; then native=true; fi
[ "$#" -eq 2 ] || [ "$#" -eq 3 ] || {
    echo 'usage: sh cancel_retry_cycles.sh [--qnx-guest-profile] BIN_DIR NEW_ABSOLUTE_OUTPUT_DIR [640|10000]' >&2
    exit 2
}
samples=${3-640}
case "$samples" in
    640|10000) ;;
    *) echo 'Sample count must be 640 or 10000.' >&2; exit 2 ;;
esac
period_ms=${SHADOW_PERIOD_MS:-20}
# Keep the normal run guard and allow longer configured periods on extended runs.
run_poll_limit=$(awk -v samples="$samples" -v period="$period_ms" 'BEGIN {
    polls = samples * period / 50 + 2400
    if (period > 1000 || polls < 14400) polls = 14400
    printf "%d", polls
}')
cd "$1"
out=$2
case "$out" in /*) ;; *) echo 'Output directory must be absolute.' >&2; exit 2 ;; esac
mkdir "$out"
log=$out/cycles.jsonl
fifo=$out/control.fifo
broker=
controller=

owned_by() {
    if "$native"; then
        parent=$(pidin -p "$1" -F '%e' | awk '$1 ~ /^[0-9]+$/ {print $1; exit}')
    else
        parent=$(ps -p "$1" -o ppid= | tr -d '[:space:]')
    fi
    [ "$parent" = "$2" ]
}

child_pids() {
    if "$native"; then
        pidin -F '%a %e'
    else
        ps -A -o pid= -o ppid=
    fi | awk -v parent="$1" '$2 == parent {print $1}'
}

snapshot_fds() {
    if "$native" && [ -n "$profile" ]; then
        pidin -p "$broker" fds > "$out/$1.fds"
    fi
}

stop_child() {
    pid=$1
    [ -n "$pid" ] && owned_by "$pid" "$$" || return 0
    kill -TERM "$pid" 2>/dev/null || :
    attempts=0
    while owned_by "$pid" "$$" && [ "$attempts" -lt 40 ]; do
        attempts=$((attempts + 1))
        sleep 0.05
    done
    if owned_by "$pid" "$$"; then
        if [ "$pid" = "$broker" ]; then
            echo 'Broker did not stop after TERM; terminating its remaining children.' >&2
            child_pids "$pid" |
                while read -r child; do
                    if owned_by "$pid" "$$" && owned_by "$child" "$pid"; then
                        kill -KILL "$child" 2>/dev/null || :
                    fi
                done
        fi
        if owned_by "$pid" "$$"; then
            kill -KILL "$pid" 2>/dev/null || :
        fi
    fi
}

cleanup() {
    stop_child "$controller"
    stop_child "$broker"
    if [ -n "$broker" ]; then
        # The shell can still see the child briefly after sending KILL.
        attempts=0
        while kill -0 "$broker" 2>/dev/null && [ "$attempts" -lt 40 ]; do
            attempts=$((attempts + 1))
            sleep 0.05
        done
        if kill -0 "$broker" 2>/dev/null; then
            echo 'Broker still exists after cleanup timeout; exit status unavailable.' >&2
        else
            if wait "$broker"; then code=0; else code=$?; fi
            printf '%s\n' "$code" > "$out/exit"
        fi
    fi
    rm -f "$fifo"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

fail() { echo "FAIL: $*; see $out" >&2; exit 1; }
wait_row() {
    attempts=0
    while ! row=$(awk -v pattern="$1" '$0 ~ pattern { print; found = 1; exit } END { if (!found) exit 1 }' "$log"); do
        if [ "$attempts" -ge "${2:-160}" ]; then
            fail "waiting for $1"
        fi
        if grep -q '"type":"summary"' "$log" || ! kill -0 "$broker" 2>/dev/null; then
            # Recheck after seeing summary in case the requested row arrived during polling.
            row=$(awk -v pattern="$1" '$0 ~ pattern { print; exit }' "$log")
            [ -n "$row" ] || fail "broker finished before $1"
            break
        fi
        attempts=$((attempts + 1))
        sleep 0.05
    done
    printf '%s\n' "$row"
}
child_pid() {
    pid=$(printf '%s\n' "$1" | sed -n "s/.*\"$2\":\([0-9][0-9]*\)[,}].*/\1/p")
    [ -n "$pid" ] && [ "$pid" -gt 1 ] && [ "$pid" -ne "$broker" ] ||
        fail "invalid $2 in broker telemetry"
    printf '%s\n' "$pid"
}
wait_retired() {
    attempts=0
    while kill -0 "$1" 2>/dev/null; do
        [ "$attempts" -lt 40 ] || fail "PID $1 still exists after retirement"
        attempts=$((attempts + 1))
        sleep 0.05
    done
}

summary_seen() {
    tail -n 1 "$log" | grep -q '^{"type":"summary"'
}

SHADOW_CANDIDATE="$(pwd -P)/shadow-candidate" awk '
    /^executable=/ {print "executable=" ENVIRON["SHADOW_CANDIDATE"]; next}
    {print}
' candidate.manifest > "$out/candidate.manifest"
control() {
    # This child alone can block opening the FIFO. The parent supervises both ends.
    trap - EXIT INT TERM
    trap 'exit 1' PIPE
    exec 3> "$fifo"
    printf 'broker %s\n' "$broker" > "$out/retirement"
    meta=$(wait_row '"type":"meta"')
    baseline=$(child_pid "$meta" baseline_pid)
    wait_row '"sequence":20,' >/dev/null

    snapshot_fds initial
    generation=1
    while [ "$generation" -le 21 ]; do
        printf 'stage %s/candidate.manifest\n' "$out" >&3
        wait_row "\"generation\":$generation,.*\"event\":\"stage_requested\"" >/dev/null
        if [ "$generation" -eq 21 ]; then
            committed=$(wait_row '"generation":21,.*"event":"candidate_committed"' 500)
            final=$(child_pid "$committed" candidate_pid)
            wait_retired "$baseline"
            snapshot_fds committed
            kill -0 "$broker" && kill -0 "$final" || fail 'broker or final candidate missing after commit'
            printf 'baseline %s\n' "$baseline" >> "$out/retirement"
        else
            replayed=$(wait_row "\"generation\":$generation,.*\"event\":\"replay_complete\"")
            candidate=$(child_pid "$replayed" candidate_pid)
            [ "$candidate" -ne "$baseline" ] || fail 'candidate PID equals baseline PID'
            boundary=$(printf '%s\n' "$replayed" | sed -n 's/.*"after_sequence":\([0-9][0-9]*\)[,}].*/\1/p')
            [ -n "$boundary" ] || fail 'missing replay boundary'
            wait_row "\"type\":\"sample\",\"sequence\":$((boundary + 1)),.*\"phase\":\"shadowing\"" >/dev/null
            printf 'cancel\n' >&3
            wait_row "\"generation\":$generation,.*\"event\":\"candidate_cancelled\"" >/dev/null
            wait_retired "$candidate"
            snapshot_fds "cancel-$generation"
            kill -0 "$broker" && kill -0 "$baseline" || fail 'broker or baseline missing after cancellation'
            printf 'cancelled %s %s\n' "$generation" "$candidate" >> "$out/retirement"
        fi
        generation=$((generation + 1))
    done

    # The parent watchdog bounds this wait. Inspect only the last trace row.
    while ! summary_seen; do
        if ! kill -0 "$broker" 2>/dev/null; then
            summary_seen || fail 'broker finished before summary'
            break
        fi
        sleep 0.05
    done
    attempts=0
    until [ -s "$out/exit" ]; do
        [ "$attempts" -lt 40 ] || fail 'broker emitted summary but did not exit'
        attempts=$((attempts + 1))
        sleep 0.05
    done
    broker=
    read -r code < "$out/exit"
    wait_retired "$final"
    [ "$code" -eq 0 ] || fail "broker exited with $code"
    printf 'final %s\n' "$final" >> "$out/retirement"
}

mkfifo "$fifo"
./shadow-demo ${profile:+"$profile"} --worker ./shadow-worker --control-stdin --samples "$samples" \
    --period-ms "$period_ms" --deadline-ms "${SHADOW_DEADLINE_MS:-10}" \
    > "$log" 2> "$out/stderr" < "$fifo" &
broker=$!
printf '%s\n' "$broker" > "$out/broker-pid"
control &
controller=$!

# A failed broker launch, blocked FIFO open or hung broker exit cannot hold
# the parent in an unbounded open or wait.
attempts=0
while kill -0 "$controller" 2>/dev/null; do
    if [ -n "$broker" ] && ! kill -0 "$broker" 2>/dev/null; then
        if wait "$broker"; then code=0; else code=$?; fi
        broker=
        printf '%s\n' "$code" > "$out/exit"
        [ "$code" -eq 0 ] || fail "broker exited with $code"
    fi
    if [ "$attempts" -eq 200 ] && ! grep -q '"type":"meta"' "$log"; then
        fail 'broker startup or FIFO rendezvous timed out'
    fi
    [ "$attempts" -lt "$run_poll_limit" ] || fail 'controller exceeded run timeout'
    attempts=$((attempts + 1))
    sleep 0.05
done
if wait "$controller"; then code=0; else code=$?; fi
controller=
[ "$code" -eq 0 ] && grep -q '^final [0-9][0-9]*$' "$out/retirement" ||
    fail 'controller did not complete'
echo "Captured 20 cancellations and a final commit in $out"
