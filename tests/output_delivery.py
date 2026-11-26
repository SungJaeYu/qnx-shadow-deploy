"""Exercise the split output interface against real workers and POSIX pipes."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest


DEMO, WORKER = (str(Path(value).resolve()) for value in sys.argv[1:3])
del sys.argv[1:3]
COMMAND = [DEMO, "--worker", WORKER, "--samples", "8", "--scenario", "rollback",
           "--qualify", "2", "--probation", "4", "--period-ms", "100", "--deadline-ms", "75"]


@contextmanager
def pipe():
    reader, writer = os.pipe()
    with os.fdopen(reader, "rb", buffering=0) as source, os.fdopen(writer, "wb", buffering=0) as sink:
        yield source, sink


def records(data):
    return [json.loads(line) for line in data.splitlines()]


def fill(sink):
    os.set_blocking(sink.fileno(), False)
    written = 0
    for chunk in (b"x" * 512, b"x"):
        while True:
            count = sink.write(chunk)
            if count is None:
                break
            written += count
    return written


class OutputDelivery(unittest.TestCase):
    def run_demo(self, sink, *, command=COMMAND, extra=(), stdout=subprocess.PIPE):
        fd = sink.fileno()
        return subprocess.run(command + ["--output-fd", str(fd), *extra], pass_fds=(fd,),
                              stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.PIPE, timeout=5)

    def test_delivers_exactly_the_selected_sources_through_rollback(self):
        with pipe() as (source, sink):
            result = self.run_demo(sink)
            sink.close()
            authority = records(source.read())
        self.assertEqual(result.returncode, 0, result.stderr)
        telemetry = records(result.stdout)
        self.assertEqual(len(authority), 8)
        self.assertEqual([row["sequence"] for row in authority], list(range(1, 9)))
        self.assertEqual([row["source"] for row in authority],
                         ["baseline", "baseline", "candidate", "candidate"] + ["baseline"] * 4)
        samples = telemetry[1:-1]
        self.assertEqual([row["sequence"] for row in samples], list(range(1, 9)))
        for actual, sample in zip(authority, samples):
            self.assertEqual(actual["type"], "authoritative_output")
            self.assertEqual(actual["schema"], 1)
            self.assertEqual(actual["producer_run_id"], telemetry[0]["producer_run_id"])
            self.assertEqual(actual["position_m"], sample["output"])
            self.assertEqual(actual["source"], sample["source"])
            self.assertTrue(sample["output_delivered"])
        self.assertEqual(telemetry[-1]["selected_outputs"], 8)
        self.assertEqual(telemetry[-1]["delivered_outputs"], 8)
        self.assertEqual(telemetry[-1]["telemetry_records_dropped"], 0)

    def test_full_authority_pipe_stops_after_the_first_attempt(self):
        with pipe() as (source, sink):
            fill(sink)
            result = self.run_demo(sink)
            sink.close()
            self.assertEqual(set(source.read()), {ord("x")})
        self.assertEqual(result.returncode, 4, result.stderr)
        summary = records(result.stdout)[-1]
        self.assertEqual(summary["selected_outputs"], 1)
        self.assertEqual(summary["delivered_outputs"], 0)
        self.assertEqual(summary["unprocessed_inputs"], 7)
        self.assertIn(b"output_pipe_full", result.stderr)
        self.assertIn(b"last_selected_sequence=1 last_delivered_sequence=0", result.stderr)

    def test_closed_authority_pipe_stops_after_preceding_delivery(self):
        with pipe() as (source, sink):
            fd = sink.fileno()
            with subprocess.Popen(COMMAND + ["--output-fd", str(fd)], pass_fds=(fd,),
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) as process:
                sink.close()
                self.assertTrue(select.select([source], [], [], 3)[0])
                first = records(source.read(512))
                self.assertEqual([row["sequence"] for row in first], [1])
                source.close()
                stdout, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 4, stderr)
        self.assertEqual(records(stdout)[-1]["delivered_outputs"], 1)
        self.assertIn(b"output_reader_disconnected", stderr)
        self.assertIn(b"last_selected_sequence=2 last_delivered_sequence=1", stderr)

    def test_full_or_closed_telemetry_pipe_does_not_stall_authority(self):
        for closed in (False, True):
            with self.subTest(closed=closed), pipe() as (source, sink), pipe() as (log, log_sink):
                if closed:
                    log.close()
                else:
                    fill(log_sink)
                result = self.run_demo(sink, stdout=log_sink)
                sink.close()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([row["sequence"] for row in records(source.read())], list(range(1, 9)))
                self.assertIn(b"telemetry_records_dropped=10", result.stderr)

    def test_telemetry_reader_resumes_without_malformed_rows(self):
        with pipe() as (source, sink), pipe() as (log, log_sink):
            filler_bytes = fill(log_sink)
            fd = sink.fileno()
            with subprocess.Popen(COMMAND + ["--output-fd", str(fd)], pass_fds=(fd,),
                                  stdout=log_sink, stderr=subprocess.PIPE) as process:
                log_sink.close()
                sink.close()
                first = []
                for _ in range(2):
                    self.assertTrue(select.select([source], [], [], 3)[0])
                    first.append(json.loads(source.readline()))
                self.assertEqual([row["sequence"] for row in first], [1, 2])
                chunks = []
                while True:
                    self.assertTrue(select.select([log], [], [], 3)[0])
                    chunk = log.read(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                _, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual([row["sequence"] for row in first + records(source.read())],
                             list(range(1, 9)))
        received = b"".join(chunks)
        self.assertEqual(received[:filler_bytes], b"x" * filler_bytes)
        telemetry_bytes = received[filler_bytes:]
        self.assertTrue(telemetry_bytes.endswith(b"\n"))
        telemetry = records(telemetry_bytes)
        self.assertGreaterEqual(len(telemetry), 2)
        self.assertEqual(telemetry[-2]["sequence"], 8)
        self.assertEqual(telemetry[-1]["type"], "summary")
        self.assertEqual(telemetry[-1]["delivered_outputs"], 8)
        self.assertGreater(telemetry[-1]["telemetry_records_dropped"], 0)

    def test_rejects_invalid_output_descriptor_and_aliases(self):
        with pipe() as (source, sink):
            invalid = self.run_demo(source)
            aliased_stdout = self.run_demo(sink, stdout=sink)
            aliased_stderr = subprocess.run(COMMAND + ["--output-fd", str(sink.fileno())],
                                            pass_fds=(sink.fileno(),), stdout=subprocess.PIPE,
                                            stderr=sink, timeout=5)
            sink.close()
            self.assertEqual(source.read(), b"")
        for result in (invalid, aliased_stdout, aliased_stderr):
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(result.stdout)

    def test_help_and_parser_errors_cannot_write_to_aliased_authority(self):
        for stream in ("stdout", "stderr"):
            for case in ("help", "malformed-option", "malformed-descriptor", "duplicate"):
                with self.subTest(stream=stream, case=case), pipe() as (source, sink):
                    fd = str(sink.fileno())
                    args = {
                        "help": ["--help", "--output-fd", fd],
                        "malformed-option": ["--samples", "invalid", "--output-fd", fd],
                        "malformed-descriptor": ["--output-fd", fd + "invalid"],
                        "duplicate": ["--output-fd", "99", "--output-fd", fd],
                    }[case]
                    stdio = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, stream: sink}
                    result = subprocess.run([DEMO, *args], pass_fds=(sink.fileno(),),
                                            timeout=5, **stdio)
                    sink.close()
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(source.read(), b"")

    def test_requires_one_decimal_descriptor_above_standard_streams(self):
        for value in ("", "-1", "+3", " 3", "3 ", "3x", "0", "1", "2", "999999999999"):
            with self.subTest(value=value):
                result = subprocess.run(COMMAND + ["--output-fd", value], capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(result.stdout)
        with pipe() as (source, sink):
            result = self.run_demo(sink, extra=("--output-fd", str(sink.fileno())))
            sink.close()
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(b"must appear only once", result.stderr)
            self.assertFalse(result.stdout)
            self.assertEqual(source.read(), b"")

    def test_nonpipe_stdout_requires_explicit_telemetry_off(self):
        with pipe() as (source, sink), tempfile.TemporaryFile() as log:
            rejected = self.run_demo(sink, stdout=log)
            self.assertEqual(rejected.returncode, 2, rejected.stderr)
            self.assertIn(b"--no-telemetry", rejected.stderr)
            delivered = self.run_demo(sink, extra=("--no-telemetry",), stdout=log)
            sink.close()
            self.assertEqual(delivered.returncode, 0, delivered.stderr)
            self.assertEqual(len(records(source.read())), 8)
            self.assertEqual(log.tell(), 0)

    def test_authority_writer_is_closed_before_worker_execution(self):
        with tempfile.TemporaryDirectory() as directory, pipe() as (source, sink):
            worker = Path(directory) / "worker-probe"
            fd = sink.fileno()
            identity = os.fstat(fd)
            worker.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "try:\n"
                f"    info = os.fstat({fd})\n"
                "except OSError:\n"
                "    pass\n"
                "else:\n"
                f"    if (info.st_dev, info.st_ino) == {(identity.st_dev, identity.st_ino)!r}:\n"
                "        sys.exit('authority writer inherited')\n"
                f"os.execv({WORKER!r}, [{WORKER!r}, *sys.argv[1:]])\n"
            )
            worker.chmod(0o755)
            result = self.run_demo(sink, command=COMMAND + ["--worker", str(worker)])
            sink.close()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(records(source.read())), 8)

    def test_missed_decision_has_no_authority_record(self):
        with tempfile.TemporaryDirectory() as directory, pipe() as (source, sink):
            worker = Path(directory) / "worker-stop"
            worker.write_text(
                f"#!{sys.executable}\n"
                "import os, signal, subprocess, sys, time\n"
                f"with subprocess.Popen([{WORKER!r}, *sys.argv[1:]], stdin=subprocess.PIPE) as child:\n"
                "    if os.read(0, 1):\n"
                "        parent = os.getppid()\n"
                "        os.kill(parent, signal.SIGSTOP)\n"
                "        try:\n"
                "            time.sleep(0.15)\n"
                "        finally:\n"
                "            os.kill(parent, signal.SIGCONT)\n"
                "    child.stdin.close()\n"
            )
            worker.chmod(0o755)
            command = [DEMO, "--worker", str(worker), "--control-stdin", "--samples", "3",
                       "--period-ms", "200", "--deadline-ms", "100"]
            result = self.run_demo(sink, command=command)
            sink.close()
            self.assertEqual(source.read(), b"")
        self.assertEqual(result.returncode, 3, result.stderr)
        telemetry = records(result.stdout)
        self.assertTrue(telemetry[1]["publication_miss"])
        self.assertEqual(telemetry[1]["reason"], "decision_deadline_exceeded")
        self.assertFalse(telemetry[1]["output_delivered"])
        self.assertEqual(telemetry[-1]["selected_outputs"], 0)
        self.assertEqual(telemetry[-1]["delivered_outputs"], 0)


if __name__ == "__main__":
    unittest.main()
