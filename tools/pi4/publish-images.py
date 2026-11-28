#!/usr/bin/env python3
"""Serialize image builds and install a verified set with rollback on failure."""
import fcntl
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile


def run_with_lock(path, command):
    with Path(path).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A Pi image build is already running") from None
        os.set_inheritable(lock.fileno(), True)
        os.execvp(command[0], command)


def install_outputs(backup_parent, pairs):
    device = backup_parent.stat().st_dev
    for source, destination in pairs:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.stat().st_dev != device or destination.parent.stat().st_dev != device:
            raise ValueError(f"Staging and destination must share a filesystem: {destination}")

    backup = Path(tempfile.mkdtemp(prefix="pi4-previous.", dir=backup_parent))
    signals = {signal.SIGHUP, signal.SIGINT, signal.SIGTERM}
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    journal = []
    try:
        for index, (source, destination) in enumerate(pairs):
            previous = backup / str(index)
            if os.path.lexists(destination):
                destination.rename(previous)
            journal.append((source, destination, previous))
            source.rename(destination)
    except BaseException:
        errors = []
        for source, destination, previous in reversed(journal):
            try:
                if not os.path.lexists(source):
                    destination.rename(source)
                if os.path.lexists(previous):
                    previous.rename(destination)
            except OSError as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError(f"Rollback files remain in {backup}: {', '.join(errors)}")
        raise
    else:
        shutil.rmtree(backup)
    finally:
        if backup.exists() and not any(backup.iterdir()):
            backup.rmdir()
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


if __name__ == "__main__":
    action, path, *args = sys.argv[1:]
    if action == "lock" and args:
        run_with_lock(path, args)
    elif action == "install" and args and len(args) % 2 == 0:
        paths = list(map(Path, args))
        install_outputs(Path(path), list(zip(paths[::2], paths[1::2])))
    else:
        raise SystemExit("Usage: publish-images.py lock LOCK COMMAND... or install BACKUP_ROOT SOURCE DEST...")
