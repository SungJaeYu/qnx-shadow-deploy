#!/usr/bin/env python3
"""Translate broker JSONL into LED state changes on a separate process."""
import argparse
import json
from pathlib import Path
import signal
import subprocess
import sys

from board_relay import frames, valid_meta, valid_sample

PHASES = ("idle", "warming", "shadowing", "probation", "committed", "rejected", "rolled_back", "failed")


class LedEvents:
    def __init__(self, hold_samples):
        self.hold_samples = hold_samples
        self.reset()

    def reset(self, active=False):
        self.sequence = 0
        self.hold_until = 0
        self.active = active
        self.failed = False
        return "off"

    def accept(self, row):
        kind = row.get("type")
        if kind == "meta" and valid_meta(row):
            return self.reset(active=True)
        if kind == "summary" and self.active:
            return self.reset()
        if kind == "deployment" and self.active:
            return None
        phase = row.get("phase")
        if (kind != "sample" or not self.active or self.failed or not valid_sample(row)
                or row["sequence"] <= self.sequence or phase not in PHASES):
            raise ValueError("unexpected broker row")
        self.sequence = row["sequence"]
        if phase == "failed":
            self.failed = True
            self.hold_until = 0
            return "off"
        if phase == "rolled_back" and row.get("event") in ("candidate_rolled_back", "candidate_cancelled"):
            self.hold_until = self.sequence + self.hold_samples
        if self.sequence < self.hold_until:
            return "rollback"
        return "qualification" if phase in ("warming", "shadowing") else "normal"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hold-samples", type=int, default=10)
    parser.add_argument("--adapter", type=Path, help="Absolute path to LED adapter executable (default: JSON dry-run)")
    args = parser.parse_args()
    if not 1 <= args.hold_samples <= 1000:
        parser.error("hold-samples must be in 1..1000")
    if args.adapter is not None and not args.adapter.is_absolute():
        parser.error("adapter path must be absolute")

    def stop(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    state = None
    leds = LedEvents(args.hold_samples)

    def set_state(next_state):
        nonlocal state
        if next_state is None or next_state == state:
            return
        if args.adapter is not None:
            state = None  # An adapter can change hardware before returning an error.
            subprocess.run([str(args.adapter), next_state], timeout=1, check=True, stdin=subprocess.DEVNULL)
        else:
            print(json.dumps({"type": "led", "state": next_state}), flush=True)
        state = next_state

    result = 0
    try:
        set_state("off")
        for row, error in frames(sys.stdin.buffer):
            if error:
                raise ValueError(error)
            set_state(leds.accept(row))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"LED consumer: {exc}", file=sys.stderr)
        result = 2
    finally:
        try:
            set_state(leds.reset())
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"LED reset failed: {exc}", file=sys.stderr)
            result = 2
    return result


if __name__ == "__main__":
    sys.exit(main())
