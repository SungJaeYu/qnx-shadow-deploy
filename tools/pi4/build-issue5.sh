#!/bin/bash
# Assemble the issue 5 debug variant from the freshly generated debug buildfile.
set -euo pipefail
board=$1
sensor=$2
revision=$3
debug_build=$4
base=artifacts/pi-images
kit=artifacts/issue5-environment/kit
sdk="$base/.vendor/run-sdk.sh"
layout="$base/.vendor/layout"
# Retain SDK Toybox bytes exactly so the embedded applet symbol table can be inspected.
sed 's|^\[perms=0555\] /usr/bin/toybox=|[perms=0555 +raw] /usr/bin/toybox=|' \
    "$debug_build/rpi4.build" > "$board/rpi4.build"
{
    printf 'committed_source=%s\n' "$revision"
    shasum -a 256 src/main.cpp build-qnx-pi4/shadow-demo
} > "$board/issue5-build-source.txt"
printf '\n[perms=0444] /etc/issue5-build-source=/work/%s/issue5-build-source.txt\n' "$board" >> "$board/rpi4.build"
printf '\n[perms=0555] /usr/bin/gawk=/opt/qnx/target/qnx/aarch64le/usr/bin/gawk\n[type=link] /usr/bin/awk=/usr/bin/gawk\n' >> "$board/rpi4.build"
for utility in egrep printf sed; do
    printf '[type=link] /bin/%s=/usr/bin/toybox\n' "$utility"
done >> "$board/rpi4.build"
for name in run.sh export-uart.sh cycles-with-receipts.sh board-smoke.sh; do
    source="$kit"
    if [ "$name" != cycles-with-receipts.sh ]; then source=tools/pi4/issue5; fi
    printf '[perms=0555] /opt/issue5/%s=/work/%s/%s\n' "$name" "$source" "$name"
done >> "$board/rpi4.build"
for samples in 640 10000; do
    printf '[perms=0444] /opt/issue5/input-%s.mm=/work/%s/input-%s.mm\n' "$samples" "$kit" "$samples"
done >> "$board/rpi4.build"
cat >> "$board/rpi4.build" <<'BUILDFILE'
[type=dir dperms=0755] /issue5-ram
[perms=0555] /sbin/devb-ram=/opt/qnx/target/qnx/aarch64le/sbin/devb-ram
[perms=0555] /sbin/mkqnx6fs=/opt/qnx/target/qnx/aarch64le/sbin/mkqnx6fs
[perms=0555] /bin/mount=/opt/qnx/target/qnx/aarch64le/bin/mount
[perms=0555] /lib/io-blk.so=/opt/qnx/target/qnx/aarch64le/lib/dll/io-blk.so
[perms=0555] /lib/fs-qnx6.so=/opt/qnx/target/qnx/aarch64le/lib/dll/fs-qnx6.so
[perms=0555] /lib/cam-disk.so=/opt/qnx/target/qnx/aarch64le/lib/dll/cam-disk.so
[perms=0555] /lib/libcam.so=/opt/qnx/target/qnx/aarch64le/lib/libcam.so
BUILDFILE
printf '[perms=0555 +raw] /opt/issue5/authority-receiver=/work/%s/authority-receiver\n' "$board" >> "$board/rpi4.build"
sed "s|/work/$debug_build/ifs-rpi4.bin|/work/$board/ifs-rpi4.bin|" "$debug_build/fat.build" > "$board/fat.build"
mkdir "$board/extracted" "$board/symbols"
bash "$sdk" bash -c '
    set -eo pipefail
    base=$1; kit=$2; board=$3; sensor=$4; layout=$5
    qcc -Vgcc_ntoaarch64le -O2 -Wall -Wextra -Werror "$kit/authority-receiver.c" -o "$board/authority-receiver"
    mkifs -v -o "$board" -r"$base/.vendor/bsp/prebuilt" "$board/rpi4.build" "$board/ifs-rpi4.bin" > "$board/mkifs.log" 2>&1
    dumpifs -x -d "$board/extracted" "$board/ifs-rpi4.bin"
    python3 "$layout/check-ifs.py" debug "$board/extracted" "$sensor/vl53l0x-reader"
    cmp "$sensor/vl53l0x-reader" "$board/extracted/opt/shadowdeploy/vl53l0x-reader"
    for name in shadow-demo shadow-worker shadow-candidate shadowctl candidate.manifest; do
        cmp "build-qnx-pi4/$name" "$board/extracted/opt/shadowdeploy/$name"
    done
    cmp "$sensor/../BUILD-SOURCE.txt" "$board/extracted/etc/shadowdeploy-sensor-source"
    cmp "$board/issue5-build-source.txt" "$board/extracted/etc/issue5-build-source"
    cmp "$board/authority-receiver" "$board/extracted/opt/issue5/authority-receiver"
    for name in input-640.mm input-10000.mm cycles-with-receipts.sh; do
        cmp "$kit/$name" "$board/extracted/opt/issue5/$name"
    done
    for name in run.sh export-uart.sh board-smoke.sh; do
        cmp "tools/pi4/issue5/$name" "$board/extracted/opt/issue5/$name"
    done
    cmp "$QNX_TARGET/aarch64le/usr/bin/toybox" "$board/extracted/usr/bin/toybox"
    dumpifs -v "$board/ifs-rpi4.bin" > "$board/dumpifs.txt"
    python3 tools/pi4/check-issue5-ifs.py "$board/extracted" "$board/dumpifs.txt"
    mkfatfsimg "$board/fat.build" "$board/boot.fat" > "$board/mkfatfsimg.log" 2>&1
' issue5-pi-kit "$base" "$kit" "$board" "$sensor" "$layout" > "$board/offline-build.log" 2>&1 || {
    cat "$board/offline-build.log" "$board/mkifs.log" >&2
    exit 1
}
for name in shadow-demo shadow-worker shadow-candidate shadowctl shadow-range-run vl53l0x-reader; do
    cp -p "$board/extracted/opt/shadowdeploy/$name" "$board/symbols/$name"
done
image=shadowdeploy-pi4-issue5-debug.img
python3 "$layout/wrap-sd.py" "$board/boot.fat" "$board/$image"
python3 "$layout/check-mounted-image.py" "$board/$image" "$board" \
    "$base/.vendor/firmware" "$layout/config.txt" > "$board/fat-check.txt" 2>&1 || {
    cat "$board/fat-check.txt" >&2
    exit 1
}
(
    cd "$board"
    shasum -a 256 "$image" authority-receiver ./*.sym symbols/* > SHA256SUMS
    shasum -a 256 -c SHA256SUMS
)
