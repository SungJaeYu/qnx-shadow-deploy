#!/usr/bin/env python3
"""Run the broker with a same-host demonstration range producer."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


if sys.platform == "darwin":
    import ctypes

    # CPython 3.9 on macOS omits clock_gettime_ns. mach_absolute_time can
    # differ from CLOCK_MONOTONIC, so call Darwin's nanosecond clock directly.
    _darwin_clock = ctypes.CDLL(None).clock_gettime_nsec_np
    _darwin_clock.argtypes = [ctypes.c_int]
    _darwin_clock.restype = ctypes.c_uint64

    def monotonic_ns():
        return _darwin_clock(6)  # Darwin CLOCK_MONOTONIC
else:
    def monotonic_ns():
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def produce(fd, count, period_ms):
    with os.fdopen(fd, "wb", buffering=0) as stream:
        try:
            for sequence in range(1, count + 1):
                # Replace this example value with a driver reading and preserve
                # its real CLOCK_MONOTONIC acquisition timestamp.
                acquired_ns = monotonic_ns()
                stream.write(f"mm {sequence} {acquired_ns} 10250\n".encode("ascii"))
                if sequence < count:
                    time.sleep(period_ms / 1000)
        except BrokenPipeError:
            pass  # Broker has stopped consuming; preserve its exit status.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--period-ms", type=int, default=100)
    parser.add_argument("--deadline-ms", type=int, default=50)
    args = parser.parse_args()
    read_fd, write_fd = os.pipe()
    build = args.build_dir.resolve()
    with subprocess.Popen(
        [build / "shadow-demo", "--worker", build / "shadow-worker", "--scenario", "promote",
         "--live-range-fd", str(read_fd), "--samples", str(args.samples),
         "--period-ms", str(args.period_ms), "--deadline-ms", str(args.deadline_ms)],
        pass_fds=(read_fd,), stdout=subprocess.PIPE, text=True,
    ) as broker:
        os.close(read_fd)
        meta = broker.stdout.readline()
        if not meta:
            os.close(write_fd)
            return broker.wait()
        if json.loads(meta).get("input_source") != "live-range-mm":
            os.close(write_fd)
            broker.terminate()
            return broker.wait()
        producer = threading.Thread(target=produce, args=(write_fd, args.samples, args.period_ms))
        producer.start()
        try:
            sys.stdout.write(meta)
            for line in broker.stdout:
                sys.stdout.write(line)
            return broker.wait()
        finally:
            if broker.poll() is None:
                broker.terminate()
                broker.wait()
            producer.join()


if __name__ == "__main__":
    sys.exit(main())
