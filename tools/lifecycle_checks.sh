#!/bin/sh
# Run one broker across a late attach, probation crash, rollback and healthy retry.
set -eu
[ "$#" -eq 2 ] || { echo 'usage: sh lifecycle_checks.sh BIN_DIR NEW_ABSOLUTE_OUTPUT_DIR' >&2; exit 2; }
cd "$1"
binary_dir=$(pwd -P)
out=$2
case "$out" in /*) ;; *) echo 'Output directory must be absolute.' >&2; exit 2 ;; esac
mkdir "$out"
log=$out/lifecycle.jsonl
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

fail() { echo "FAIL: $*" >&2; exit 1; }
wait_row() {
    count=0
    while ! row=$(awk -v pattern="$1" '$0 ~ pattern { print; found = 1; exit } END { if (!found) exit 1 }' "$log"); do
        if grep -q '"type":"summary"' "$log" || ! kill -0 "$broker" 2>/dev/null || [ "$count" -ge 100 ]; then
            fail "waiting for $1; see $out"
        fi
        count=$((count + 1))
        sleep 0.1
    done
    printf '%s\n' "$row"
}
number() {
    printf '%s\n' "$1" | sed -n "s/.*\"$2\":\([0-9][0-9]*\)[,}].*/\1/p"
}
observed_child() {
    pid=$(number "$1" "$2")
    [ -n "$pid" ] && [ "$pid" -gt 1 ] && [ "$pid" -ne "$broker" ] ||
        fail "invalid $2 in broker telemetry"
    printf '%s\n' "$pid"
}
wait_retired() {
    count=0
    while kill -0 "$1" 2>/dev/null; do
        [ "$count" -lt 20 ] || fail "PID $1 still exists after retirement"
        count=$((count + 1))
        sleep 0.1
    done
}
snapshot() {
    if [ "$(uname -s)" = QNX ]; then
        if [ "$1" != finished ]; then
            pidin -p "$broker" fds > "$out/$1.fds"
        fi
        pidin ar > "$out/$1.processes"
    fi
}

SHADOW_CANDIDATE="$binary_dir/shadow-candidate" awk '
    /^executable=/ {print "executable=" ENVIRON["SHADOW_CANDIDATE"]; next}
    {print}
' candidate.manifest > "$out/candidate.manifest"
mkfifo "$out/control.fifo"
./shadow-demo --worker ./shadow-worker --control-stdin --samples 90 \
    --period-ms 200 --deadline-ms 150 --qualify 3 --probation 24 \
    < "$out/control.fifo" > "$log" 2> "$out/stderr" &
broker=$!
printf '%s\n' "$broker" > "$out/broker-pid"
exec 3> "$out/control.fifo"

meta=$(wait_row '"type":"meta"')
baseline=$(observed_child "$meta" baseline_pid)
wait_row '"sequence":20,' >/dev/null
snapshot baseline
printf 'stage %s/candidate.manifest\n' "$out" >&3

promote=$(wait_row '"generation":1,.*"event":"candidate_promoted"')
first=$(observed_child "$promote" candidate_pid)
[ "$first" -ne "$baseline" ] || fail 'candidate PID equals baseline PID'
snapshot probation
# Signal only the PID from this owned broker's promotion record, after observing
# probation authority and before commit. The verifier checks the observed transition.
kill -KILL "$first" || fail 'injected candidate termination failed'
wait_row '"generation":1,.*"event":"candidate_rolled_back"' >/dev/null
wait_retired "$first"
kill -0 "$baseline" || fail 'baseline lost at rollback'
snapshot rollback
printf 'stage %s/candidate.manifest\n' "$out" >&3

commit=$(wait_row '"generation":2,.*"event":"candidate_committed"')
second=$(observed_child "$commit" candidate_pid)
[ "$second" -ne "$first" ] && [ "$second" -ne "$baseline" ] || fail 'candidate PID was reused'
wait_retired "$baseline"
kill -0 "$second" || fail 'committed candidate lost'
snapshot committed
if wait "$broker"; then code=0; else code=$?; fi
broker=
printf '%s\n' "$code" > "$out/exit"
wait_retired "$second"
snapshot finished
[ "$code" -eq 0 ] || fail "broker exited with $code"
printf 'PASS child retirement: baseline=%s first=%s second=%s\n' "$baseline" "$first" "$second" |
    tee "$out/retirement"
