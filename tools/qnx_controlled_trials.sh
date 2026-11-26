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
for command in pidin cksum cmp date awk sed tail grep tr sleep; do command -v "$command" >/dev/null || exit 2; done

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
cleanup_detail=
shutdown() {
    # Only signal children launched by this shell. A worker from a trace is
    # evidence of a possible orphan, never a signal target.
    children="$broker $load_pids"
    for pid in $children; do kill -TERM "$pid" 2>/dev/null || :; done
    remaining=3
    while :; do
        living=
        for pid in $children; do
            if kill -0 "$pid" 2>/dev/null; then living="$living $pid"; fi
        done
        [ -n "$living" ] || break
        if [ "$remaining" -eq 0 ]; then
            printf '%s\n' $living > "$stem.unreaped-pids"
            cleanup_detail=children_still_present
            return 1
        fi
        remaining=$((remaining - 1))
        sleep 1
    done
    if [ -n "$broker" ]; then
        if wait "$broker"; then status=0; else status=$?; fi
        printf '%s\n' "$status" > "$stem.exit"
        broker=
    fi
    for pid in $load_pids; do wait "$pid" || :; done
    load_pids=
}
finish() {
    pidin ar > "$stem.processes" || { cleanup_detail=snapshot_unavailable; return 1; }
    date -u '+%Y-%m-%dT%H:%M:%SZ' > "$stem.finished" || { cleanup_detail=timestamp_unavailable; return 1; }
    baseline_pid=$(sed -n '1s/^{"type":"meta",.*"baseline_pid":\([1-9][0-9]*\),.*$/\1/p' "$stem.jsonl")
    broker_pid=$(cat "$stem.broker-pid")
    case "$baseline_pid" in [1-9]*) ;; *) cleanup_detail=baseline_pid_unavailable; return 1 ;; esac
    # Require the broker's terminal record and a parser-recognizable process
    # snapshot before treating an attempt as isolated from the next one.
    tail -n 1 "$stem.jsonl" | grep -q '^{"type":"summary",' || {
        cleanup_detail=terminal_record_unavailable
        return 1
    }
    snapshot_result=0
    awk -v owned="$broker_pid $baseline_pid $(tr '\n' ' ' < "$stem.load-pids")" '
        BEGIN {n = split(owned, p); for (i = 1; i <= n; ++i) wanted[p[i]] = 1}
        NR == 1 {header = (tolower($1) == "pid" && tolower($2) == "arguments" && NF == 2); next}
        NF == 0 {next}
        $1 !~ /^[1-9][0-9]*$/ || NF < 2 || found[$1]++ {invalid = 1; next}
        {++seen; if ($1 in wanted) leak = 1}
        END {if (!header || !seen || invalid) exit 1; if (leak) exit 2}
    ' "$stem.processes" || snapshot_result=$?
    case "$snapshot_result" in
        0) return 0 ;;
        2) cleanup_detail=owned_process_present ;;
        *) cleanup_detail=invalid_process_snapshot ;;
    esac
    return 1
}
abort_cleanup() {
    trap - 0
    printf '%s\n' "$cleanup_detail" > "$stem.cleanup"
    printf 'aborted_cleanup\n' > "$out/collection-status"
    exit 1
}
cleanup() {
    if [ -n "$broker$load_pids" ]; then shutdown || :; fi
}
interrupt() {
    trap '' INT TERM
    if [ -n "$stem" ]; then
        printf 'requested\n' > "$stem.operator-stop"
        load_complete=yes
        for pid in $load_pids; do kill -0 "$pid" 2>/dev/null || load_complete=no; done
        printf '%s\n' "$load_complete" > "$stem.load-complete"
        stopped=yes
        shutdown || stopped=no
        finish || stopped=no
        if [ "$stopped" = yes ]; then
            printf 'verified\n' > "$stem.cleanup"
        else
            printf '%s\n' "$cleanup_detail" > "$stem.cleanup"
        fi
    fi
    trap - 0
    printf 'interrupted\n' > "$out/collection-status"
    exit 130
}
trap cleanup 0
trap interrupt INT TERM

# Alternate within each repetition to reduce elapsed-time bias between conditions.
i=1
while [ "$i" -le "$trials" ]; do
    for condition in idle cpu; do
        stem="$out/$condition-$i"
        cleanup_detail=
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
        printf '%s\n' "$load_complete" > "$stem.load-complete"
        stopped=yes
        shutdown || stopped=no
        finish || stopped=no
        [ "$stopped" = yes ] || abort_cleanup
        case "$status" in 0|3) ;; *) cleanup_detail=unexpected_broker_exit; abort_cleanup ;; esac
        printf 'verified\n' > "$stem.cleanup"
        stem=
    done
    i=$((i + 1))
done
cksum "$bin/shadow-demo" "$bin/shadow-worker" > "$out/final-binaries.cksum"
cmp "$out/binaries.cksum" "$out/final-binaries.cksum"
printf 'complete\n' > "$out/collection-status"
