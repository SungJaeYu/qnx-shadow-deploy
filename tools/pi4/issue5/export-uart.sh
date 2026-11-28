#!/bin/sh
# Run only after timing. Capture each UART export in its own local file.
set -eu
[ "$#" -eq 1 ] || { echo "usage: sh export-uart.sh /issue5-ram/issue5-NAME" >&2; exit 2; }
case "$1" in /tmp/issue5-*|/issue5-ram/issue5-*) ;; *) exit 2;; esac
id=${1##*/}
cd "${1%/*}"
case "$id" in *[!a-zA-Z0-9_-]*) exit 2;; esac
archive="$id.tar.gz"
[ -d "$id" ] && [ -f "$id.meta" ] || exit 2

complete_archive() {
    [ -f "$1" ] && toybox gzip -t "$1" >/dev/null 2>&1
}

# The old exporter could leave a truncated gzip at the original path.
# Preserve those bytes and publish the replacement under a separate name.
if [ -e "$archive" ] && ! complete_archive "$archive"; then
    archive="$id.recovered.tar.gz"
fi
if [ ! -e "$archive" ]; then
    temp=$(toybox mktemp -d "./.$id.export.XXXXXXXX")
    trap 'toybox rm -rf "$temp"' EXIT
    trap 'exit 1' HUP INT TERM
    toybox tar -cf "$temp/run.tar" "$id" "$id.meta"
    toybox gzip -c "$temp/run.tar" > "$temp/complete.tar.gz"
    # Publish only complete bytes. Concurrent exports reuse the winner's file.
    if ! toybox ln -T "$temp/complete.tar.gz" "$archive" 2>/dev/null; then
        [ -f "$archive" ] || exit 1
    fi
fi
if ! complete_archive "$archive"; then
    printf 'Unable to verify complete gzip archive: %s\n' "$archive" >&2
    exit 2
fi
checksum=$(toybox cksum < "$archive")
set -- $checksum
[ "$#" -eq 2 ] || exit 2
printf '\n@@SHADOW5_BEGIN %s %s@@\n' "$1" "$2"
toybox base64 -w 76 "$archive"
printf '@@SHADOW5_END@@\n'
