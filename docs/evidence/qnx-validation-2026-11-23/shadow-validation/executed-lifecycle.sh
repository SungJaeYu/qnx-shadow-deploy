#!/bin/sh
# Run against the existing image; capture native connections and original JSONL.
set -eu
[ "$(uname -s)" = QNX ] || { echo 'This check requires a QNX guest.' >&2; exit 2; }
cd /system/bin/shadowdeploy
out=/data/shadow-validation
mkdir -p "$out"
broker=
cleanup() {
    exec 3>&-
    if [ -n "$broker" ]; then kill -TERM "$broker" 2>/dev/null || :; wait "$broker" || :; fi
    rm -f "$out/control.fifo"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

wait_event() {
    count=0
    until grep -q "$1" "$log"; do
        if grep -q '"type":"summary"' "$log" || [ "$count" -ge 100 ]; then
            echo "FAIL waiting for $1 in $log" >&2
            return 1
        fi
        count=$((count + 1))
        sleep 0.1
    done
}
snapshot() {
    # Let retirement after the publication decision finish before inspection.
    sleep 0.4
    pidin -p "$broker" fds > "$out/$name-$1.fds"
    pidin ar > "$out/$name-$1.processes"
}
start() {
    name=$1
    log="$out/$name.jsonl"
    [ ! -e "$log" ] || { echo "Refusing to overwrite $log" >&2; exit 2; }
    mkfifo "$out/control.fifo"
    ./shadow-demo --worker ./shadow-worker --control-stdin --samples 100 \
        --period-ms 200 --deadline-ms 150 --qualify 3 --probation 15 \
        < "$out/control.fifo" > "$log" 2> "$out/$name.stderr" &
    broker=$!
    exec 3> "$out/control.fifo"
    wait_event '"sequence":20,'
    snapshot baseline
}
finish() {
    wait_event '"event":"candidate_committed"'
    snapshot committed
    if wait "$broker"; then code=0; else code=$?; fi
    broker=
    exec 3>&-
    rm "$out/control.fifo"
    echo "$name $code" >> "$out/lifecycle-exits.txt"
    pidin ar > "$out/$name-finished.processes"
    [ "$code" -eq 0 ] || return 1
}

sed 's|executable=./shadow-candidate|executable=/system/bin/shadowdeploy/shadow-candidate|' \
    candidate.manifest > "$out/good.manifest"
sed 's/mode=good/mode=delay/' "$out/good.manifest" > "$out/delay.manifest"
sed 's/mode=good/mode=stale/' "$out/good.manifest" > "$out/stale.manifest"

start cancel-retry
printf 'stage %s/good.manifest\n' "$out" >&3
wait_event '"event":"candidate_promoted"'
snapshot promoted
printf 'cancel\n' >&3
wait_event '"event":"candidate_cancelled"'
snapshot cancelled
printf 'stage %s/good.manifest\n' "$out" >&3
finish

start replay-retry
generation=0
for cycle in 1 2 3; do
    for mode in delay stale; do
        generation=$((generation + 1))
        printf 'stage %s/%s.manifest\n' "$out" "$mode" >&3
        wait_event "\"generation\":$generation,.*\"event\":\"preparation_failed\""
        snapshot "rejected-$generation"
    done
done
printf 'stage %s/good.manifest\n' "$out" >&3
finish
echo 'PASS cancellation, repeated replay rejection, retry and connection snapshots'
