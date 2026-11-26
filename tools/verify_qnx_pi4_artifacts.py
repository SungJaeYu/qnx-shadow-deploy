#!/usr/bin/env python3
"""Validate the project files for the QNX AArch64 board and print SHA-256 sums."""

import argparse
import hashlib
import os
from pathlib import Path
import struct


EXECUTABLES = (
    "shadow-demo", "shadow-worker", "shadow-candidate", "policy-tests",
    "staging-tests", "qnx-ipc-tests", "qnx-ipc-receiver",
)


def check_executable(path, data):
    def require(condition, message):
        if not condition:
            raise ValueError(f"{path}: {message}")

    require(os.access(path, os.X_OK), "execute bit required")
    require(len(data) >= 64 and data[:7] == b"\x7fELF\x02\x01\x01",
            "expected little-endian ELF64")
    elf_type, machine, version = struct.unpack_from("<HHI", data, 16)
    require(elf_type in (2, 3) and machine == 183 and version == 1,
            "expected an AArch64 executable")
    table_offset = struct.unpack_from("<Q", data, 32)[0]
    entry_size, entry_count = struct.unpack_from("<HH", data, 54)
    require(entry_size == 56 and table_offset >= 64 and
            table_offset + entry_count * entry_size <= len(data),
            "invalid ELF program-header table")
    interpreters = []
    for entry in range(entry_count):
        header = table_offset + entry * entry_size
        if struct.unpack_from("<I", data, header)[0] == 3:  # PT_INTERP
            offset = struct.unpack_from("<Q", data, header + 8)[0]
            size = struct.unpack_from("<Q", data, header + 32)[0]
            require(offset + size <= len(data), "invalid ELF interpreter extent")
            interpreters.append(data[offset:offset + size])
    require(interpreters == [b"/usr/lib/ldqnx-64.so.2\0"], "expected the QNX 64-bit interpreter")


def check_manifest(path, data):
    fields = {}
    for line in data.decode("utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or not value or key in fields:
            raise ValueError(f"{path}: malformed or duplicate manifest field: {line!r}")
        fields[key] = value
    expected_fields = {
        "format": "1",
        "artifact": "position-filter-v2",
        "executable": "./shadow-candidate",
        "protocol": "2",
        "architecture": "aarch64",
        "os": "qnx",
        "contract": "position-m-velocity-mps-v1",
        "replay_samples": "16",
        "mode": "good",
        "fault_after": "0",
    }
    for key, expected in expected_fields.items():
        if fields.get(key) != expected:
            raise ValueError(f"{path}: expected {key}={expected}")
    if fields.keys() != expected_fields.keys():
        raise ValueError(f"{path}: unexpected manifest fields: {sorted(fields.keys() - expected_fields.keys())}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_dir", type=Path)
    args = parser.parse_args()
    hashes = []
    try:
        for name in (*EXECUTABLES, "candidate.manifest"):
            path = args.build_dir / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{path}: expected a regular project artifact")
            data = path.read_bytes()
            if name == "candidate.manifest":
                check_manifest(path, data)
            else:
                check_executable(path, data)
            hashes.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print("\n".join(hashes))


if __name__ == "__main__":
    main()
