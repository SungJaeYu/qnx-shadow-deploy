"""Exercise real worker processes, failure injection, and single-source publication."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

DEMO, WORKER = map(lambda value: str(Path(value).resolve()), sys.argv[1:3])
sys.argv = [sys.argv[0]]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from verify_qnx_logs import validate_continuity


class ProcessIntegration(unittest.TestCase):
    def run_scenario(self, scenario, expected_phase, expected_event, code=0, *,
                     probation=8, trace=None):
        command = [DEMO, "--worker", WORKER, "--scenario", scenario, "--samples", "22",
                   "--qualify", "3", "--probation", str(probation), "--period-ms", "100",
                   "--deadline-ms", "75"]
        if trace is not None:
            command.extend(["--sensor-trace", str(trace)])
        result = subprocess.run(command, capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, code, result.stderr + result.stdout[-1200:])
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[0]["backend"], "host-posix-pipes")
        self.assertNotEqual(rows[0]["baseline_pid"], rows[0]["candidate_pid"])
        for pid in [rows[0]["baseline_pid"], rows[0]["candidate_pid"]]:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        ticks = [row for row in rows if row["type"] == "sample"]
        self.assertEqual([row["sequence"] for row in ticks], list(range(1, len(ticks) + 1)))
        self.assertEqual(rows[0]["release_policy"], "guarded-fixed-grid")
        self.assertEqual(rows[-1]["phase"], expected_phase)
        self.assertIn(expected_event, [row["event"] for row in ticks])
        self.assertEqual(rows[-1]["outputs"], sum(row["output"] is not None for row in ticks))
        summary = rows[-1]
        self.assertEqual(rows[0]["requested_inputs"], 22)
        self.assertEqual(summary["acquired_inputs"], len(ticks))
        self.assertEqual(summary["unprocessed_inputs"], 22 - len(ticks))
        self.assertEqual(summary["selected_outputs"], summary["outputs"])
        self.assertEqual(summary["missing_selected_outputs"], len(ticks) - summary["outputs"])
        self.assertEqual(summary["decision_deadline_misses"], summary["publication_misses"])
        self.assertEqual(summary["skipped_nominal_slots"], sum(r["skipped_nominal_slots"] for r in ticks))
        self.assertEqual(summary["last_nominal_slot"], ticks[-1]["nominal_slot"])
        self.assertEqual(summary["max_scheduling_lateness_ms"], max(r["release_lateness_ms"] for r in ticks))
        self.assertIsNone(ticks[0]["acquisition_interval_ms"])
        self.assertEqual(summary["external_delivery"], "unmeasured")
        self.assertTrue(validate_continuity(rows[0], summary, ticks))
        for row in ticks:
            if row["output"] is not None:
                self.assertEqual(row["output"], row[row["source"]]["position"])
                self.assertFalse(row["publication_miss"])
                self.assertEqual(row[row["source"] + "_check"], "ok")
        if expected_phase == "rejected":
            self.assertTrue(all(row["source"] != "candidate" for row in ticks))
        if expected_phase == "rolled_back":
            rollback = next(row for row in ticks if row["event"] == "candidate_rolled_back")
            self.assertEqual(rollback["source"], "baseline")
        return rows

    def test_phase_timing_is_opt_in_and_preserves_sample_telemetry(self):
        command = [DEMO, "--worker", WORKER, "--scenario", "reject-protocol",
                   "--samples", "3", "--period-ms", "100", "--deadline-ms", "75"]
        normal = subprocess.run(command, capture_output=True, text=True, timeout=5)
        timed = subprocess.run(command + ["--phase-timing"], capture_output=True, text=True, timeout=5)
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertEqual(timed.returncode, 0, timed.stderr)
        self.assertEqual(normal.stderr, "")
        normal_rows = [json.loads(line) for line in normal.stdout.splitlines()]
        timed_rows = [json.loads(line) for line in timed.stdout.splitlines()]
        self.assertEqual([row.keys() for row in normal_rows], [row.keys() for row in timed_rows])
        self.assertEqual([row.get("event") for row in normal_rows],
                         [row.get("event") for row in timed_rows])
        phases = [json.loads(line) for line in timed.stderr.splitlines()]
        self.assertEqual([row["sequence"] for row in phases], [1, 2, 3])
        rejections = [row for row in timed_rows if row.get("event") == "candidate_rejected"]
        self.assertEqual([row["sequence"] for row in rejections], [2])
        self.assertEqual(rejections[0]["reason"], "protocol_mismatch")
        rejection_sequence = rejections[0]["sequence"]
        for record in phases:
            self.assertEqual(record["type"], "phase_timing")
            self.assertEqual(record["ipc_deadline_ns"] - record["acquired_ns"], 56_250_000)
            self.assertLessEqual(record["selected_ns"], record["flush_start_ns"])
            self.assertLessEqual(record["flush_start_ns"], record["flush_end_ns"])
            self.assertIsNone(record["authority_write_start_ns"])
            self.assertIsNone(record["authority_write_end_ns"])
            self.assertIsNone(record["authority_write_outcome"])
            for worker in (record["baseline"], record["candidate"]):
                if worker is None:
                    continue
                direct = timed_rows[0]["backend"] == "qnx-native-ipc" and record["candidate"] is None
                if direct:
                    exchange_starts = [worker["direct_start_ns"]]
                    collection = []
                    for key in ("submit_ns", "dispatch_ns", "future_wait_ns", "future_return_ns"):
                        self.assertIsNone(worker[key])
                else:
                    self.assertIsNone(worker["direct_start_ns"])
                    exchange_starts = [worker["submit_ns"], worker["dispatch_ns"]]
                    collection = [worker["future_return_ns"]]
                    self.assertLessEqual(worker["submit_ns"], worker["future_wait_ns"])
                    self.assertLessEqual(worker["future_wait_ns"], worker["future_return_ns"])
                sequence = [record["acquired_ns"], *exchange_starts, worker["ipc_start_ns"],
                            worker["ipc_end_ns"], worker["completed_ns"], *collection, record["selected_ns"]]
                self.assertEqual(sequence, sorted(sequence))
                self.assertIsNone(worker["pre_expiry_ns"])
            self.assertIsNone(record["baseline"]["retirement_start_ns"])
        for record in phases:
            candidate = record["candidate"]
            if record["sequence"] < rejection_sequence:
                self.assertIsNotNone(candidate)
                self.assertIsNone(candidate["retirement_start_ns"])
                self.assertIsNone(candidate["retirement_end_ns"])
            elif record["sequence"] == rejection_sequence:
                self.assertIsNotNone(candidate)
                self.assertLessEqual(record["flush_end_ns"], candidate["retirement_start_ns"])
                self.assertLessEqual(candidate["retirement_start_ns"], candidate["retirement_end_ns"])
            else:
                self.assertIsNone(candidate)

    def test_interrupted_run_has_no_missing_tail(self):
        command = [DEMO, "--worker", WORKER, "--scenario", "promote", "--samples", "20",
                   "--period-ms", "200", "--deadline-ms", "150"]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
            first = [json.loads(proc.stdout.readline()) for _ in range(2)]
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 130, stderr)
        rows = first + [json.loads(line) for line in stdout.splitlines()]
        summary = rows[-1]
        self.assertEqual(summary["acquired_inputs"], len(rows) - 2)
        self.assertGreater(summary["unprocessed_inputs"], 0)
        self.assertTrue(validate_continuity(rows[0], summary, rows[1:-1]))
        self.assertEqual(summary["unprocessed_inputs"], 20 - summary["acquired_inputs"])
        self.assertEqual(summary["missing_selected_outputs"], 0)
        self.assertEqual(summary["skipped_nominal_slots"], sum(row["skipped_nominal_slots"] for row in rows[1:-1]))
        if summary["acquired_inputs"] == 1:
            self.assertIsNone(summary["max_acquisition_interval_ms"])
            self.assertIsNone(summary["max_selected_interval_ms"])
            self.assertIsNone(summary["effective_selected_rate_hz"])

    def test_promote(self):
        rows = self.run_scenario("promote", "committed", "candidate_committed")
        promotion = next(row for row in rows if row.get("event") == "candidate_promoted")
        self.assertEqual(promotion["sequence"], 4)
        self.assertEqual(rows[-1]["worker_retirement_samples"], 1)
        self.assertGreaterEqual(rows[-1]["max_worker_retirement_ns"], 0)

    def test_rejections(self):
        expected = {"units": "position_out_of_range", "delay": "deadline_exceeded",
                    "nan": "non_finite_output", "stale": "stale_or_mismatched_output",
                    "protocol": "protocol_mismatch", "switch": "switch_discontinuity"}
        for suffix, reason in expected.items():
            with self.subTest(scenario=suffix):
                rows = self.run_scenario("reject-" + suffix, "rejected", "candidate_rejected")
                event = next(row for row in rows if row.get("event") == "candidate_rejected")
                self.assertEqual(event["reason"], reason)

    def test_rollback(self):
        rows = self.run_scenario("rollback", "rolled_back", "candidate_rolled_back")
        self.assertEqual(rows[-1]["worker_retirement_samples"], 1)
        self.assertGreaterEqual(rows[-1]["max_worker_retirement_ns"], 0)

    def test_shutdown_only_cleanup_is_excluded(self):
        rows = self.run_scenario("promote", "probation", "candidate_promoted", probation=40)
        self.assertEqual(rows[-1]["worker_retirement_samples"], 0)
        self.assertIsNone(rows[-1]["max_worker_retirement_ns"])

    def test_frozen_candidate_rolls_back_on_a_moving_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "ramp.mm"
            trace.write_text("".join(f"{10000 + index * 200}\n" for index in range(22)))
            rows = self.run_scenario("semantic-freeze", "rolled_back", "candidate_rolled_back",
                                     probation=18, trace=trace)
        rollback = next(row for row in rows if row.get("event") == "candidate_rolled_back")
        self.assertEqual(rollback["reason"], "measurement_residual_exceeded")
        self.assertEqual(rollback["candidate_check"], "measurement_residual_exceeded")
        self.assertTrue(any(row.get("event") == "candidate_promoted" for row in rows))

    def test_frozen_candidate_on_stationary_input_remains_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "stationary.mm"
            trace.write_text("10000\n" * 22)
            self.run_scenario("semantic-freeze", "committed", "candidate_committed", trace=trace)

    def test_biased_candidate_rolls_back_after_promotion(self):
        rows = self.run_scenario("semantic-bias", "rolled_back", "candidate_rolled_back")
        rollback = next(row for row in rows if row.get("event") == "candidate_rolled_back")
        self.assertEqual(rollback["reason"], "measurement_residual_exceeded")
        self.assertEqual(rollback["sequence"], 9)
        self.assertTrue(any(row.get("event") == "candidate_promoted" for row in rows))

    def test_no_valid_output(self):
        for scenario in ["baseline-failure", "no-fallback", "post-commit-failure"]:
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario, "failed", "output_stopped", 3)

    def test_missing_worker_and_invalid_arguments(self):
        for args in [["--worker", "/nonexistent/shadow-worker"], ["--qualify", "0"],
                     ["--samples", "-1"], ["--deadline-ms", "1000"], ["--unknown", "x"]]:
            result = subprocess.run([DEMO] + args, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)

    def test_incomplete_and_unresponsive_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "worker"
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o700)
            result = subprocess.run([DEMO, "--worker", str(script)], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"handshake failed", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
