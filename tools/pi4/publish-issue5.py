#!/usr/bin/env python3
"""Swap a verified Issue 5 image directory with rollback on rename failure."""
from pathlib import Path
import shutil
import signal
import sys
import tempfile

stage, board = map(Path, sys.argv[1:])
if not board.exists():
    stage.rename(board)
else:
    # Leave the previous verified directory intact until staging is complete.
    # Mask catchable termination between the renames so rollback can finish.
    signals = {signal.SIGHUP, signal.SIGINT, signal.SIGTERM}
    backup = Path(tempfile.mkdtemp(prefix="pi4-previous.", dir=board.parent))
    previous = backup / board.name
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    try:
        board.rename(previous)
        try:
            stage.rename(board)
        except BaseException:
            previous.rename(board)
            raise
        shutil.rmtree(previous)
    finally:
        if not previous.exists():
            backup.rmdir()
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
