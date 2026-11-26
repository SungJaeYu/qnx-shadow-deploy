"""Check that interrupted driver cleanup records the forced broker exit."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


DRIVER = Path(__file__).resolve().parents[1] / "tools" / "cancel_retry_cycles.sh"


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        out = root / "run"
        broker = root / "shadow-demo"
        broker.write_text(
            "#!/bin/sh\n"
            'exec "$SHADOW_TEST_PYTHON" -c \'\n'
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            'print("ready", flush=True)\n'
            "time.sleep(30)\n"
            "'\n"
        )
        broker.chmod(0o755)
        (root / "candidate.manifest").write_text("executable=unused\n")
        with subprocess.Popen(
            ["sh", str(DRIVER), str(root), str(out)],
            env={**os.environ, "SHADOW_TEST_PYTHON": sys.executable},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as driver:
            try:
                trace = out / "cycles.jsonl"
                deadline = time.monotonic() + 5
                while not trace.exists() or "ready\n" not in trace.read_text():
                    if driver.poll() is not None or time.monotonic() >= deadline:
                        raise AssertionError("stub broker did not reach FIFO rendezvous")
                    time.sleep(0.02)
                driver.send_signal(signal.SIGTERM)
                stdout, stderr = driver.communicate(timeout=10)
                assert driver.returncode == 130, (stdout, stderr, driver.returncode)
                assert "Broker did not stop after TERM" in stderr, stderr
                status = out / "exit"
                assert status.exists(), "cleanup did not record the forced broker exit"
                assert int(status.read_text()) > 128, status.read_text()
                assert not (out / "control.fifo").exists(), "cleanup left the control FIFO"
            finally:
                if driver.poll() is None:
                    driver.terminate()
                    try:
                        driver.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        driver.kill()
                        driver.communicate()


if __name__ == "__main__":
    main()
