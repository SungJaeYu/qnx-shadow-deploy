#!/usr/bin/env python3
"""Prove a late publication failure restores all requested prior outputs."""
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from unittest.mock import patch

helper = Path(__file__).with_name("publish-images.py")
install_outputs = runpy.run_path(str(helper))["install_outputs"]

with tempfile.TemporaryDirectory() as scratch:
    root = Path(scratch)
    stage = root / "stage"
    live = root / "live"
    stage.mkdir()
    live.mkdir()
    pairs = [(stage / name, live / name) for name in ("stock", "symbols", "new", "issue")]
    untouched = live / "history"
    untouched.write_text("historical")
    for source, destination in pairs:
        if source.name == "symbols":
            source.mkdir()
            destination.mkdir()
            (source / "symbol").write_text("replacement directory")
            (destination / "symbol").write_text("previous directory")
        else:
            source.write_text("replacement")
            if source.name != "new":
                destination.write_text("previous")

    rename = Path.rename

    def fail_last(path, destination):
        if path == stage / "issue":
            raise OSError("simulated late install rename failure")
        return rename(path, destination)

    with patch.object(Path, "rename", fail_last):
        try:
            install_outputs(root, pairs)
        except OSError as error:
            assert "simulated late" in str(error)
        else:
            raise AssertionError("Expected install failure")

    assert (live / "stock").read_text() == (live / "issue").read_text() == "previous"
    assert (live / "symbols" / "symbol").read_text() == "previous directory"
    assert not (live / "new").exists()
    assert untouched.read_text() == "historical"
    assert not list(root.glob("pi4-previous.*"))

    install_outputs(root, pairs)
    assert (live / "stock").read_text() == (live / "issue").read_text() == "replacement"
    assert (live / "symbols" / "symbol").read_text() == "replacement directory"
    assert (live / "new").read_text() == "replacement"
    assert untouched.read_text() == "historical"

    lock = [sys.executable, str(helper), "lock", str(root / "build.lock")]
    hold = [sys.executable, "-c", "print('locked', flush=True); input()"]
    with subprocess.Popen(lock + hold, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as active:
        try:
            assert active.stdout.readline().strip() == "locked"
            blocked = subprocess.run(lock + [sys.executable, "-c", ""], capture_output=True, text=True)
            assert blocked.returncode == 1 and "already running" in blocked.stderr
        finally:
            active.terminate()
    subprocess.run(lock + [sys.executable, "-c", ""], check=True)

print("PASS: late publication rollback, successful replacement, and exclusive build lock.")
