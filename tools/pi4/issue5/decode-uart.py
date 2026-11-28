#!/usr/bin/env python3
"""Recover one checksummed gzip archive from a serial terminal capture."""
import argparse
import base64
import gzip
import io
from pathlib import Path
import re
import subprocess
import zlib

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("capture", type=Path)
parser.add_argument("archive", type=Path)
args = parser.parse_args()
data = args.capture.read_bytes().replace(b"\r", b"")
frames = re.findall(
    rb"^@@SHADOW5_BEGIN ([0-9]+) ([0-9]+)@@\n([A-Za-z0-9+/=\n]+)^@@SHADOW5_END@@$",
    data, flags=re.MULTILINE,
)
if len(frames) != 1:
    raise SystemExit(f"Expected one complete frame, found {len(frames)}")
crc, length, payload = frames[0]
decoded = base64.b64decode(payload.replace(b"\n", b""), validate=True)
actual = subprocess.run(["cksum"], input=decoded, check=True, capture_output=True).stdout.split()
if actual != [crc, length]:
    raise SystemExit("Archive POSIX checksum or length differs from the board value")
try:
    with gzip.GzipFile(fileobj=io.BytesIO(decoded)) as archive:
        while archive.read(64 * 1024):
            pass
except (EOFError, OSError, zlib.error) as exc:
    raise SystemExit(f"Invalid gzip archive: {exc}") from exc
with args.archive.open("xb") as output:
    output.write(decoded)
print(f"Verified and saved {len(decoded)} archive bytes")
