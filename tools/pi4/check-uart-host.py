#!/usr/bin/env python3
"""Exercise the target export with synthetic inputs and host applet shims."""
import base64
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

root = Path(__file__).resolve().parents[2]
exporter = root / 'tools/pi4/issue5/export-uart.sh'
decoder = root / 'tools/pi4/issue5/decode-uart.py'
with tempfile.TemporaryDirectory(prefix='pi4-uart-host-') as scratch:
    work = Path(scratch)
    toybox = work / 'toybox'
    toybox.write_text(f"""#!{sys.executable}
import base64, os, pathlib, subprocess, sys
command, *args = sys.argv[1:]
if command == 'ln':
    os.link(*args[1:])
elif command == 'base64':
    if os.environ.get('BREAK_STREAM'):
        print('XXXX', flush=True)
        raise SystemExit(7)
    data = base64.b64encode(pathlib.Path(args[2]).read_bytes())
    for offset in range(0, len(data), int(args[1])):
        print(data[offset:offset + int(args[1])].decode())
elif command == 'gzip' and args[0] == '-c' and os.environ.get('BREAK_COMPRESSION'):
    sys.stdout.write('partial gzip contents')
    raise SystemExit(8)
else:
    raise SystemExit(subprocess.run([f'/usr/bin/{{command}}' if command != 'rm' else '/bin/rm', *args]).returncode)
""")
    toybox.chmod(0o755)
    with tempfile.TemporaryDirectory(prefix='issue5-uart-check-', dir='/tmp') as run_dir:
        run = Path(run_dir)
        payload = run / 'synthetic.txt'
        payload.write_bytes(b'completed synthetic run\n' * 20)
        meta = run.with_suffix('.meta')
        archive = run.with_suffix('.tar.gz')
        legacy = run.with_suffix('.tar')
        recovered = run.with_suffix('.recovered.tar.gz')
        try:
            meta.write_text('driver_exit=0\n')
            legacy.write_bytes(b'interrupted earlier tar')
            env = os.environ | {'PATH': str(work)}
            kwargs = dict(args=['/bin/sh', str(exporter), str(run)], capture_output=True)
            failure = subprocess.run(**kwargs, env=env | {'BREAK_COMPRESSION': '1'})
            assert failure.returncode and b'SHADOW5_BEGIN' not in failure.stdout and not archive.exists(), failure
            assert legacy.read_bytes() == b'interrupted earlier tar'
            assert payload.read_bytes() == b'completed synthetic run\n' * 20
            interrupted = subprocess.run(**kwargs, env=env | {'BREAK_STREAM': '1'})
            assert interrupted.returncode and b'SHADOW5_END' not in interrupted.stdout, interrupted
            saved = archive.read_bytes()
            first = subprocess.run(**kwargs, env=env, check=True).stdout
            second = subprocess.run(**kwargs, env=env | {'BREAK_COMPRESSION': '1'}, check=True).stdout
            assert first == second and archive.read_bytes() == saved
            capture = work / 'capture.txt'
            capture.write_bytes(first)
            decoded = work / 'decoded.tar.gz'
            subprocess.run([sys.executable, decoder, capture, decoded], check=True)
            assert decoded.read_bytes() == saved
            with tarfile.open(decoded) as bundle:
                assert bundle.extractfile(f'{run.name}/synthetic.txt').read() == payload.read_bytes()
                assert bundle.extractfile(meta.name).read() == meta.read_bytes()
            # The old exporter may have created the gzip but died before adding its trailer.
            truncated = saved[:-8]
            archive.write_bytes(truncated)
            process_args = kwargs["args"]
            with subprocess.Popen(process_args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as first:
                concurrent = subprocess.run(**kwargs, env=env, check=True)
                recovered_first, stderr = first.communicate()
                assert first.returncode == 0, stderr
            assert concurrent.stdout == recovered_first
            recovered_second = subprocess.run(**kwargs, env=env | {'BREAK_COMPRESSION': '1'}, check=True).stdout
            assert recovered_first == recovered_second
            assert archive.read_bytes() == truncated
            with tarfile.open(recovered) as bundle:
                assert bundle.extractfile(f'{run.name}/synthetic.txt').read() == payload.read_bytes()
            # Even a wire frame with the right length and cksum must be rejected if gzip is truncated.
            crc, length = subprocess.check_output(["cksum"], input=truncated).split()
            body = base64.b64encode(truncated)
            payload_lines = b"\n".join(body[i:i + 76] for i in range(0, len(body), 76))
            capture.write_bytes(b"@@SHADOW5_BEGIN " + crc + b" " + length + b"@@\n" + payload_lines + b"\n@@SHADOW5_END@@\n")
            out = work / 'should-not-exist.tar.gz'
            rejected = subprocess.run([sys.executable, decoder, capture, out], text=True, capture_output=True)
            assert rejected.returncode and 'Invalid gzip archive' in rejected.stderr and not out.exists()
        finally:
            for path in (meta, archive, legacy, recovered):
                path.unlink(missing_ok=True)
print('PASS: compression interruption, legacy partial tar, UART interruption, byte-identical retry, missing gzip trailer recovery, and decoder integrity.')
