#!/bin/sh
# Exercise one broker under load without taking in-run process/descriptor snapshots.
set -eu
[ "$#" -eq 2 ] || { echo 'usage: sh lifecycle_loaded_checks.sh BIN_DIR NEW_ABSOLUTE_OUTPUT_DIR' >&2; exit 2; }
cd "$1"
binary_dir=$(pwd -P)
out=$2
case "$out" in /*) ;; *) echo 'Output directory must be absolute.' >&2; exit 2 ;; esac
mkdir "$out"

fail() { echo "FAIL: $*; see $out" >&2; exit 1; }
broker=
observer=
alarm=
cleanup() {
    for job in "$alarm" "$observer" "$broker"; do
        if [ -n "$job" ]; then kill -TERM "$job" 2>/dev/null || :; fi
    done
    for job in "$alarm" "$observer" "$broker"; do
        if [ -n "$job" ]; then wait "$job" 2>/dev/null || :; fi
    done
    rm -f "$out/control.fifo" "$out/telemetry.fifo"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

rewrite_manifest() {
    SHADOW_CANDIDATE="$binary_dir/shadow-candidate" awk -v mode="$1" -v sequence="$2" '
        /^executable=/ {print "executable=" ENVIRON["SHADOW_CANDIDATE"]; next}
        /^mode=/ {print "mode=" mode; next}
        /^fault_after=/ {print "fault_after=" sequence; next}
        {print}
    ' candidate.manifest
}
rewrite_manifest crash 38 > "$out/candidate-crash.manifest"
rewrite_manifest good 0 > "$out/candidate.manifest"

pid_field() {
    pid=${row#*\""$1"\":}
    pid=${pid%%[,\}]*}
    case "$pid" in ''|*[!0-9]*) fail "invalid $1 in broker telemetry" ;; esac
    [ "$pid" -gt 1 ] || fail "invalid $1 in broker telemetry"
}

observe() {
    # The broker opens its telemetry writer first, then its command reader.
    # Keep the command writer open until EOF so stdin stays usable during replay.
    exec 3> "$out/control.fifo"
    baseline= first= second= retried=0
    while IFS= read -r row; do
        printf '%s\n' "$row"
        case "$row" in
            '{"type":"meta",'*)
                pid_field baseline_pid; baseline=$pid ;;
            '{"type":"sample",'*'"generation":1,'*'"event":"candidate_promoted",'*)
                pid_field candidate_pid; first=$pid ;;
            '{"type":"sample",'*'"phase":"rolled_back","source":"baseline",'*'"generation":1,'*'"event":"candidate_rolled_back",'*'"candidate_check":"worker_disconnected",'*)
                pid_field candidate_pid
                [ "$pid" = "$first" ] && [ "$retried" -eq 0 ] || fail 'unexpected rollback identity'
                printf 'stage %s/candidate.manifest\n' "$out" >&3
                retried=1 ;;
            '{"type":"sample",'*'"generation":2,'*'"event":"candidate_committed",'*)
                pid_field candidate_pid; second=$pid ;;
        esac
    done
    [ -n "$baseline" ] && [ -n "$first" ] && [ -n "$second" ] && [ "$retried" -eq 1 ] ||
        fail 'missing promotion, rollback, or retry commit'
    printf '%s %s %s\n' "$baseline" "$first" "$second" > "$out/worker-pids"
}

mkfifo "$out/control.fifo" "$out/telemetry.fifo"
: > "$out/lifecycle.jsonl"
observe < "$out/telemetry.fifo" >> "$out/lifecycle.jsonl" 2> "$out/observer.stderr" &
observer=$!
./shadow-demo --worker ./shadow-worker --control-stdin --samples 90 \
    --period-ms 200 --deadline-ms 150 --qualify 3 --probation 24 \
    --candidate-manifest "$out/candidate-crash.manifest" --stage-at 20 \
    > "$out/telemetry.fifo" < "$out/control.fifo" 2> "$out/stderr" &
broker=$!
printf '%s\n' "$broker" > "$out/broker-pid"

(
    sleep 35 &
    sleeper=$!
    trap 'kill -TERM "$sleeper" 2>/dev/null || :; wait "$sleeper" 2>/dev/null || :; exit 0' TERM
    wait "$sleeper" || exit 0
    echo 'Loaded lifecycle runner timed out.' > "$out/timeout"
    kill -TERM "$$" 2>/dev/null || :
) &
alarm=$!

if wait "$broker"; then code=0; else code=$?; fi
broker=
printf '%s\n' "$code" > "$out/exit"
if wait "$observer"; then observed=0; else observed=$?; fi
observer=
kill -TERM "$alarm" 2>/dev/null || :
wait "$alarm" 2>/dev/null || :
alarm=
[ "$code" -eq 0 ] || fail "broker exited with $code"
[ "$observed" -eq 0 ] || fail "observer exited with $observed"
read -r baseline first second < "$out/worker-pids"
for pid in "$baseline" "$first" "$second"; do
    if kill -0 "$pid" 2>/dev/null; then fail "worker PID $pid still exists after broker exit"; fi
done
printf 'PASS child retirement: baseline=%s first=%s second=%s\n' "$baseline" "$first" "$second" > "$out/retirement"
cat "$out/retirement"
