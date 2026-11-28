# Local Raspberry Pi 4B images

From a clean tracked checkout, run `bash tools/pi4/build-images.sh all` on
macOS to build and verify three regular SD image files. The optional
`stock` and `issue5` arguments build only the stock pair or the Issue #5
image. The existing private entrypoints in `artifacts/pi-images/build-images.sh`
and `artifacts/issue5-environment/kit/build-pi-copy.sh` call the same recipe.

The recipe uses the local `shadowdeploy-qnx-host:24.04` container, licensed
QNX SDP and Raspberry Pi BSP, pinned boot firmware, and the private
`artifacts/pi-images/sensor/src` tree. The private
`artifacts/pi-images/.vendor/layout/build-ifs.sh` and `render-image.py`
must honor `SHADOW_PI4_BUILD_ROOT`. The renderer embeds the sensor tree's
`BUILD-SOURCE.txt` as `/etc/shadowdeploy-sensor-source` when available.
The build compiles both reader modes, checks the actual payload, and records
the compiler, source checksums and output checksums. Use the image
directory's `SHA256SUMS` before writing a physical card.

For the Issue #5 UART export, wait for the run to exit and use a fresh host
capture for every attempt. If the archive was successfully created before
a capture failed, exporting the same run resends the exact bytes. An
interrupted new archive stays unpublished. If an older exporter left a
truncated gzip, the replacement is published as `RUN.recovered.tar.gz`
and subsequent retries use those same bytes. Preserve the serial capture
and decode it with `python3 tools/pi4/issue5/decode-uart.py CAPTURE ARCHIVE`.
The decoder checks the transmitted length and POSIX checksum, then verifies
the gzip stream integrity before writing the archive.

Run `python3 tools/pi4/check-uart-host.py` to exercise archive creation,
interruption, retry, and decode with synthetic input and host applet shims.
Target command availability is checked during IFS assembly. Actual
QNX RAM-disk and UART behavior need their own runtime verification.
