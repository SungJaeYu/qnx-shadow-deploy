#!/usr/bin/env python3
"""Verify the Issue 5 kit's target shell dependencies in the extracted image."""
from pathlib import Path
import subprocess
import sys

tree = Path(sys.argv[1])
entries = Path(sys.argv[2]).read_text()
toybox = tree / "usr/bin/toybox"
symbols = subprocess.check_output(["ntoaarch64-nm", str(toybox)], text=True)
applets = {line.split()[-1] for line in symbols.splitlines() if line.strip()}
for command in ("cksum", "base64", "tar", "gzip", "ln", "mktemp", "rm"):
    assert f"{command}_main" in applets, command
linked_applets = {
    "egrep": "grep", "sed": "sed", "cat": "cat", "mkdir": "mkdir",
    "mkfifo": "mkfifo", "rm": "rm", "sleep": "sleep", "tail": "tail", "uname": "uname",
}
for command, implementation in linked_applets.items():
    assert f" bin/{command} -> /usr/bin/toybox\n" in entries, command
    assert f"{implementation}_main" in applets, command
for command in ("usr/bin/gawk", "sbin/devb-ram", "sbin/mkqnx6fs", "bin/mount"):
    assert (tree / command).stat().st_mode & 0o111, command
assert " usr/bin/awk -> /usr/bin/gawk\n" in entries
assert " bin/waitfor -> /bin/on\n" in entries
assert " bin/sh -> /bin/ksh\n" in entries
for script in ("run.sh", "board-smoke.sh"):
    path = tree / "opt/issue5" / script
    assert "dirname" not in path.read_text(), path
print("Verified embedded Issue 5 shell commands and Toybox applet functions.")
