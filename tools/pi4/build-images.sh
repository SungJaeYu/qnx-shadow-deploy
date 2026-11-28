#!/bin/bash
# Use local licensed Pi ingredients to build and verify regular image files.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
selection=${1:-stock}
case "$selection" in stock|issue5|all) ;; *) exit 2 ;; esac
p=artifacts/pi-images
layout="$p/.vendor/layout"
sdk="$p/.vendor/run-sdk.sh"
envdir=artifacts/issue5-environment
revision=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo 'Commit tracked changes before building images.' >&2
    exit 2
fi
mkdir -p "$p/build"
stage=$(mktemp -d "$p/build/publish.XXXXXX")
issue_stage=
trap 'rm -rf "$stage"; if [ -n "$issue_stage" ]; then rm -rf "$issue_stage"; fi' EXIT
mkdir -p "$stage/sensor" "$stage/symbols"
cp -p "$p/sensor/README.md" "$stage/sensor/"
ln -s "../../../sensor/src" "$stage/sensor/src"
{
    printf 'shadowdeploy_git=%s\n' "$revision"
    image_id=$(docker image ls --filter reference=shadowdeploy-qnx-host:24.04 --no-trunc --format '{{.ID}}')
    test -n "$image_id"
    printf 'sdk_host_image=%s\n' "$image_id"
    printf 'sensor_compiler_variant=gcc_ntoaarch64le\n'
    bash "$sdk" ntoaarch64-g++ --version
    shasum -a 256 "$layout/build-ifs.sh" "$layout/render-image.py"
    (cd "$p/sensor"; find src -type f -print | LC_ALL=C sort | while IFS= read -r name; do
        shasum -a 256 "$name"
    done)
    shasum -a 256 tools/pi4/build-sensor.sh
} > "$stage/sensor/BUILD-SOURCE.txt"

bash "$sdk" bash -c '
    set -eo pipefail
    cmake -S . -B build-qnx-pi4 -G Ninja -DCMAKE_BUILD_TYPE=Debug \
        -DCMAKE_TOOLCHAIN_FILE=cmake/qnx8-aarch64.cmake -DBUILD_TESTING=ON
    cmake --build build-qnx-pi4 --parallel 2
    python3 tools/verify_qnx_pi4_artifacts.py build-qnx-pi4
    sh tools/pi4/build-sensor.sh "$1" "$2"
' build-pi4 "$p/sensor" "$stage/sensor" > "$stage/cross-build.log" 2>&1 || {
    cat "$stage/cross-build.log" >&2
    exit 1
}
(
    cd "$stage/sensor"
    shasum -a 256 release/vl53l0x-reader debug/vl53l0x-reader > SHA256SUMS
    cat SHA256SUMS >> BUILD-SOURCE.txt
)

modes=(deploy debug)
build_root="$p/build"
if [ "$selection" = issue5 ]; then
    modes=(debug)
    build_root="$stage/build"
fi
for mode in "${modes[@]}"; do
    sensor=release
    if [ "$mode" = debug ]; then sensor=debug; fi
    out="$build_root/$mode"
    mkdir -p "$out" "$stage/extracted-$mode"
    bash "$sdk" env SHADOW_PI4_BUILD_ROOT="/work/$build_root" bash "$layout/build-ifs.sh" "$mode" \
        --git-revision "$revision" --sensor-dir "/work/$stage/sensor/$sensor" > "$out/build.log" 2>&1 || {
        cat "$out/build.log" "$out/mkifs.log" >&2
        exit 1
    }
    bash "$sdk" bash -c '
        set -e
        dumpifs -x -d "$2" "$3/ifs-rpi4.bin"
        python3 "$4/check-ifs.py" "$1" "$2" "$5/vl53l0x-reader"
        cmp "$2/opt/shadowdeploy/vl53l0x-reader" "$5/vl53l0x-reader"
        cmp "$2/etc/shadowdeploy-sensor-source" "$5/../BUILD-SOURCE.txt"
        if [ "$1" = debug ]; then
            for name in shadow-demo shadow-worker shadow-candidate shadowctl candidate.manifest; do
                cmp "build-qnx-pi4/$name" "$2/opt/shadowdeploy/$name"
            done
        fi
    ' check-pi4 "$mode" "$stage/extracted-$mode" "$out" "$layout" "$stage/sensor/$sensor" \
        > "$out/ifs-check.txt" 2>&1 || { cat "$out/ifs-check.txt" >&2; exit 1; }
    if [ "$selection" != issue5 ]; then
        image="$stage/shadowdeploy-pi4-$mode.img"
        python3 "$layout/wrap-sd.py" "$out/boot.fat" "$image"
        python3 "$layout/check-mounted-image.py" "$image" "$out" \
            "$p/.vendor/firmware" "$layout/config.txt" > "$out/fat-check.txt" 2>&1 || {
            cat "$out/fat-check.txt" >&2
            exit 1
        }
    fi
    if [ "$mode" = debug ]; then
        for name in shadow-demo shadow-worker shadow-candidate shadowctl shadow-range-run vl53l0x-reader; do
            cp -p "$stage/extracted-debug/opt/shadowdeploy/$name" "$stage/symbols/$name"
        done
        cp -p "$out/"/{startup-bcm2711-rpi4,procnto-smp-instr}.sym "$stage/symbols/"
    fi
done

if [ "$selection" != stock ]; then
    issue_stage=$(mktemp -d "$envdir/pi4-stage.XXXXXX")
    bash tools/pi4/build-issue5.sh "$issue_stage" "$stage/sensor/debug" "$revision" "$build_root/debug"
fi
if [ "$selection" != issue5 ]; then
    (
        cd "$stage"
        shasum -a 256 shadowdeploy-pi4-{deploy,debug}.img symbols/* > SHA256SUMS
        shasum -a 256 -c SHA256SUMS
    )
    mkdir -p "$p/symbols"
    mv "$stage/symbols/"* "$p/symbols/"
    mv "$stage/"shadowdeploy-pi4-{deploy,debug}.img "$stage/SHA256SUMS" "$p/"
    for mode in release debug; do
        mkdir -p "$p/sensor/$mode"
        mv "$stage/sensor/$mode/vl53l0x-reader" "$p/sensor/$mode/"
    done
    mv "$stage/sensor/"{BUILD-SOURCE.txt,SHA256SUMS} "$p/sensor/"
fi
mv "$stage/cross-build.log" "$p/"
if [ -n "$issue_stage" ]; then
    python3 tools/pi4/publish-issue5.py "$issue_stage" "$envdir/pi4"
fi
