#!/bin/sh
set -eu
if [ "$#" -ne 2 ]; then echo "usage: sh board-smoke.sh RUN_ID 640|10000" >&2; exit 2; fi
id=$1
samples=$2
case "$id" in ""|*[!a-zA-Z0-9_-]*) exit 2;; esac
case "$samples" in 640|10000) ;; *) exit 2;; esac
dir=/issue5-ram
case "$0" in */*) script_dir=${0%/*} ;; *) script_dir=. ;; esac
kit=$(CDPATH= cd -- "$script_dir" && pwd -P)
app=${SHADOW_ISSUE5_BINS:-/opt/shadowdeploy}
if [ ! -f "$dir/.issue5-ramdisk" ]; then
    if [ -e /dev/ram0 ]; then
        echo "Expected a fresh RAM disk. Reboot before trying again." >&2
        exit 2
    fi
    devb-ram ram capacity=1 blk ramdisk=64m,cache=512k,vnode=256
    waitfor /dev/ram0 5
    [ -b /dev/ram0 ]
    mkqnx6fs -q /dev/ram0
    mount -t qnx6 /dev/ram0 "$dir"
    echo 'ShadowDeploy issue 5 QNX6 RAM disk' > "$dir/.issue5-ramdisk"
fi
exec sh "$kit/run.sh" "$app" "$dir/issue5-$id" "$samples"
