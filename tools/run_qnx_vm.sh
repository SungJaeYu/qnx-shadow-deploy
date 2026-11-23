#!/bin/bash
# Boot the generated x86-64 development image using software emulation.
set -euo pipefail

action=${1:-run}
case "$action" in
    help|-h|--help)
        cat <<'EOF'
Usage: bash tools/run_qnx_vm.sh [run|--print-command]

Requires qemu-system-x86_64 on the host (macOS: brew install qemu).
SHADOW_QEMU may specify an explicit QEMU executable.
Uses 2 virtual CPUs, 1 GiB RAM, TCG software emulation and a serial console.
No guest networking or host directory sharing is enabled.
Disk changes are temporary; export guest logs before exiting.
Press Ctrl-A then X to exit QEMU, or Ctrl-A then C for its monitor.
EOF
        exit 0 ;;
    run|--print-command) ;;
    *) printf 'Unknown action: %s\n' "$action" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then exit 2; fi

project_dir=$(cd "$(dirname "$0")/.." && pwd -P)
output_dir=$project_dir/build-qnx-vm/output
for name in ifs.bin disk-qemu; do
    if [ ! -s "$output_dir/$name" ]; then
        printf 'Missing image: %s. Run qnx_container.sh vm-image first.\n' "$name" >&2
        exit 1
    fi
done
case "$output_dir" in
    *,*) printf 'QEMU image paths must not contain commas.\n' >&2; exit 2 ;;
esac

qemu=${SHADOW_QEMU:-qemu-system-x86_64}
args=(-machine pc -accel tcg -cpu max -smp 2 -m 1024
    -kernel "$output_dir/ifs.bin"
    -drive "file=$output_dir/disk-qemu,format=raw,if=ide,id=drv0"
    -snapshot -nic none -nographic -serial mon:stdio -no-reboot
    -object rng-random,filename=/dev/urandom,id=rng0
    -device virtio-rng-pci,rng=rng0)
if [ "$action" = --print-command ]; then
    printf '%q ' "$qemu" "${args[@]}"
    printf '\n'
    exit 0
fi
if ! command -v "$qemu" >/dev/null 2>&1; then
    printf 'QEMU not found. On macOS, run: brew install qemu\n' >&2
    exit 1
fi
"$qemu" --version
printf 'Booting QNX. Guest disk changes will be discarded on exit.\n'
printf 'After startup completes: cd /system/bin/shadowdeploy\n'
exec "$qemu" "${args[@]}"
