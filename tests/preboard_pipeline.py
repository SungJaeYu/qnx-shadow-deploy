"""Exercise the broker trace, relay bounds and LED consumer."""
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

BUILD = Path(sys.argv.pop()).resolve()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from board_relay import BoardRelay, MAX_FRAME_BYTES, RING_SIZE
from led_events import LedEvents

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def encode(row):
    return (json.dumps(row) + "\n").encode()


def meta():
    return {"type": "meta", "schema": 1, "backend": "host-posix-pipes", "scenario": "promote"}


def sample(sequence, phase="shadowing", event=""):
    return {"type": "sample", "sequence": sequence, "phase": phase, "event": event,
            "time_s": sequence * 0.02, "truth": None, "input": 10}


class Pipeline(unittest.TestCase):
    def test_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.mm"
            command = [str(BUILD / "shadow-demo"), "--worker", str(BUILD / "shadow-worker"),
                       "--scenario", "promote", "--samples", "3", "--period-ms", "100",
                       "--deadline-ms", "80", "--sensor-trace", str(trace)]
            trace.write_text("10250\n10250\n10250\n")
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(rows[0]["input_source"], "sensor-trace-mm")
            self.assertIsNone(rows[1]["truth"])
            self.assertEqual(rows[1]["input"], 10.25)
            for row in rows[1:-1]:
                self.assertEqual(row["baseline"]["position"], 10.25)
                self.assertEqual(row["candidate"]["position"], 10.25)
            for bad in ("10250\n", "10250\n10250\n10250", "10250\n0.5\n10250\n",
                        "1000001\n10250\n10250\n", "0" * 4097 + "\n", "10250\n" * 4):
                trace.write_text(bad)
                result = subprocess.run(command, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("sensor trace", result.stderr)

            fifo = Path(directory) / "fifo"
            os.mkfifo(fifo)
            result = subprocess.run(command[:-1] + [str(fifo)], capture_output=True, text=True, timeout=2)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("regular file", result.stderr)

    def test_relay_slow_readers_and_disconnect(self):
        relay = BoardRelay("pi4")
        data = encode(meta()) + encode(sample(2)) + b"bad\n" + b"x" * (MAX_FRAME_BYTES + 3) + b"\n"
        data += b"".join(encode(sample(i)) for i in range(3, RING_SIZE + 4))
        reader = threading.Thread(target=relay.consume, args=(io.BytesIO(data + b"partial"),))
        reader.start()
        reader.join(timeout=3)
        self.assertFalse(reader.is_alive())
        snapshot = relay.snapshot()
        self.assertEqual(snapshot["source"], "pi4")
        self.assertEqual(snapshot["upstream"], "disconnected")
        self.assertEqual(snapshot["malformed"], 2)
        self.assertEqual(snapshot["oversize"], 1)
        self.assertEqual(snapshot["sequence_gaps"], 1)
        self.assertEqual(snapshot["evicted"], 2)
        self.assertEqual(len(snapshot["rows"]), RING_SIZE)
        self.assertEqual(snapshot["rows"][-1]["sequence"], RING_SIZE + 3)
        self.assertEqual(snapshot["meta"], meta())
        relay.consume(io.BytesIO(encode(meta()) + encode(sample(1))))
        self.assertNotEqual(relay.snapshot()["run_id"], snapshot["run_id"])
        self.assertEqual(relay.snapshot()["rows"], [sample(1)])

    def test_read_only_http_and_abandoned_observer(self):
        with subprocess.Popen([sys.executable, str(TOOLS / "serve.py"), "--port", "0",
                               "--board-stdin", "pi4"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as server:
            try:
                port = int(server.stdout.readline().decode().rsplit(":", 1)[-1])
                pending = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                pending.request("GET", "/api/board")
                pending.close()
                server.stdin.write(encode(meta()) + encode(sample(2))
                                   + encode({"type": "summary", "samples": 4}))
                server.stdin.close()

                deadline = time.monotonic() + 2
                while True:
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    connection.request("GET", "/api/board")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    snapshot = json.load(response)
                    connection.close()
                    if snapshot["upstream"] == "disconnected":
                        break
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                self.assertTrue(snapshot["complete"])
                self.assertEqual(snapshot["sequence_gaps"], 3)
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                connection.request("POST", "/api/board")
                self.assertEqual(connection.getresponse().status, 404)
                connection.close()
            finally:
                server.terminate()

    def test_led_cli_resets_on_malformed_and_eof(self):
        command = [sys.executable, str(TOOLS / "led_events.py")]
        for ending, status in ((b"", 0), (b"{malformed}\n", 2)):
            result = subprocess.run(command, input=encode(meta()) + encode(sample(1)) + ending,
                                    capture_output=True, timeout=3)
            self.assertEqual(result.returncode, status, result.stderr)
            self.assertEqual([json.loads(line)["state"] for line in result.stdout.splitlines()],
                             ["off", "qualification", "off"])

    def test_led_hold_and_reset(self):
        leds = LedEvents(hold_samples=3)
        self.assertEqual(leds.accept(meta()), "off")
        self.assertEqual(leds.accept(sample(1, "idle")), "normal")
        self.assertEqual(leds.accept(sample(2)), "qualification")
        self.assertEqual(leds.accept(sample(3, "rolled_back", "candidate_rolled_back")), "rollback")
        self.assertEqual(leds.accept(sample(5, "rolled_back")), "rollback")
        self.assertEqual(leds.accept(sample(6, "rolled_back")), "normal")
        self.assertEqual(leds.accept({"type": "summary"}), "off")
        self.assertEqual(leds.accept(meta()), "off")
        self.assertEqual(leds.accept(sample(1, "failed")), "off")


if __name__ == "__main__":
    unittest.main()
