#!/bin/sh
# No serial output from the broker or receiver during the timed interval.
set -eu
[ "$#" -eq 3 ] || { echo "usage: sh run.sh BIN_DIR NEW_OUTPUT_DIR 640|10000" >&2; exit 2; }
case "$0" in */*) script_dir=${0%/*} ;; *) script_dir=. ;; esac
kit=$(CDPATH= cd -- "$script_dir" && pwd -P)
bins=$1
out=$2
samples=$3
case "$samples" in 640|10000) ;; *) exit 2;; esac
input="$kit/input-$samples.mm"
case "$out" in /issue5-ram/issue5-*|/tmp/issue5-*|/data/issue5-*) ;; *) echo "invalid run path" >&2; exit 2;; esac
meta="$out.meta"
(umask 077; set -C; : > "$meta")
{
    uname -a
    [ ! -r /etc/shadowdeploy-release ] || cat /etc/shadowdeploy-release
    [ ! -r /etc/issue5-build-source ] || cat /etc/issue5-build-source
    echo "condition=idle"
    echo "input=deterministic-triangle-mm"
    toybox cksum < "$input"
    echo "period_ms=20"
    echo "decision_ms=10"
    echo "ipc_ms=7.5"
    echo "samples=$samples"
    echo "-- processes before --"
    pidin ar
} >> "$meta"
code=0
SHADOW_ISSUE5_TRACE="$input" SHADOW_PERIOD_MS=20 SHADOW_DEADLINE_MS=10 \
    SHADOW_AUTHORITY_RECEIVER="$kit/authority-receiver" \
    sh "$kit/cycles-with-receipts.sh" "$bins" "$out" "$samples" || code=$?
{
    echo "driver_exit=$code"
    echo "-- processes after --"
    pidin ar
} >> "$meta"
exit "$code"
