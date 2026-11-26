#!/bin/sh
# Collect baseline-only native traces at idle and under controlled CPU contention.
set -eu

if [ "$#" -ne 8 ]; then
    echo "Usage: $0 /bin-dir /new-out-dir trials samples period_ms deadline_ms busy_loops environment.txt" >&2
    exit 2
fi
[ "$(uname -s)" = QNX ] || { echo 'This collector requires QNX.' >&2; exit 2; }
bin=$1 out=$2 trials=$3 samples=$4 period=$5 deadline=$6 loops=$7 environment=$8
case "$bin:$out" in /*:/*) ;; *) echo 'Use absolute binary and output paths.' >&2; exit 2 ;; esac
for number in "$trials" "$samples" "$period" "$deadline" "$loops"; do
    case "$number" in 0*|*[!0-9]*|'') echo 'Use positive decimal integers.' >&2; exit 2 ;; esac
    [ "$number" -le 100000 ] || exit 2
done
[ "$deadline" -lt "$period" ] && [ "$period" -le 1000 ] && [ "$loops" -le 64 ] || exit 2
[ -x "$bin/shadow-demo" ] && [ -x "$bin/shadow-worker" ] && [ -s "$environment" ] || exit 2
for command in pidin cksum cmp date; do command -v "$command" >/dev/null || exit 2; done

mkdir "$out"
cat "$environment" > "$out/environment.txt"
uname -a > "$out/uname.txt"
pidin info > "$out/pidin-info.txt"
pidin ar > "$out/initial.processes"
cksum "$bin/shadow-demo" "$bin/shadow-worker" > "$out/binaries.cksum"
printf '%s %s %s %s %s\n' "$trials" "$samples" "$period" "$deadline" "$loops" > "$out/plan.txt"

broker=
load_pids=
stem=
stop_load() {
    for pid in $load_pids; do kill -TERM "$pid" 2>/dev/null || :; done
    for pid in $load_pids; do wait "$pid" || :; done
    load_pids=
}
cleanup() {
    if [ -n "$broker" ]; then kill -TERM "$broker" 2>/dev/null || :; wait "$broker" || :; fi
    stop_load
}
interrupt() {
    trap '' INT TERM
    if [ -n "$stem" ]; then
        printf 'requested\n' > "$stem.operator-stop"
        if [ -n "$broker" ]; then
            kill -TERM "$broker" 2>/dev/null || :
            if wait "$broker"; then status=0; else status=$?; fi
            broker=
            printf '%s\n' "$status" > "$stem.exit"
        fi
        load_complete=yes
        for pid in $load_pids; do kill -0 "$pid" 2>/dev/null || load_complete=no; done
        stop_load
        printf '%s\n' "$load_complete" > "$stem.load-complete"
        pidin ar > "$stem.processes" || :
        date -u '+%Y-%m-%dT%H:%M:%SZ' > "$stem.finished" || :
    fi
    exit 130
}
trap cleanup 0
trap interrupt INT TERM

# Alternate within each repetition to reduce elapsed-time bias between conditions.
i=1
while [ "$i" -le "$trials" ]; do
    for condition in idle cpu; do
        stem="$out/$condition-$i"
        date -u '+%Y-%m-%dT%H:%M:%SZ' > "$stem.started"
        if [ "$condition" = cpu ]; then
            j=0
            while [ "$j" -lt "$loops" ]; do
                (while :; do :; done) &
                load_pids="$load_pids $!"
                j=$((j + 1))
            done
        fi
        printf '%s\n' $load_pids > "$stem.load-pids"
        for pid in $load_pids; do kill -0 "$pid"; done
        "$bin/shadow-demo" --worker "$bin/shadow-worker" --control-stdin \
            --samples "$samples" --period-ms "$period" --deadline-ms "$deadline" \
            < /dev/null > "$stem.jsonl" 2> "$stem.stderr" &
        broker=$!
        printf '%s\n' "$broker" > "$stem.broker-pid"
        if wait "$broker"; then status=0; else status=$?; fi
        broker=
        printf '%s\n' "$status" > "$stem.exit"
        load_complete=yes
        for pid in $load_pids; do kill -0 "$pid" 2>/dev/null || load_complete=no; done
        stop_load
        printf '%s\n' "$load_complete" > "$stem.load-complete"
        pidin ar > "$stem.processes"
        date -u '+%Y-%m-%dT%H:%M:%SZ' > "$stem.finished"
        stem=
    done
    i=$((i + 1))
done
cksum "$bin/shadow-demo" "$bin/shadow-worker" > "$out/final-binaries.cksum"
cmp "$out/binaries.cksum" "$out/final-binaries.cksum"
printf 'complete\n' > "$out/collection-status"
