#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
mkdir -p artifacts/pi-images/build
exec python3 tools/pi4/publish-images.py lock artifacts/pi-images/build/.build.lock \
    bash tools/pi4/assemble-images.sh "$@"
