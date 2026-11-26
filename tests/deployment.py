"""Attach separately built artifacts while real baseline processes continue serving."""
import json
import os
import shutil
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

    def test_late_attachment_crash_rollback_and_retry(self):
        self.check_lifecycle_run("lifecycle_checks.sh")

    def test_loaded_lifecycle_crash_and_retry(self):
        self.check_lifecycle_run("lifecycle_loaded_checks.sh", "--loaded")

    def check_lifecycle_run(self, driver, *verify_options):
        tools = Path(__file__).resolve().parents[1] / "tools"
        directory = Path(tempfile.mkdtemp(prefix="lifecycle-", dir=BUILD))
        evidence = directory / "evidence"
        try:
            command = ["sh", str(tools / driver), str(BUILD), str(evidence)]
            with subprocess.Popen(command, start_new_session=True) as runner:
                try:
                    code = runner.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    runner.terminate()
                    try:
                        runner.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        os.killpg(runner.pid, signal.SIGKILL)
                    raise
                if code:
                    raise subprocess.CalledProcessError(code, command)
            subprocess.run([sys.executable, str(tools / "verify_lifecycle.py"), "--functional-only",
                            *verify_options, str(evidence)], check=True, timeout=5)
            if verify_options:
                manifest = evidence / "candidate-crash.manifest"
                manifest.write_text(manifest.read_text().replace("fault_after=38", "fault_after=39"))
                result = subprocess.run([sys.executable, str(tools / "verify_lifecycle.py"),
                                         "--functional-only", "--loaded", str(evidence)],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("injected crash sequence", result.stderr)
            else:
                self.check_lifecycle_verifier_rejects_modified_evidence(tools, evidence)
        except (subprocess.SubprocessError, OSError, AssertionError) as error:
            self.fail(f"{error}; lifecycle evidence preserved at {directory}")
        else:
            shutil.rmtree(directory)

    def check_lifecycle_verifier_rejects_modified_evidence(self, tools, evidence):
        synthetic = evidence.parent / "synthetic"
        shutil.copytree(evidence, synthetic)
        command = [sys.executable, str(tools / "verify_lifecycle.py"), "--functional-only", str(synthetic)]

        def rejects(message):
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(message, result.stderr)

        marker = synthetic / "retirement"
        original_marker = marker.read_text()
        marker.write_text("PASS child retirement: baseline=1 first=2 second=3\n")
        rejects("retirement PIDs do not match")
        marker.write_text(original_marker)

        trace = synthetic / "lifecycle.jsonl"
        original_trace = trace.read_text()
        for field, invalid in (("generation", 2), ("phase", "probation"), ("candidate_pid", -1)):
            rows = [json.loads(line) for line in original_trace.splitlines()]
            promote = next(row for row in rows if row.get("event") == "candidate_promoted")
            qualification = next(row for row in rows if row.get("sequence") == promote["sequence"] - 1)
            qualification[field] = invalid
            trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
            rejects("generation, phase or candidate PID chronology")

        rows = [json.loads(line) for line in original_trace.splitlines()]
        stage = next(row for row in rows if row.get("event") == "stage_requested")
        ready = next(row for row in rows if row.get("event") == "replay_complete")
        ready["after_sequence"] = ready["through_sequence"] = stage["after_sequence"]
        trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
        rejects("stage, replay and promotion boundaries out of order")

        rows = [json.loads(line) for line in original_trace.splitlines()]
        rows[-1]["skipped_nominal_slots"] += 1
        trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
        rejects("invalid continuity field: skipped_nominal_slots")

        rows = [json.loads(line) for line in original_trace.splitlines()]
        rows[0]["backend"] = "qnx-native-ipc"
        trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
        broker = int((synthetic / "broker-pid").read_text())
        baseline = rows[0]["baseline_pid"]
        first, second = [row["candidate_pid"] for row in rows if row.get("event") == "candidate_promoted"]
        ordinary_fds = " 0 1 control\n 1 1 log\n 2 1 log\n 4 1 pipe\n 5 1 pipe\n"
        (synthetic / "baseline.fds").write_text(ordinary_fds + f" 0s {baseline} native\n")
        shutil.copyfile(synthetic / "baseline.fds", synthetic / "rollback.fds")
        (synthetic / "probation.fds").write_text(ordinary_fds +
                                                f" 0s {baseline} native\n 1s {first} native\n")
        committed = synthetic / "committed.fds"
        committed.write_text(ordinary_fds + f" 2s {second} native\n")
        observer = max(broker, baseline, first, second) + 1
        system_processes = "1 system\n2 service\n"
        (synthetic / "baseline.processes").write_text(
            system_processes + f"{broker} broker\n{baseline} worker\n{observer} /bin/pidin ar\n")
        finished = synthetic / "finished.processes"
        finished.write_text(system_processes + f"{observer + 1} /bin/pidin ar\n")
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=5)

        committed.write_text(committed.read_text() + " 6 1 leaked-pipe\n")
        rejects("commit left extra descriptors")
        committed.write_text(ordinary_fds + f" 2s {second} native\n")
        finished.write_text("1 system\n")
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 77, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["native_cleanup"]["verdict"], "inconclusive")

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
