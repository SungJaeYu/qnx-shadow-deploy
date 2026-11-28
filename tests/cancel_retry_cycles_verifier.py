"""Reject malformed copies of a fresh cancellation/retry trace."""
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from verify_cancel_retry_cycles import verify


def mutate_and_reject(original, label, mutate, reason, **options):
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp)
        for name in ("cycles.jsonl", "exit", "retirement"):
            shutil.copyfile(original / name, copy / name)
        trace = copy / "cycles.jsonl"
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        mutate(rows)
        trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
        try:
            verify(copy, **options)
        except ValueError as error:
            if reason not in str(error):
                raise AssertionError(f"{label}: unexpected rejection: {error}") from error
        else:
            raise AssertionError(f"{label}: corrupted trace passed")
        print(f"PASS negative: {label}")


def event(rows, name, generation=1):
    return next(row for row in rows if row.get("generation") == generation and row.get("event") == name)


def main(original):
    verify(original)
    def remove_profile(rows):
        rows[0]["backend"] = "qnx-native-ipc"
        rows[0].pop("candidate_launch_profile", None)

    mutate_and_reject(original, "missing guest profile",
                      remove_profile,
                      "expected qnx-guest-16-mib", require_qnx_guest_profile=True)
    mutate_and_reject(original, "wrong guest profile",
                      lambda rows: rows[0].update(backend="qnx-native-ipc",
                                                 candidate_launch_profile="default"),
                      "expected qnx-guest-16-mib", require_qnx_guest_profile=True)
    mutate_and_reject(original, "guest profile on another backend",
                      lambda rows: rows[0].update(backend="posix-pipe",
                                                 candidate_launch_profile="qnx-guest-16-mib"),
                      "expected qnx-native-ipc", require_qnx_guest_profile=True)
    try:
        verify(original, expected_samples=10000)
    except ValueError as error:
        if "expected 10000 requested samples, got 640" not in str(error):
            raise
    else:
        raise AssertionError("a 640-sample trace passed the 10000-sample gate")
    print("PASS negative: 640-sample trace cannot pass the 10000-sample gate")
    mutate_and_reject(original, "generated capture relabelled as sensor trace",
                      lambda rows: rows[0].update(input_source="sensor-trace-mm"),
                      "invalid sensor trace input", sensor_trace=True)
    mutate_and_reject(original, "acceptance claims wrong preceding sample",
                      lambda rows: event(rows, "cancel_requested").update(after_sequence=1),
                      "outside its sample boundary")
    mutate_and_reject(original, "no valid live shadow sample",
                      lambda rows: next(row for row in rows if row.get("sequence") ==
                                        event(rows, "replay_complete")["after_sequence"] + 1)
                      .update(candidate_check="deadline_exceeded"), "no valid live shadow sample")
    mutate_and_reject(original, "missing accepted cancellation",
                      lambda rows: rows.remove(event(rows, "cancel_requested")), "exactly one cancel_requested")
    mutate_and_reject(original, "summary hides nominal skips",
                      lambda rows: rows[-1].update(skipped_nominal_slots=rows[-1]["skipped_nominal_slots"] + 1),
                      "invalid continuity field: skipped_nominal_slots")
    mutate_and_reject(original, "per-sample deadline flag conflicts with timestamps",
                      lambda rows: next(row for row in rows if row["type"] == "sample")
                      .update(publication_miss=True), "invalid continuity field: publication_miss")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
