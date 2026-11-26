#!/bin/sh
# Observe operator cancellation with the stock worker or a gated replay witness.
set -eu
[ "$(uname -s)" = QNX ] || { echo 'This check requires QNX.' >&2; exit 2; }
cd "${SHADOW_QNX_TARGET_DIR:-/system/bin/shadowdeploy}"
out=${SHADOW_QNX_TRACE_DIR:-/data/shadow-cancel-ordering}
case "$out" in
    /*) ;;
    *) echo 'SHADOW_QNX_TRACE_DIR must be absolute.' >&2; exit 2 ;;
esac
case "${1:-stock}" in
    stock) cases="live replay" ;;
    gated | boundary)
        cases="replay-gated"
        [ "$1" != boundary ] || cases="live-near handoff-gated"
        [ -x ./qnx-cancel-replay-worker ] || { echo 'Missing qnx-cancel-replay-worker.' >&2; exit 2; }
        ;;
    *) echo 'Expected stock, gated or boundary.' >&2; exit 2 ;;
esac
mkdir "$out"
broker=
cleanup() {
    exec 3>&-
    if [ -n "$broker" ]; then kill -TERM "$broker" 2>/dev/null || :; wait "$broker" || :; fi
    rm -f "$out/control.fifo"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

finish_capture_without_receipt() {
    term_sent=0
    if kill -TERM "$broker" 2>/dev/null; then term_sent=1; fi
    printf 'reason=no-receipt term_sent=%s\n' "$term_sent" > "$stem.capture-inconclusive"
    if wait "$broker"; then code=0; else code=$?; fi
    broker=
    exec 3>&-
    printf '%s\n' "$code" > "$stem.exit"
    if pidin ar > "$stem-finished.processes.partial"; then
        mv "$stem-finished.processes.partial" "$stem-finished.processes"
    fi
    if [ "$code" -ne 0 ] && { [ "$code" -ne 130 ] || [ "$term_sent" -ne 1 ]; }; then
        echo "FAIL: broker exited $code while waiting for a receipt." >&2
        exit 1
    fi
    echo 'INCONCLUSIVE: no receipt witness.' >&2
    rm "$out/control.fifo"
    return 77
}

wait_trace() {
    count=0
    until grep -Eq "$1" "$log"; do
        if grep -q '"type":"summary"' "$log" || [ "$count" -ge 250 ]; then
            echo "FAIL $name waiting for $1" >&2
            return 1
        fi
        count=$((count + 1))
        sleep 0.02
    done
}

last_sample() {
    awk -F '[:,]' '/^\{"type":"sample","sequence":/ { n=$4 } END { print n+0 }' "$log"
}

reply_tid() {
    awk -v broker="$broker" -v peer="$candidate" '
        NF == 4 && $1 == broker && $2 > 1 && $3 == "REPLY" && $4 == peer { tid=$2; ++n }
        END { if (n == 1) print tid }
    ' "$1"
}

manifest() {
    candidate_path="$(pwd -P)/${4:-shadow-candidate}" fault_after="$2" awk -v mode="$3" '
        /^executable=/ {print "executable=" ENVIRON["candidate_path"]; next}
        /^fault_after=/ {print "fault_after=" ENVIRON["fault_after"]; next}
        /^mode=/ {print "mode=" mode; next}
        {print}
    ' candidate.manifest > "$out/$1.manifest"
}

manifest good 0 good
manifest live 22 delay
manifest replay 0 delay
export SHADOW_QNX_REPLAY_GATE_DIR="$out"
inconclusive=0

for name in $cases; do
    case "$name" in
        replay-gated|live-near|handoff-gated) manifest "$name" 0 good qnx-cancel-replay-worker ;;
    esac
    export SHADOW_QNX_CANCEL_CASE="$name"
    stem="$out/$name"
    log="$stem.jsonl"
    mkfifo "$out/control.fifo"
    set --
    case "$name" in
        live-near|handoff-gated) set -- --phase-timing ;;
    esac
    ./shadow-demo "$@" --worker ./shadow-worker --control-stdin --samples 44 \
        --period-ms 500 --deadline-ms 450 --qualify 1 --probation 10 \
        < "$out/control.fifo" > "$log" 2> "$stem.stderr" &
    broker=$!
    exec 3> "$out/control.fifo"
    printf '%s\n' "$broker" > "$stem.broker-pid"
    wait_trace '"type":"sample","sequence":16,'
    pidin -p "$broker" fds > "$stem-baseline.fds"
    printf 'stage %s/%s.manifest\n' "$out" "$name" >&3
    [ "$name" = replay-gated ] || wait_trace '"generation":1,.*"event":"stage_requested"'
    candidate=
    if [ "$name" = live ]; then
        wait_trace '"generation":1,.*"event":"candidate_promoted"'
        candidate=$(sed -n '/"generation":1,.*"event":"candidate_promoted"/s/.*"candidate_pid":\([0-9][0-9]*\),.*/\1/p' "$log")
    fi

    if [ "$name" = replay-gated ] || [ "$name" = live-near ] || [ "$name" = handoff-gated ]; then
        if [ "$name" = live-near ]; then
            wait_trace '"type":"sample","sequence":21,'
        fi
        count=0
        while :; do
            receipt=
            if { IFS= read -r receipt < "$stem.witness"; } 2>/dev/null; then
                case "$receipt" in
                    "received pid="*) break ;;
                esac
            fi
            count=$((count + 1))
            if [ "$count" -ge 200 ]; then
                finish_capture_without_receipt || inconclusive=1
                continue 2
            fi
            sleep 0.005
        done
        # Release only after the complete command write to the broker FIFO.
        if [ "$name" = replay-gated ]; then
            printf 'cancel\n' >&3
            printf '%s\n' "$receipt" > "$stem.release"
        else
            target=0
            if [ "$name" = live-near ]; then
                acquired=$(printf '%s\n' "$receipt" | sed -n 's/.* acquired_ns=\([0-9][0-9]*\) .*/\1/p')
                # awk uses doubles, so do the addition in the native helper to preserve ns.
                target=$acquired
            fi
            ./qnx-cancel-replay-worker --write-cancel "$stem" "$target" > "$stem.write"
        fi
    else
        tid=
        observed_from=0
        count=0
        while [ "$count" -lt 100 ]; do
            if [ "$name" = replay ] && [ -z "$candidate" ]; then
                pidin ar > "$stem-worker.processes"
                candidate=$(awk '$2 ~ /(^|\/)shadow-candidate$/ && $3=="delay" && $4=="0" {p=$1; ++n} END {if (n==1) print p}' "$stem-worker.processes")
            fi
            if [ -n "$candidate" ]; then
                observed_from=$(last_sample)
                pidin -p "$broker" -f abJB > "$stem-before.threads"
                tid=$(reply_tid "$stem-before.threads")
                [ -z "$tid" ] || break
            fi
            count=$((count + 1))
            sleep 0.01
        done
        before=$(last_sample)
        printf 'cancel\n' >&3
        after=$(last_sample)
        after_tid=
        if [ -n "$candidate" ]; then
            pidin -p "$broker" -f abJB > "$stem-after.threads"
            after_tid=$(reply_tid "$stem-after.threads")
        fi
        observed_through=$(last_sample)
        printf 'broker=%s peer=%s before_tid=%s after_tid=%s before_observation=%s before_write=%s after_write=%s after_observation=%s\n' \
            "$broker" "${candidate:-0}" "${tid:-0}" "${after_tid:-0}" \
            "$observed_from" "$before" "$after" "$observed_through" > "$stem-ordering.txt"
        # For the live case, both views must be from the same unpublished sample.
        # Replay can advance to another historical request without publishing a live sample.
        if [ -z "$tid" ] || [ "$tid" != "$after_tid" ] ||
            { [ "$name" = live ] && { [ "$observed_from" != "$before" ] || [ "$before" != "$after" ] || [ "$before" != "$observed_through" ]; }; }; then
            inconclusive=1
        fi
    fi

    wait_trace '"generation":1,.*"event":"candidate_cancelled"'
    cancelled=$(sed -n '/"generation":1,.*"event":"candidate_cancelled"/s/^{"type":"sample","sequence":\([0-9][0-9]*\),.*/\1/p' "$log")
    wait_trace "\"type\":\"sample\",\"sequence\":$((cancelled + 2)),"
    pidin -p "$broker" fds > "$stem-cancelled.fds"
    pidin ar > "$stem-cancelled.processes"
    printf 'stage %s/good.manifest\n' "$out" >&3
    wait_trace '"generation":2,.*"event":"candidate_committed"'
    if wait "$broker"; then code=0; else code=$?; fi
    broker=
    exec 3>&-
    rm "$out/control.fifo"
    pidin ar > "$stem-finished.processes"
    printf '%s\n' "$code" > "$stem.exit"
    [ "$code" -eq 0 ] || exit 1
done
if [ "$inconclusive" -eq 1 ]; then
    echo 'INCONCLUSIVE: a required receipt or REPLY observation window was missed.'
    exit 77
fi
echo 'Captured cancellation ordering attempts; run the host verifier.'
