#!/usr/bin/env python3
"""Verify a fresh tools/lifecycle_checks.sh run exported from a host or guest."""

import argparse
import json
import math
from pathlib import Path
import re


def check(condition, message):
    if not condition:
        raise ValueError(message)


def only(rows, event, generation):
    matches = [row for row in rows if row.get("event") == event and
               row["generation"] == generation]
    check(len(matches) == 1, f"expected one {event} for generation {generation}")
    return matches[0]


def verify_native(path, broker, baseline, first, second):
    connections = {}
    for phase in ("baseline", "probation", "rollback", "committed"):
        text = (path / f"{phase}.fds").read_text()
        connections[phase] = [(fd, int(pid)) for fd, pid in
                              re.findall(r"^\s+(\d+s?)\s+(\d+)\s", text, re.M)]
        check(connections[phase], f"missing {phase} broker fd snapshot")
    expected = {"baseline": {baseline}, "probation": {baseline, first},
                "rollback": {baseline}, "committed": {second}}
    for phase, pids in expected.items():
        worker_connections = [pid for fd, pid in connections[phase]
                              if fd.endswith("s") and pid in {baseline, first, second}]
        check(sorted(worker_connections) == sorted(pids), f"{phase} side-channel identities")
    check(sorted(connections["rollback"]) == sorted(connections["baseline"]),
          "rollback left extra broker connections or descriptors")
    remaining = (path / "finished.processes").read_text()
    remaining_pids = {int(pid) for pid in re.findall(r"^\s*(\d+)\s", remaining, re.M)}
    check(remaining_pids and not (remaining_pids & {broker, baseline, first, second}),
          "missing process list or surviving owned process")
    return {phase: sorted(pids) for phase, pids in expected.items()}


