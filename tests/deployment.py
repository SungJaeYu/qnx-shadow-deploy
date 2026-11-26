"""Attach separately built artifacts while real baseline processes continue serving."""
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

BUILD = Path(sys.argv[1]).resolve()
sys.argv = [sys.argv[0]]


class DeploymentIntegration(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.template = (BUILD / "candidate.manifest").read_text().replace(
            "executable=./shadow-candidate", f"executable={BUILD / 'shadow-candidate'}").replace(
            "replay_samples=16", "replay_samples=4")

    def tearDown(self):
        self.directory.cleanup()

    def manifest(self, name="candidate", **fields):
        entries = dict(line.split("=", 1) for line in self.template.splitlines())
        entries.update({key: str(value) for key, value in fields.items()})
        path = self.root / (name + ".manifest")
        path.write_text("".join(f"{key}={value}\n" for key, value in entries.items()))
        return path

    def run_live(self, manifest=None, samples=30, respond=None):
        args = [str(BUILD / "shadow-demo"), "--worker", str(BUILD / "shadow-worker"),
                "--samples", str(samples), "--period-ms", "200", "--deadline-ms", "150",
                "--qualify", "3", "--probation", "8", "--control-stdin"]
        if manifest:
            args += ["--candidate-manifest", str(manifest), "--stage-at", "5"]
        with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, bufsize=1,
                              start_new_session=True) as process:
            def terminate_group():
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            timer = threading.Timer(12, terminate_group)
            timer.start()
            rows = []
            try:
                for line in process.stdout:
                    row = json.loads(line)
                    rows.append(row)
                    command = respond(row) if respond else None
                    if command:
                        process.stdin.write(command + "\n")
                        process.stdin.flush()
                process.stdin.close()
                code = process.wait(timeout=4)
                if code != 0:
                    (BUILD / "deployment-failure.jsonl").write_text(
                        "".join(json.dumps(row) + "\n" for row in rows))
                self.assertEqual(code, 0, process.stderr.read() + json.dumps(rows[-5:]))
            finally:
                timer.cancel()
                if process.poll() is None:
                    terminate_group()
        self.assertEqual(rows[-1]["type"], "summary")
        self.assertEqual(rows[-1]["outputs"], samples, json.dumps(rows[-4:]))
        self.assertEqual(rows[-1]["missing_outputs"], 0)
        ticks = [row for row in rows if row["type"] == "sample"]
        self.assertEqual([r["sequence"] for r in ticks], list(range(1, samples + 1)))
        baseline_pid = rows[0]["baseline_pid"]
        for tick in ticks:
            if tick["baseline_pid"] > 0:
                self.assertEqual(tick["baseline_pid"], baseline_pid)
            self.assertEqual(tick["output"], tick[tick["source"]]["position"])
        pids = {r.get("candidate_pid", -1) for r in rows} | {baseline_pid}
        for pid in pids - {-1}:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        return rows

    def test_separate_artifact_replays_then_qualifies_live(self):
        rows = self.run_live(self.manifest())
        ready = next(r for r in rows if r.get("event") == "replay_complete")
        promoted = next(r for r in rows if r.get("event") == "candidate_promoted")
        self.assertEqual(ready["through_sequence"], ready["after_sequence"])
        self.assertGreaterEqual(ready["replayed"], 4)
        self.assertEqual(promoted["sequence"], ready["after_sequence"] + 4)
        self.assertEqual(rows[-1]["phase"], "committed")
        expected = None
        for row in (r for r in rows if r["type"] == "sample" and r["sequence"] >= 2):
            expected = row["input"] if expected is None else expected + .65 * (row["input"] - expected)
            if row["candidate"] is not None:
                self.assertAlmostEqual(row["candidate"]["position"], expected, places=7)

    def test_replay_fault_never_gains_authority(self):
        for mode, reason in [("stale", "replay_stale_or_mismatched_output"),
                             ("delay", "replay_deadline_exceeded")]:
            with self.subTest(mode=mode):
                rows = self.run_live(self.manifest(mode=mode), samples=16)
                failure = next(r for r in rows if r.get("event") == "preparation_failed")
                self.assertEqual(failure["reason"], reason)
                self.assertTrue(all(r["source"] == "baseline" for r in rows if r["type"] == "sample"))

    def test_semantically_invalid_replay_never_gains_authority(self):
        rows = self.run_live(self.manifest(mode="offset", replay_samples=5), samples=16)
        self.assertIn("replay_measurement_residual_exceeded",
                      [row.get("reason") for row in rows if row.get("event") == "preparation_failed"])
        self.assertTrue(all(row["source"] == "baseline" for row in rows if row["type"] == "sample"))

    def test_invalid_manifest_can_be_replaced_without_baseline_restart(self):
        bad = self.manifest("bad", protocol=99)
        good = self.manifest("good")
        rows = self.run_live(bad, samples=35,
            respond=lambda row: f"stage {good}" if row.get("sequence") == 14 else None)
        self.assertTrue(any(r.get("reason") == "manifest_protocol_mismatch" for r in rows))
        promotion = next(r for r in rows if r.get("event") == "candidate_promoted")
        self.assertEqual(promotion["generation"], 2)
        self.assertEqual(rows[-1]["phase"], "committed")

    def test_cancel_probation_uses_current_fallback(self):
        rows = self.run_live(self.manifest(), respond=lambda row:
            "cancel" if row.get("event") == "candidate_promoted" else None)
        cancelled = next(r for r in rows if r.get("event") == "candidate_cancelled")
        self.assertEqual(cancelled["source"], "baseline")
        self.assertEqual(cancelled["phase"], "rolled_back")

    def test_unresponsive_startup_does_not_block_baseline(self):
        worker = self.root / "slow-worker"
        pid_file = self.root / "slow.pid"
        worker.write_text(f"#!{sys.executable}\nimport os, time\n"
                          f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(10)\n")
        worker.chmod(0o700)
        rows = self.run_live(self.manifest(executable=worker), samples=42)
        self.assertEqual(rows[-1]["phase"], "rejected")
        self.assertTrue(any(r.get("event") == "preparation_failed" for r in rows))
        self.assertGreater(sum(r.get("phase") == "warming" for r in rows), 10)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_cancel_startup_then_retry(self):
        worker = self.root / "starting-worker"
        worker.write_text(f"#!{sys.executable}\nimport os, time\ntime.sleep(.7)\n"
                          f"os.execv({str(BUILD / 'shadow-candidate')!r}, ['worker', 'good', '0'])\n")
        worker.chmod(0o700)
        good = self.manifest("good")
        rows = self.run_live(self.manifest(executable=worker), samples=40,
            respond=lambda row: "cancel" if row.get("sequence") == 7 else
                f"stage {good}" if row.get("sequence") == 20 else None)
        self.assertTrue(any(r.get("event") == "candidate_cancelled" for r in rows))
        promoted = next(r for r in rows if r.get("event") == "candidate_promoted")
        self.assertEqual(promoted["generation"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
