#!/bin/sh
set -eu
[ "$#" -eq 2 ] || { echo "usage: build-sensor.sh SOURCE_TREE OUTPUT_TREE" >&2; exit 2; }
output=$2
mkdir -p "$output/release" "$output/debug"
output=$(cd "$output" && pwd -P)
cd "$1"

q++ -Vgcc_ntoaarch64le -std=c++17 -D_QNX_SOURCE -Isrc -Wall -Wextra -Wpedantic -Werror \
    -O2 -DNDEBUG -Wl,-s -o "$output/release/vl53l0x-reader" \
    src/main.cpp src/clock.cpp src/vendor/VL53L0X.cpp
q++ -Vgcc_ntoaarch64le -std=c++17 -D_QNX_SOURCE -Isrc -Wall -Wextra -Wpedantic -Werror \
    -Og -g3 -o "$output/debug/vl53l0x-reader" \
    src/main.cpp src/clock.cpp src/vendor/VL53L0X.cpp