def verify(path):
    rows = [json.loads(line) for line in (path / "lifecycle.jsonl").read_text().splitlines()]
    meta, summary = rows[0], rows[-1]
    check(meta["type"] == "meta" and meta["schema"] == 2 and summary["type"] == "summary",
          "incomplete live telemetry")
    check((meta["period_ms"], meta["deadline_ms"], meta["ipc_budget_ms"]) == (200, 150, 112.5),
          "unexpected lifecycle timing configuration")
    backend = meta["backend"]
    check(backend in ("host-posix-pipes", "qnx-native-ipc"), f"unexpected backend {backend}")
    check((path / "exit").read_text().strip() == "0", "broker failed")
    check((path / "retirement").read_text().startswith("PASS child retirement: "),
          "runner did not verify child retirement")
    ticks = [row for row in rows if row["type"] == "sample"]
    requested = meta["requested_inputs"]
    check(requested == 90 and [tick["sequence"] for tick in ticks] == list(range(1, requested + 1)),
          "incomplete or missing input sequence")
    baseline = meta["baseline_pid"]
    broker = int((path / "broker-pid").read_text())
    check(meta["candidate_pid"] == -1, "candidate started before staging")
    first_promote = only(ticks, "candidate_promoted", 1)
    rollback = only(ticks, "candidate_rolled_back", 1)
    second_promote = only(ticks, "candidate_promoted", 2)
    commit = only(ticks, "candidate_committed", 2)
    first, second = first_promote["candidate_pid"], second_promote["candidate_pid"]
    check(min(broker, baseline, first, second) > 1 and len({broker, baseline, first, second}) == 4,
          "invalid or reused process identity")
    check(rollback["candidate_pid"] == first and commit["candidate_pid"] == second,
          "candidate identity changed within its generation")
    check(first_promote["sequence"] < rollback["sequence"] <
          first_promote["sequence"] + meta["probation_samples"] - 1,
          "candidate crash was not observed before the generation 1 commit boundary")
    check(rollback["phase"] == "rolled_back" and rollback["candidate_check"] == "worker_disconnected" and
          rollback["candidate"]["position"] is None, "rollback did not observe candidate process loss")
    check(commit["sequence"] == second_promote["sequence"] + meta["probation_samples"] - 1,
          "generation 2 did not finish probation")
    check(not any(row["event"] == "candidate_committed" and row["generation"] == 1 for row in ticks),
          "failed generation committed")

    for generation, promoted, pid in ((1, first_promote, first), (2, second_promote, second)):
        stage = only(rows, "stage_requested", generation)
        ready = only(rows, "replay_complete", generation)
        check(stage["after_sequence"] >= (20 if generation == 1 else rollback["sequence"]),
              "stage happened before live service or previous rollback")
        through = ready["through_sequence"]
        check(ready["candidate_pid"] == pid and ready["replayed"] >= 16 and
              through == ready["after_sequence"] and through >= stage["after_sequence"],
              "generation replay did not catch up with its own candidate")
        check(promoted["sequence"] == through + meta["qualification_samples"] + 1,
              "promotion did not follow fresh qualification")
        for tick in ticks[through:promoted["sequence"] - 1]:
            check(tick["phase"] == "shadowing" and tick["candidate_pid"] == pid and
                  tick["candidate_check"] == "ok" and tick["source"] == "baseline",
                  "invalid or authoritative candidate during fresh qualification")

    expected_baseline = None
    previous_output = None
    max_output_step = 0.0
    for tick in ticks:
        sequence = tick["sequence"]
        check(tick["baseline_pid"] == (baseline if sequence <= commit["sequence"] else -1),
              f"baseline PID continuity at {sequence}")
        if sequence <= commit["sequence"]:
            # Verify the current filter step independently, including on rollback.
            measured = tick["input"]
            expected_baseline = measured if expected_baseline is None else (
                expected_baseline + 0.25 * (measured - expected_baseline))
            check(tick["baseline_check"] == "ok" and math.isclose(
                tick["baseline"]["position"], expected_baseline, abs_tol=1e-7),
                f"invalid or stale baseline at {sequence}")
        expected_source = ("candidate" if first_promote["sequence"] <= sequence < rollback["sequence"] or
                           sequence >= second_promote["sequence"] else "baseline")
        check(tick["source"] == expected_source and tick[f"{expected_source}_check"] == "ok",
              f"incorrect authority or selected observation at {sequence}")
        if expected_source == "candidate":
            check(tick["candidate_pid"] == (first if tick["generation"] == 1 else second),
                  f"incorrect candidate PID at {sequence}")
        if sequence > rollback["sequence"] and tick["generation"] == 1:
            check(tick["candidate"] is None and tick["candidate_pid"] == -1,
                  "failed generation transport was reused")
        output = tick["output"]
        check(output is not None and output == tick[expected_source]["position"],
              f"selection does not match current observation at {sequence}")
        if previous_output is not None:
            max_output_step = max(max_output_step, abs(output - previous_output))
        previous_output = output

    check(summary["phase"] == "committed" and
          summary["acquired_inputs"] == summary["selected_outputs"] == requested and
          summary["missing_selected_outputs"] == summary["unprocessed_inputs"] ==
          summary["decision_deadline_misses"] == 0 and
          not any(tick["publication_miss"] for tick in ticks),
          "lifecycle summary does not match observed continuity")
    check(max_output_step <= 5, "selected stream exceeded the 5 m continuity limit")
    timing = {key: summary[key] for key in (
        "decision_deadline_misses", "initial_unoccupied_slots", "skipped_nominal_slots",
        "max_scheduling_lateness_ms", "max_nominal_grid_lateness_ms",
        "max_acquisition_interval_ms", "max_selected_interval_ms", "effective_selected_rate_hz",
    )}
    timing["max_decision_latency_ms"] = max(tick["decision_latency_ms"] for tick in ticks)
    timing["verdict"] = ("pass" if not any(timing[key] for key in (
        "decision_deadline_misses", "initial_unoccupied_slots", "skipped_nominal_slots",
    )) and not any(tick["publication_miss"] for tick in ticks) and
        timing["max_decision_latency_ms"] <= meta["deadline_ms"] else "fail")

    evidence = {
        "backend": backend, "broker_pid": broker, "baseline_pid": baseline,
        "first_candidate_pid": first, "second_candidate_pid": second, "selected_outputs": requested,
        "first_promotion": first_promote["sequence"], "rollback": rollback["sequence"],
        "second_promotion": second_promote["sequence"], "commit": commit["sequence"],
        "max_output_step_m": round(max_output_step, 9), "timing": timing,
    }
    if backend == "qnx-native-ipc":
        evidence["native_connections"] = verify_native(path, broker, baseline, first, second)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fresh_run_dir", type=Path)
    parser.add_argument("--functional-only", action="store_true",
                        help="report nominal-grid timing without gating the host functional check")
    args = parser.parse_args()
    try:
        evidence = verify(args.fresh_run_dir)
    except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
        parser.exit(1, f"FAIL {args.fresh_run_dir}: {error}\n")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return not args.functional_only and evidence["timing"]["verdict"] != "pass"


if __name__ == "__main__":
    raise SystemExit(main())
