#!/usr/bin/env python3
"""Check a fresh same-broker cancellation trace and online retirement observations."""
import argparse
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def one(rows, generation, event):
    found = [row for row in rows if row.get("generation") == generation and row.get("event") == event]
    require(len(found) == 1, f"generation {generation}: expected exactly one {event}")
    return found[0]


def verify(directory):
    rows = [json.loads(line) for line in (directory / "cycles.jsonl").read_text().splitlines()]
    require(len(rows) >= 2 and rows[0]["type"] == "meta" and rows[-1]["type"] == "summary",
            "missing trace envelope")
    meta, summary = rows[0], rows[-1]
    require([row["type"] for row in rows if row["type"] in ("meta", "summary")] == ["meta", "summary"],
            "multiple trace envelopes")
    require(meta["scenario"] == "live-deployment" and meta["schema"] == 2 and meta["candidate_pid"] == -1,
            "expected a live broker starting with only a baseline")
    require("input_source" not in meta, "expected live inputs")
    require((directory / "exit").read_text().strip() == "0", "broker failed")
    require(not any(row.get("event") in {"command_rejected", "preparation_failed",
                                       "candidate_rejected", "candidate_rolled_back"} for row in rows),
            "a command or candidate was rejected unexpectedly")
    samples = [row for row in rows if row["type"] == "sample"]
    expected_samples = meta["requested_inputs"]
    require(expected_samples == 320 and [row["sequence"] for row in samples] == list(range(1, expected_samples + 1)),
            "missing or nonconsecutive live samples")
    require(summary["phase"] == "committed" and summary["outputs"] == expected_samples
            and summary["missing_outputs"] == 0 and not summary["interrupted"],
            "final state or continuity summary failed")

    retirement = (directory / "retirement").read_text().splitlines()
    require(retirement and retirement[0].startswith("broker "), "broker PID not recorded")
    broker = int(retirement[0].split()[1])
    baseline = meta["baseline_pid"]
    require(broker > 1 and baseline > 1 and broker != baseline, "invalid broker or baseline PID")
    pids = {broker, baseline}
    expected_retirement = [f"broker {broker}"]
    previous_terminal_index = 0
    starts, candidate_windows = [], {}
    for generation in range(1, 22):
        stage = one(rows, generation, "stage_requested")
        replay = one(rows, generation, "replay_complete")
        stage_index, replay_index = rows.index(stage), rows.index(replay)
        require(previous_terminal_index < stage_index < replay_index, f"generation {generation}: stage/replay order")
        require(replay["replayed"] >= 16 and replay["through_sequence"] == replay["after_sequence"],
                f"generation {generation}: historical replay did not catch up")
        starts.append(stage["after_sequence"] + 1)
        pid = replay["candidate_pid"]
        require(pid > 1 and pid not in pids, f"generation {generation}: missing or reused candidate PID")
        pids.add(pid)
        if generation <= 20:
            request = one(rows, generation, "cancel_requested")
            cancelled = one(rows, generation, "candidate_cancelled")
            previous_terminal_index = rows.index(cancelled)
            require(replay_index < rows.index(request) < previous_terminal_index
                    and request["after_sequence"] + 1 == cancelled["sequence"]
                    and request["reason"] == cancelled["reason"] == "operator_cancelled"
                    and cancelled["phase"] == "rejected" and cancelled["candidate_pid"] == pid,
                    f"generation {generation}: cancellation ordering, phase or identity")
            expected_retirement.append(f"cancelled {generation} {pid}")
            terminal_sequence = cancelled["sequence"]
        else:
            promoted = one(rows, generation, "candidate_promoted")
            committed = one(rows, generation, "candidate_committed")
            require(replay_index < rows.index(promoted) < rows.index(committed)
                    and promoted["candidate_pid"] == committed["candidate_pid"] == pid
                    and promoted["phase"] == "probation" and committed["phase"] == "committed"
                    and promoted["sequence"] - replay["after_sequence"] == meta["qualification_samples"] + 1
                    and committed["sequence"] - promoted["sequence"] == meta["probation_samples"] - 1,
                    "final healthy promotion or commit failed")
            expected_retirement.extend((f"baseline {baseline}", f"final {pid}"))
            terminal_sequence = expected_samples
        candidate_windows[generation] = (replay["after_sequence"], terminal_sequence, pid)

    for event, count in (("stage_requested", 21), ("replay_complete", 21), ("cancel_requested", 20),
                         ("candidate_cancelled", 20), ("candidate_promoted", 1), ("candidate_committed", 1)):
        require(sum(row.get("event") == event for row in rows) == count, f"unexpected {event} count")
    require(retirement == expected_retirement, "child retirement observations do not match trace")
    require(committed["sequence"] < expected_samples, "no sample confirmed post-commit operation")
    for sample in samples:
        sequence = sample["sequence"]
        require(sample["generation"] == sum(start <= sequence for start in starts),
                f"sample {sequence}: unexpected generation")
        source = "candidate" if sequence >= promoted["sequence"] else "baseline"
        require(sample["source"] == source and sample["output"] is not None
                and sample[source] is not None and sample["output"] == sample[source]["position"],
                f"sample {sequence}: selected output missing or has wrong source")
        expected_baseline = baseline if sequence <= committed["sequence"] else -1
        require(sample["baseline_pid"] == expected_baseline, f"sample {sequence}: baseline identity changed")
        generation = sample["generation"]
        expected_candidate = -1
        if generation:
            replay_boundary, terminal, pid = candidate_windows[generation]
            if replay_boundary < sequence <= terminal:
                expected_candidate = pid
        require(sample["candidate_pid"] == expected_candidate, f"sample {sequence}: unexpected candidate PID")
    return meta, summary, committed["sequence"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fresh_trace_dir", type=Path)
    parser.add_argument("--require-timing", action="store_true", help="also require zero skipped slots and decision misses")
    args = parser.parse_args()
    try:
        meta, summary, commit = verify(args.fresh_trace_dir)
        print(f"PASS functional: backend={meta['backend']} cancellations=20 commit_sample={commit} "
              f"selected_outputs={summary['outputs']} all workers retired")
        skipped, misses = summary["skipped_nominal_slots"], summary["decision_deadline_misses"]
        print(f"Timing: period_ms={meta['period_ms']} deadline_ms={meta['deadline_ms']} "
              f"ipc_budget_ms={meta['ipc_budget_ms']} skipped_nominal_slots={skipped} decision_misses={misses} "
              f"max_acquisition_interval_ms={summary['max_acquisition_interval_ms']}")
        if args.require_timing:
            require(skipped == misses == 0, "timing gate failed")
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
