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


def check_cancel_precedes_candidate_inspection():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        out = root / "run"
        (root / "candidate.manifest").write_text("executable=unused\n")
        broker = root / "shadow-demo"
        broker.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, sys\n"
            "signal = pathlib.Path(os.environ['SHADOW_TEST_CANCEL_SENT'])\n"
            "print('{\"type\":\"meta\",\"baseline_pid\":424242}', flush=True)\n"
            "print('{\"type\":\"sample\",\"sequence\":20,\"phase\":\"idle\"}', flush=True)\n"
            "if not sys.stdin.readline().startswith('stage '): sys.exit(2)\n"
            "print('{\"type\":\"deployment\",\"generation\":1,\"event\":\"stage_requested\"}', flush=True)\n"
            "print('{\"type\":\"deployment\",\"generation\":1,\"event\":\"replay_complete\","
            "\"after_sequence\":22,\"candidate_pid\":424243}', flush=True)\n"
            "print('{\"type\":\"sample\",\"sequence\":23,\"generation\":1,\"phase\":\"shadowing\","
            "\"candidate_check\":\"ok\"}', flush=True)\n"
            "if sys.stdin.readline() == 'cancel\\n': signal.touch()\n"
            "print('{\"type\":\"summary\"}', flush=True)\n"
        )
        broker.chmod(0o755)
        sed = root / "sed"
        sed.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *candidate_pid*) \n"
            "    retries=0\n"
            "    until test -f \"$SHADOW_TEST_CANCEL_SENT\"; do\n"
            "      retries=$((retries + 1)); test \"$retries\" -lt 40 || exit 3\n"
            "      sleep 0.05\n"
            "    done ;;\n"
            "esac\n"
            "exec /usr/bin/sed \"$@\"\n"
        )
        sed.chmod(0o755)
        sent = root / "cancel-sent"
        subprocess.run(
            ["sh", str(DRIVER), str(root), str(out)],
            env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                 "SHADOW_TEST_CANCEL_SENT": str(sent)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        assert sent.exists(), "controller inspected the candidate PID before sending cancel"


if __name__ == "__main__":
    check_cancel_precedes_candidate_inspection()
    check_interrupted_cleanup([])
    check_interrupted_cleanup(["--qnx-guest-profile"])
