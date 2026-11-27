#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest


BUILD = Path(sys.argv.pop(1)).resolve()
PERIOD_MS = 150
DEADLINE_MS = 100


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from live_range_example import monotonic_ns
from verify_qnx_logs import validate_continuity


def frame(sequence, acquired_ns=None, range_mm=10250):
    if acquired_ns is None:
        acquired_ns = monotonic_ns()
    return f"mm {sequence} {acquired_ns} {range_mm}\n".encode()


class LiveRangeTest(unittest.TestCase):
    def run_broker(self, produce, samples=3, split=True):
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        options = ["--output-fd", str(output_write)] if split else []
        inherited = (input_read, output_write) if split else (input_read,)
        with subprocess.Popen(
            [BUILD / "shadow-demo", "--worker", BUILD / "shadow-worker",
             "--scenario", "promote", "--samples", str(samples), "--period-ms", str(PERIOD_MS),
             "--deadline-ms", str(DEADLINE_MS), "--phase-timing", "--live-range-fd",
             str(input_read), *options],
            pass_fds=inherited, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as broker:
            os.close(input_read)
            os.close(output_write)
            with os.fdopen(input_write, "wb", buffering=0) as writer, os.fdopen(output_read, "rb") as output:
                try:
                    meta = json.loads(broker.stdout.readline())
                    self.assertEqual(meta["input_source"], "live-range-mm")
                    produce(writer)
                    events, errors = broker.communicate(timeout=4)
                    return (broker.returncode, [json.loads(line) for line in events.splitlines()],
                            errors.decode(), [json.loads(line) for line in output])
                finally:
                    if broker.poll() is None:
                        broker.kill()

    def test_fragmentation_and_original_acquisition_budget(self):
        acquired = []

        def produce(writer):
            for sequence, mm in enumerate((10250, 10275, 10300), 1):
                acquired.append(monotonic_ns())
                record = frame(sequence, acquired[-1], mm)
                writer.write(record[:10])
                time.sleep(0.005)
                writer.write(record[10:])
                if sequence < 3:
                    time.sleep(PERIOD_MS / 1000)

        code, events, errors, _ = self.run_broker(produce, split=False)
        self.assertEqual(code, 0, errors)
        rows = events[:-1]
        self.assertEqual([row["sequence"] for row in rows], [1, 2, 3])
        self.assertEqual([row["input"] for row in rows], [10.25, 10.275, 10.3])
        self.assertEqual([row["acquired_ns"] for row in rows], acquired)
        self.assertTrue(all(row["truth"] is None for row in rows))
        self.assertTrue(all(row["baseline"]["position"] is not None and
                            row["candidate"]["position"] is not None for row in rows))
        acquired.clear()
        code, events, errors, authority = self.run_broker(produce)
        self.assertEqual(code, 0, errors)
        summary = events[-1]
        self.assertEqual([row["sequence"] for row in authority], [1, 2, 3])
        self.assertEqual([row["deadline_ns"] for row in authority],
                         [stamp + DEADLINE_MS * 1000000 for stamp in acquired])
        self.assertEqual(summary["delivered_outputs"], 3)
        self.assertIsNone(summary["input_error"])

    def test_fresh_queued_and_sparse_records_keep_producer_timestamps(self):
        acquired = []

        def produce(writer):
            for delay in (0, (PERIOD_MS - 30) / 1000, (PERIOD_MS + 40) / 1000):
                time.sleep(delay)
                acquired.append(monotonic_ns())
                writer.write(frame(len(acquired), acquired[-1]))

        code, rows, errors, _ = self.run_broker(produce, split=False)
        self.assertEqual(code, 0, errors)
        self.assertEqual([row["acquired_ns"] for row in rows[:-1]], acquired)
        self.assertGreater(rows[1]["scheduled_release_offset_ns"], rows[1]["acquired_offset_ns"])
        self.assertEqual(rows[1]["release_lateness_ms"], 0)
        self.assertGreater(rows[2]["acquired_offset_ns"], rows[2]["scheduled_release_offset_ns"])
        meta = {"period_ms": PERIOD_MS, "deadline_ms": DEADLINE_MS, "requested_inputs": 3,
                "input_source": "live-range-mm", "release_policy": "acquisition-relative"}
        self.assertTrue(validate_continuity(meta, rows[-1], rows[:-1]))

    def test_invalid_streams_produce_no_output(self):
        cases = (
            ("invalid_units", lambda w: w.write(b"m 1 100 10\n")),
            ("invalid_record", lambda w: w.write(frame(1, range_mm=-1))),
            ("invalid_record", lambda w: w.write(frame(1, acquired_ns=2**64))),
            ("out_of_range", lambda w: w.write(frame(1, range_mm=1000001))),
            ("sequence", lambda w: w.write(frame(2))),
            ("timestamp_order", lambda w: w.write(frame(1, acquired_ns=0))),
            ("future", lambda w: w.write(frame(1, acquired_ns=monotonic_ns() + 10**9))),
            ("stale", lambda w: w.write(frame(1, acquired_ns=monotonic_ns() - 10**9))),
            ("oversize", lambda w: w.write(b"x" * 64)),
            ("missing", lambda w: None),
            ("partial_timeout", lambda w: w.write(b"mm 1 ")),
            ("disconnected", lambda w: w.close()),
            ("truncated", lambda w: (w.write(b"mm 1 "), w.close())),
        )
        for reason, produce in cases:
            with self.subTest(reason=reason):
                code, rows, errors, authority = self.run_broker(produce)
                self.assertEqual(code, 5, errors)
                self.assertEqual(authority, [])
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["input_error"], "live_range_" + reason)
                self.assertEqual(rows[0]["phase"], "failed")
                self.assertEqual(rows[0]["unprocessed_inputs"], 3)
                self.assertIn("expected_sequence=1", errors)

    def test_stops_after_initial_output_on_reused_timestamp(self):
        def produce(writer):
            stamp = monotonic_ns()
            writer.write(frame(1, stamp))
            writer.write(frame(2, stamp))

        code, rows, errors, authority = self.run_broker(produce)
        self.assertEqual(code, 5, errors)
        self.assertEqual([row["sequence"] for row in authority], [1])
        self.assertEqual(rows[-1]["input_error"], "live_range_timestamp_order")
        self.assertEqual(rows[-1]["unprocessed_inputs"], 2)
        self.assertIn("expected_sequence=2", errors)

    def test_rejects_regular_file_and_write_only_descriptors(self):
        read_fd, write_fd = os.pipe()
        try:
            with Path(__file__).open("rb") as regular:
                for fd in (regular.fileno(), write_fd):
                    result = subprocess.run(
                        [BUILD / "shadow-demo", "--live-range-fd", str(fd)],
                        pass_fds=(fd,), capture_output=True, timeout=3,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(b"dedicated read-only pipe", result.stderr)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_rejects_stderr_alias_before_writing(self):
        read_fd, write_fd = os.pipe()
        try:
            for options in ([], ["--help"], ["--invalid-option"]):
                result = subprocess.run(
                    [BUILD / "shadow-demo", *options, "--live-range-fd", str(read_fd)],
                    pass_fds=(read_fd,), stdout=subprocess.PIPE, stderr=write_fd, timeout=3,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
            os.set_blocking(read_fd, False)
            with self.assertRaises(BlockingIOError):
                os.read(read_fd, 1)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_rejects_read_write_alias(self):
        read_fd, write_fd = os.pipe()
        try:
            result = subprocess.run([BUILD / "shadow-demo", "--live-range-fd", str(read_fd),
                                     "--output-fd", str(write_fd), "--no-telemetry"],
                                    pass_fds=(read_fd, write_fd), capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"separate input pipe", result.stderr)
        finally:
            os.close(read_fd)
            os.close(write_fd)


if __name__ == "__main__":
    unittest.main()
