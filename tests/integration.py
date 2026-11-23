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


class ProcessIntegration(unittest.TestCase):
    def run_scenario(self, scenario, expected_phase, expected_event, code=0):
        result = subprocess.run(
            [DEMO, "--worker", WORKER, "--scenario", scenario, "--samples", "22",
             "--qualify", "3", "--probation", "8", "--period-ms", "30", "--deadline-ms", "25"],
            capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, code, result.stderr + result.stdout[-1200:])
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[0]["backend"], "host-posix-pipes")
        self.assertNotEqual(rows[0]["baseline_pid"], rows[0]["candidate_pid"])
        for pid in [rows[0]["baseline_pid"], rows[0]["candidate_pid"]]:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        ticks = [row for row in rows if row["type"] == "sample"]
        self.assertEqual([row["sequence"] for row in ticks], list(range(1, len(ticks) + 1)))
        self.assertEqual(rows[-1]["phase"], expected_phase)
        self.assertIn(expected_event, [row["event"] for row in ticks])
        self.assertEqual(rows[-1]["outputs"], sum(row["output"] is not None for row in ticks))
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

    def test_promote(self):
        rows = self.run_scenario("promote", "committed", "candidate_committed")
        promotion = next(row for row in rows if row.get("event") == "candidate_promoted")
        self.assertEqual(promotion["sequence"], 4)

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
        self.run_scenario("rollback", "rolled_back", "candidate_rolled_back")

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
