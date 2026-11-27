#!/bin/sh
# Functional demonstration using the installed binaries and generated manifest.
set -eu
if [ "$#" -ne 1 ]; then
    printf 'Usage: %s BINARY_DIRECTORY\n' "$0" >&2
    exit 1
fi

bin=$(cd "$1" && pwd -P)
umask 077
run=$(mktemp -d "${TMPDIR:-/tmp}/shadowhook.XXXXXX")
printf 'Broker trace and example manifests: %s\n' "$run"
export SHADOW_CONTROL_SOCKET="$run/control.sock"

# Stage the demo fixtures before running the example post-install commands.
# These manifests are copied solely for demonstration, with the same candidate executable.
sed "s|^executable=.*|executable=$bin/shadow-candidate|" "$bin/candidate.manifest" > "$run/good.manifest"
sed 's/^protocol=.*/protocol=1/' "$run/good.manifest" > "$run/reject.manifest"
"$bin/shadow-demo" --worker "$bin/shadow-worker" --control-socket "$SHADOW_CONTROL_SOCKET" \
    --samples 200 --period-ms 200 --deadline-ms 150 --qualify 3 --probation 20 \
    < /dev/null > "$run/broker.log" &
broker=$!
cleanup() {
    kill "$broker" 2>/dev/null || :
    wait "$broker" 2>/dev/null || :
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

sequence() {
    sed -n 's/^{"type":"sample","sequence":\([0-9][0-9]*\),.*/\1/p' "$run/broker.log" | tail -n 1
}
waited=0
while current=$(sequence) && [ "${current:-0}" -lt 16 ]; do
    if [ "$waited" -ge 15 ] || ! kill -0 "$broker" 2>/dev/null; then
        printf 'Broker did not collect its initial replay history. See %s/broker.log\n' "$run" >&2
        exit 1
    fi
    sleep 1
    waited=$((waited + 1))
done

deploy() {
    wanted_code=$1
    wanted_result=$2
    manifest=$3
    code=0
    result=$("$bin/shadowctl" deploy "$manifest" --wait) || code=$?
    printf '%s\n' "$result"
    if [ "$code" -ne "$wanted_code" ] || [ "$result" != "$wanted_result" ]; then
        printf 'Unexpected hook result (exit %s). See %s/broker.log\n' "$code" "$run" >&2
        exit 1
    fi
}

deploy 2 '{"outcome":"rejected","generation":1,"reason":"manifest_protocol_mismatch","active_owner":"baseline"}' \
    "$run/reject.manifest"

# This fixture crashes at an absolute sample sequence during probation.
fault_after=$(($(sequence) + 15))
sed -e 's/^mode=.*/mode=crash/' -e "s/^fault_after=.*/fault_after=$fault_after/" \
    "$run/good.manifest" > "$run/crash.manifest"
deploy 3 '{"outcome":"rolled_back","generation":2,"reason":"worker_disconnected","active_owner":"baseline"}' \
    "$run/crash.manifest"
deploy 0 '{"outcome":"committed","generation":3,"reason":"probation_passed","active_owner":"candidate"}' \
    "$run/good.manifest"
deploy 4 '{"outcome":"refused","generation":null,"reason":"deployment_already_committed","active_owner":"candidate"}' \
    "$run/good.manifest"
