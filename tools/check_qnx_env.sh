#!/bin/sh
# Read-only preflight. Never prints license contents or account credentials.
set -eu
missing=0
for tool in q++ cmake mkqnximage; do
    if command -v "$tool" >/dev/null 2>&1; then
        printf 'FOUND %s\n' "$tool"
    else
        printf 'MISSING %s\n' "$tool"
        missing=1
    fi
done
if [ -z "${QNX_HOST:-}" ] || [ -z "${QNX_TARGET:-}" ]; then
    printf 'MISSING QNX_HOST or QNX_TARGET; source qnxsdp-env.sh\n'
    missing=1
fi
if [ "$missing" -ne 0 ]; then
    printf 'QNX cross-build environment is not ready. See docs/qnx-setup.md.\n'
    exit 1
fi
printf 'Tool discovery passed. Compile and run the target tests to verify the installation and license.\n'
