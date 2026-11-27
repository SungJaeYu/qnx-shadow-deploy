"""Verify local hook results against a running broker with stdin disconnected."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest


BUILD = Path(sys.argv.pop(1)).resolve()


class DeploymentHook(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="shc-", dir="/tmp")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.endpoint = self.root / "control"
        self.template = dict(line.split("=", 1) for line in
                             (BUILD / "candidate.manifest").read_text().splitlines())
        self.template.update(executable=str(BUILD / "shadow-candidate"), replay_samples="4")
        self.log = self.root / "broker.log"
        self.output = open(self.log, "w")
        self.addCleanup(self.output.close)

    def manifest(self, filename, **changes):
        fields = {**self.template, **changes}
        path = self.root / filename
        path.write_text("".join(f"{key}={value}\n" for key, value in fields.items()))
        return path

    def broker(self, stdout=None):
        command = [str(BUILD / "shadow-demo"), "--worker", str(BUILD / "shadow-worker"),
                   "--control-socket", str(self.endpoint), "--samples", "240", "--period-ms", "50",
                   "--deadline-ms", "40", "--qualify", "3", "--probation", "20"]
        broker = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=self.output if stdout is None else stdout)
        self.addCleanup(self.stop, broker)
        if stdout is None:
            self.wait_for(lambda r: r.get("sequence") == 8)
        return broker

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def wait_for(self, condition):
        deadline = time.monotonic() + 4
        with self.log.open() as log:
            while time.monotonic() < deadline:
                offset = log.tell()
                line = log.readline()
                if line.endswith("\n"):
                    row = json.loads(line)
                    if condition(row):
                        return row
                else:
                    log.seek(offset)
                    time.sleep(0.01)
        self.fail("broker did not reach the expected event")

    def hook(self, path, timeout_ms=4000):
        env = dict(os.environ, SHADOW_CONTROL_SOCKET=str(self.endpoint))
        return subprocess.Popen([str(BUILD / "shadowctl"), "deploy", str(path), "--wait",
                                 "--timeout-ms", str(timeout_ms)],
                                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)

    def outcome(self, process, code, outcome, generation, reason, owner):
        stdout, _ = process.communicate(timeout=6)
        self.assertEqual(process.returncode, code, stdout)
        self.assertEqual(json.loads(stdout), dict(outcome=outcome, generation=generation,
                                                  reason=reason, active_owner=owner))

    def test_reject_rollback_retry_commit_and_refusal(self):
        self.broker()
        bad = self.manifest("bad protocol.manifest", protocol=1)
        crash = self.manifest("crash.manifest", mode="crash", fault_after=25)
        good = self.manifest("good.manifest")
        self.outcome(self.hook(bad), 2, "rejected", 1, "manifest_protocol_mismatch", "baseline")
        with self.hook(crash) as pending:
            self.wait_for(lambda r: r.get("generation") == 2 and r.get("event") == "candidate_promoted")
            self.outcome(self.hook(good), 4, "refused", None, "deployment_busy", "candidate")
            self.outcome(pending, 3, "rolled_back", 2, "worker_disconnected", "baseline")
        self.outcome(self.hook(good), 0, "committed", 3, "probation_passed", "candidate")
        self.outcome(self.hook(bad), 4, "refused", None, "deployment_already_committed", "candidate")
        self.assertFalse(any(json.loads(line).get("generation") == 4
                             for line in self.log.read_text().splitlines() if line.endswith("}")))

    def test_wait_deadline_keeps_acknowledged_generation_and_does_not_cancel(self):
        self.broker()
        good = self.manifest("good.manifest")
        self.outcome(self.hook(good, 300), 5, "unknown", 1, "timeout", None)
        self.wait_for(lambda r: r.get("generation") == 1 and r.get("event") == "candidate_committed")
        self.outcome(self.hook(good), 4, "refused", None, "deployment_already_committed", "candidate")

    def test_contact_loss_after_acceptance(self):
        broker = self.broker()
        with self.hook(self.manifest("good.manifest")) as pending:
            self.wait_for(lambda r: r.get("event") == "candidate_promoted")
            self.stop(broker)
            self.outcome(pending, 5, "unknown", 1, "contact_lost", None)
        self.assertFalse(self.endpoint.exists())
        self.outcome(self.hook(self.root / "good.manifest"), 5, "unknown", None, "broker_unreachable", None)

    def test_full_stdout_pipe_does_not_block_local_deployment(self):
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        self.addCleanup(os.close, writer)
        os.set_blocking(writer, False)
        try:
            while True:
                os.write(writer, b"x" * 4096)
        except BlockingIOError:
            pass
        os.set_blocking(writer, True)
        self.broker(stdout=writer)
        ready_by = time.monotonic() + 3
        while not self.endpoint.exists() and time.monotonic() < ready_by:
            time.sleep(0.01)
        self.assertTrue(self.endpoint.exists())
        # Replay needs four completed baseline samples.
        time.sleep(0.4)
        self.outcome(self.hook(self.manifest("good.manifest")), 0, "committed", 1,
                     "probation_passed", "candidate")

    def test_fragmented_request_isolated_from_next_client(self):
        self.broker()
        good = self.manifest("good.manifest")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(self.endpoint))
            path = os.fsencode(good)
            client.sendall(path[:2])
            time.sleep(0.12)
            client.sendall(path[2:] + b"\n")
            with client.makefile("rb") as replies:
                self.assertEqual(replies.readline(), b"a 1\n")
                self.outcome(self.hook(good), 4, "refused", None, "deployment_busy", "baseline")
                self.assertEqual(replies.readline().split(maxsplit=1)[0], b"0")


if __name__ == "__main__":
    unittest.main()
