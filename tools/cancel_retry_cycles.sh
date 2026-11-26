#!/bin/sh
# Run one broker and keep fresh trace and child retirement observations.
set -eu
[ "$#" -eq 2 ] || {
    echo 'usage: sh cancel_retry_cycles.sh BIN_DIR NEW_ABSOLUTE_OUTPUT_DIR' >&2
    exit 2
}
cd "$1"
out=$2
case "$out" in /*) ;; *) echo 'Output directory must be absolute.' >&2; exit 2 ;; esac
mkdir "$out"
log=$out/cycles.jsonl
broker=
cleanup() {
    exec 3>&-
    if [ -n "$broker" ]; then
        kill -TERM "$broker" 2>/dev/null || :
        wait "$broker" || :
    fi
    rm -f "$out/control.fifo"
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

SHADOW_CANDIDATE="$(pwd -P)/shadow-candidate" awk '
    /^executable=/ {print "executable=" ENVIRON["SHADOW_CANDIDATE"]; next}
    {print}
' candidate.manifest > "$out/candidate.manifest"
mkfifo "$out/control.fifo"
./shadow-demo --worker ./shadow-worker --control-stdin --samples 320 \
    --period-ms "${SHADOW_PERIOD_MS:-20}" --deadline-ms "${SHADOW_DEADLINE_MS:-10}" \
    < "$out/control.fifo" > "$log" 2> "$out/stderr" &
broker=$!
printf 'broker %s\n' "$broker" > "$out/retirement"
exec 3> "$out/control.fifo"

meta=$(wait_row '"type":"meta"')
baseline=$(child_pid "$meta" baseline_pid)
wait_row '"sequence":20,' >/dev/null

generation=1
while [ "$generation" -le 21 ]; do
    printf 'stage %s/candidate.manifest\n' "$out" >&3
    wait_row "\"generation\":$generation,.*\"event\":\"stage_requested\"" >/dev/null
    if [ "$generation" -eq 21 ]; then
        committed=$(wait_row '"generation":21,.*"event":"candidate_committed"' 500)
        final=$(child_pid "$committed" candidate_pid)
        wait_retired "$baseline"
        kill -0 "$broker" && kill -0 "$final" || fail 'broker or final candidate missing after commit'
        printf 'baseline %s\n' "$baseline" >> "$out/retirement"
    else
        replayed=$(wait_row "\"generation\":$generation,.*\"event\":\"replay_complete\"")
        candidate=$(child_pid "$replayed" candidate_pid)
        [ "$candidate" -ne "$baseline" ] || fail 'candidate PID equals baseline PID'
        printf 'cancel\n' >&3
        wait_row "\"generation\":$generation,.*\"event\":\"candidate_cancelled\"" >/dev/null
        wait_retired "$candidate"
        kill -0 "$broker" && kill -0 "$baseline" || fail 'broker or baseline missing after cancellation'
        printf 'cancelled %s %s\n' "$generation" "$candidate" >> "$out/retirement"
    fi
    generation=$((generation + 1))
done

wait_row '"type":"summary"' 8000 >/dev/null
if wait "$broker"; then code=0; else code=$?; fi
broker=
printf '%s\n' "$code" > "$out/exit"
wait_retired "$final"
printf 'final %s\n' "$final" >> "$out/retirement"
[ "$code" -eq 0 ] || fail "broker exited with $code"
echo "Captured 20 cancellations and a final commit in $out"
