"""Check that interrupted driver cleanup records the forced broker exit."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


DRIVER = Path(__file__).resolve().parents[1] / "tools" / "cancel_retry_cycles.sh"


def check_interrupted_cleanup(profile_args):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        out = root / "run"
        broker = root / "shadow-demo"
        broker.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$@" > "$SHADOW_TEST_BROKER_ARGS"\n'
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
            ["sh", str(DRIVER), *profile_args, str(root), str(out)],
            env={**os.environ, "SHADOW_TEST_PYTHON": sys.executable,
                 "SHADOW_TEST_BROKER_ARGS": str(root / "broker-args")},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ) as driver:
            completed = False
            try:
                trace = out / "cycles.jsonl"
                deadline = time.monotonic() + 5
                while not trace.exists() or "ready\n" not in trace.read_text():
                    if driver.poll() is not None:
                        stdout, stderr = driver.communicate(timeout=5)
                        raise AssertionError(
                            f"driver exited before FIFO rendezvous: {driver.returncode}, {stdout!r}, {stderr!r}"
                        )
                    if time.monotonic() >= deadline:
                        broker_stderr = out / "stderr"
                        detail = broker_stderr.read_text() if broker_stderr.exists() else "stderr file absent"
                        raise AssertionError(f"stub broker did not reach FIFO rendezvous: {detail}")
                    time.sleep(0.02)
                args = (root / "broker-args").read_text().splitlines()
                assert ("--qnx-guest-profile" in args) == bool(profile_args), args
                driver.send_signal(signal.SIGTERM)
                stdout, stderr = driver.communicate(timeout=10)
                assert driver.returncode == 130, (stdout, stderr, driver.returncode)
                assert "Broker did not stop after TERM" in stderr, stderr
                status = out / "exit"
                assert status.exists(), "cleanup did not record the forced broker exit"
                assert int(status.read_text()) > 128, status.read_text()
                assert not (out / "control.fifo").exists(), "cleanup left the control FIFO"
                completed = True
            finally:
                if not completed:
                    try:
                        os.killpg(driver.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    driver.communicate(timeout=5)


if __name__ == "__main__":
    check_interrupted_cleanup([])
    check_interrupted_cleanup(["--qnx-guest-profile"])
