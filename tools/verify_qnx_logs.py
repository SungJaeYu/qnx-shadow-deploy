#!/usr/bin/env python3
"""Check exported native traces without running QNX binaries on the host."""
import json
import math
from pathlib import Path
import re
import sys


class TraceValidationError(ValueError):
    pass


def check(condition, description):
    if not condition:
        raise TraceValidationError(description)


def load(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    return validate_rows(path, rows)


def validate_continuity(meta, summary, ticks):
    """Verify optional producer metrics against exact monotonic offsets."""
    if "requested_inputs" not in meta:
        check("requested_inputs" not in summary and not any("nominal_slot" in row for row in ticks),
              "incomplete continuity schema")
        return False
    requested = meta["requested_inputs"]
    check(type(requested) is int and 1 <= requested <= 1_000_000 and len(ticks) <= requested,
          "invalid requested input count")
    period_ms = meta["period_ms"]
    check(type(period_ms) is int and 1 <= period_ms <= 1000, "invalid continuity period")
    period_ns = period_ms * 1_000_000

    def fields(row, expected):
        for key, value in expected.items():
            actual = row.get(key)
            if isinstance(value, float):
                matches = type(actual) in (int, float) and math.isclose(actual, value, rel_tol=1e-9)
            else:
                matches = type(actual) is type(value) and actual == value
            check(key in row and matches, f"invalid continuity field: {key}")

    previous_acquired = None
    last_slot = None
    initial = skipped = 0
    intervals, scheduling, nominal, grid, selected = [], [], [], [], []
    for index, row in enumerate(ticks):
        acquired, scheduled, decision = (row.get(key) for key in
                                         ("acquired_offset_ns", "scheduled_release_offset_ns",
                                          "decision_offset_ns"))
        check(all(type(value) is int for value in (acquired, scheduled, decision))
              and 0 <= scheduled <= acquired <= decision, "invalid continuity offsets")
        slot, remainder = divmod(acquired, period_ns)
        gap = 0
        interval_ms = None
        if previous_acquired is None:
            check(scheduled == 0, "first release must be the nominal origin")
            initial = slot
        else:
            check(scheduled >= previous_acquired + period_ns, "release outside no-burst schedule")
            interval_ms = (acquired - previous_acquired) / 1e6
            intervals.append(interval_ms)
            gap = slot - last_slot - 1
        scheduling.append((acquired - scheduled) / 1e6)
        nominal.append(remainder / 1e6)
        grid.append((acquired - index * period_ns) / 1e6)
        fields(row, {
            "nominal_slot": slot, "skipped_nominal_slots": gap, "acquisition_interval_ms": interval_ms,
            "release_lateness_ms": scheduling[-1], "nominal_slot_lateness_ms": nominal[-1],
            "nominal_grid_lateness_ms": grid[-1],
        })
        if row["output"] is not None:
            selected.append(decision)
        skipped += gap
        previous_acquired, last_slot = acquired, slot
    selected_intervals = [(current - previous) / 1e6 for previous, current in zip(selected, selected[1:])]
    check(all(value > 0 for value in selected_intervals), "out-of-order selections")
    fields(summary, {
        "requested_inputs": requested, "acquired_inputs": len(ticks),
        "unprocessed_inputs": requested - len(ticks), "selected_outputs": summary["outputs"],
        "missing_selected_outputs": summary["missing_outputs"],
        "decision_deadline_misses": summary["publication_misses"],
        "initial_unoccupied_slots": initial, "skipped_nominal_slots": skipped,
        "last_nominal_slot": last_slot, "max_scheduling_lateness_ms": max(scheduling, default=None),
        "max_nominal_slot_lateness_ms": max(nominal, default=None),
        "max_nominal_grid_lateness_ms": max(grid, default=None),
        "max_acquisition_interval_ms": max(intervals, default=None),
        "max_selected_interval_ms": max(selected_intervals, default=None),
        "effective_selected_rate_hz": ((len(selected) - 1) * 1e9 / (selected[-1] - selected[0])
                                       if selected_intervals else None),
        "external_delivery": "unmeasured",
    })
    return True


def validate_rows(path, rows, *, allow_interrupted=False):
    check(rows[0]["type"] == "meta", f"{path.name}: missing metadata")
    check(rows[0]["backend"] == "qnx-native-ipc", f"{path.name}: wrong backend")
    check(rows[-1]["type"] == "summary", f"{path.name}: incomplete trace")
    meta, summary = rows[0], rows[-1]
    ticks = [row for row in rows if row["type"] == "sample"]
    check([r["sequence"] for r in ticks] == list(range(1, len(ticks) + 1)),
          f"{path.name}: sequence gap or reuse")
    check(summary["samples"] == len(ticks), f"{path.name}: incorrect sample count")
    check(summary["outputs"] == sum(r["output"] is not None for r in ticks),
          f"{path.name}: incorrect output count")
    check(summary["missing_outputs"] == len(ticks) - summary["outputs"],
          f"{path.name}: incorrect missing-output count")
    check(summary["publication_misses"] == sum(r["publication_miss"] for r in ticks),
          f"{path.name}: incorrect deadline-miss count")
    check(allow_interrupted or not summary["interrupted"], f"{path.name}: interrupted run")
    for previous, current in zip(ticks, ticks[1:]):
        check(current["time_s"] - previous["time_s"] >= meta["period_ms"] / 1000 - 1e-6,
              f"{path.name}: catch-up burst")
    for row in ticks:
        if row["baseline_pid"] > 0:
            check(row["baseline_pid"] == meta["baseline_pid"], f"{path.name}: baseline restarted")
        if row["output"] is not None:
            source = row["source"]
            check(source in ("baseline", "candidate"), f"{path.name}: invalid authority")
            check(row["output"] == row[source]["position"], f"{path.name}: output identity")
            check(row[source + "_check"] == "ok", f"{path.name}: invalid selected observation")
            check(not row["publication_miss"] and row["decision_latency_ms"] <= meta["deadline_ms"],
                  f"{path.name}: late publication")
        if row["event"] in ("candidate_rolled_back", "candidate_cancelled"):
            check(row["source"] == "baseline", f"{path.name}: fallback identity")
    if summary["phase"] == "failed":
        check(ticks[-1]["source"] == "none" and ticks[-1]["output"] is None,
              f"{path.name}: failed run did not stop output")
    validate_continuity(meta, summary, ticks)
    return rows, ticks


def event(rows, name):
    found = [r for r in rows if r.get("event") == name]
    check(bool(found), f"Missing event: {name}")
    return found[0]


def verify_scenarios(traces, exits_path, board=False):
    expected = {
        "promote": ("committed", "candidate_committed", 0),
        "rollback": ("rolled_back", "candidate_rolled_back", 0),
        "baseline-failure": ("failed", "output_stopped", 3),
        "no-fallback": ("failed", "output_stopped", 3),
        "post-commit-failure": ("failed", "output_stopped", 3),
    }
    reasons = {"units": "position_out_of_range", "delay": "deadline_exceeded",
               "nan": "non_finite_output", "stale": "stale_or_mismatched_output",
               "protocol": "protocol_mismatch", "switch": "switch_discontinuity"}
    expected.update({"reject-" + name: ("rejected", "candidate_rejected", 0) for name in reasons})
    if board:
        exits = {name: int((exits_path / f"functional-{name}.exit").read_text()) for name in expected}
    else:
        exit_rows = [line.split() for line in exits_path.read_text().splitlines()]
        check(len(exit_rows) == len(expected), "Incomplete or duplicate scenario exit records")
        exits = {name: int(code) for name, code in exit_rows}
    expected_stops = {
        "baseline-failure": (2, "baseline_worker_disconnected"),
        "no-fallback": (8, "no_valid_fallback"),
        "post-commit-failure": (13, "candidate_worker_disconnected"),
    }
    for name, (phase, expected_event, code) in expected.items():
        rows, ticks = traces["functional-" + name]
        check((rows[0]["period_ms"], rows[0]["deadline_ms"]) == (100, 75), "Scenario budget changed")
        check((rows[0]["qualification_samples"], rows[0]["probation_samples"]) == (3, 8),
              "Scenario qualification or probation changed")
        check(rows[-1]["phase"] == phase and exits[name] == code, f"{name}: unexpected outcome")
        decision = event(rows, expected_event)
        if code == 0:
            check(rows[-1]["samples"] == rows[-1]["outputs"] == 22, f"{name}: incomplete run")
        elif board:
            sequence, reason = expected_stops[name]
            check(decision["sequence"] == rows[-1]["samples"] == sequence and
                  decision["reason"] == reason and rows[-1]["outputs"] == sequence - 1,
                  f"{name}: wrong stop boundary, stop reason or pre-stop outputs")
        if board:
            check_baseline_lifetime(rows, ticks, name)
        if name.startswith("reject-"):
            check(decision["reason"] == reasons[name[7:]], f"{name}: wrong rejection reason")
            check(all(t["source"] == "baseline" for t in ticks), f"{name}: rejected candidate selected")
        if name == "promote":
            check(event(rows, "candidate_promoted")["sequence"] == 4, "Wrong promotion boundary")
        if name in ("rollback", "no-fallback"):
            check(decision["sequence"] == 8, f"{name}: failed before injected exit")
        if name == "post-commit-failure":
            check(event(rows, "candidate_committed")["sequence"] < decision["sequence"],
                  "Post-commit failure happened before commit")
    return expected


def check_baseline_lifetime(rows, ticks, name):
    committed = next((r["sequence"] for r in ticks if r["event"] == "candidate_committed"), len(ticks))
    baseline_pid = rows[0]["baseline_pid"]
    check(baseline_pid > 0, f"{name}: missing initial baseline PID")
    check(all(t["baseline_pid"] == (baseline_pid if t["sequence"] <= committed else -1) for t in ticks),
          f"{name}: baseline PID or retirement boundary changed")


def verify_live(traces, cases, board=False):
    for name, generation in cases:
        rows, ticks = traces[name]
        check(rows[-1]["phase"] == "committed", f"{name}: not committed")
        check(rows[-1]["samples"] == rows[-1]["outputs"] == 100, f"{name}: missing output")
        commit = event(rows, "candidate_committed")
        check(commit["generation"] == generation, f"{name}: wrong generation")
        ready_events = [r for r in rows if r.get("event") == "replay_complete"]
        if board:
            check_baseline_lifetime(rows, ticks, name)
            check(any(r["generation"] == generation for r in ready_events), f"{name}: missing replay")
        for ready in ready_events:
            check(ready["replayed"] >= 16 and ready["through_sequence"] == ready["after_sequence"],
                  f"{name}: incomplete replay")
            promoted = next(r for r in rows if r.get("event") == "candidate_promoted"
                            and r["generation"] == ready["generation"])
            check(promoted["sequence"] == ready["after_sequence"] + rows[0]["qualification_samples"] + 1,
                  f"{name}: replay counted toward live qualification")
            if board and ready["generation"] == generation:
                check(commit["sequence"] == promoted["sequence"] + rows[0]["probation_samples"] - 1,
                      f"{name}: wrong probation boundary")


def verify_lifecycle(root, traces):
    event(traces["cancel-retry"][0], "candidate_cancelled")
    replay_rows, replay_ticks = traces["replay-retry"]
    failures = [r for r in replay_rows if r.get("event") == "preparation_failed"]
    check([r["generation"] for r in failures] == list(range(1, 7)), "Missing repeated replay rejection")
    check([r["reason"] for r in failures] == ["replay_deadline_exceeded", "replay_stale_or_mismatched_output"] * 3,
          "Incorrect replay failure reasons")
    check(all(r["source"] == "baseline" for r in replay_ticks if r["generation"] < 7),
          "Rejected replay generation gained authority")

    def connections(name):
        text = (root / (name + ".fds")).read_text()
        return [(fd, int(pid)) for fd, pid in re.findall(r"^\s+(\d+s?)\s+(\d+)\s", text, re.M)]

    for name in ("cancel-retry", "replay-retry"):
        rows, ticks = traces[name]
        initial = connections(name + "-baseline")
        baseline_pid = rows[0]["baseline_pid"]
        check(any(fd.endswith("s") and pid == baseline_pid for fd, pid in initial), "Missing native baseline connection")
        stages = ["cancelled"] if name == "cancel-retry" else [f"rejected-{g}" for g in range(1, 7)]
        for stage in stages:
            check(connections(name + "-" + stage) == initial, f"{name}/{stage}: leaked connection or pipe")
        committed = event(rows, "candidate_committed")
        final = connections(name + "-committed")
        check(not any(pid == baseline_pid for _, pid in final), f"{name}: baseline connection survived commit")
        check(any(fd.endswith("s") and pid == committed["candidate_pid"] for fd, pid in final),
              f"{name}: missing committed native connection")
        pids = {r.get("candidate_pid", -1) for r in rows} | {baseline_pid}
        for stage in ("finished",):
            processes = (root / f"{name}-{stage}.processes").read_text()
            remaining = {int(pid) for pid in re.findall(r"^\s*(\d+)\s", processes, re.M)}
            check(not (pids & remaining), f"{name}: child remains after broker exit")


def verify_timeout(root, traces, kinds, board=False):
    timeout_exits = dict(line.split() for line in (root / "timeout-exits.txt").read_text().splitlines())
    for kind in kinds:
        rows, ticks = traces["timeout-" + kind]
        check((rows[0]["period_ms"], rows[0]["deadline_ms"]) == (1000, 150), "Timeout probe budget changed")
        check(timeout_exits[kind] == "0" and rows[-1]["phase"] == "rejected", "Timeout probe failed")
        check(rows[-1]["samples"] == rows[-1]["outputs"] == 4, "Timeout probe lost output")
        rejected = event(rows, "candidate_rejected")
        check(rejected["sequence"] == 2 and rejected["reason"] == "deadline_exceeded", "Missing native timeout")
        check(112.5 <= rejected["candidate"]["latency_ms"] < 250, "Timeout did not precede the injected reply")
        check(all(t["candidate_pid"] == -1 and t["candidate"] is None for t in ticks[2:]), "Timed-out child reused")
        if board:
            check_baseline_lifetime(rows, ticks, kind)
            check(all(t["source"] == "baseline" for t in ticks), f"{kind}: candidate gained authority")
    observations = {}
    for kind in ("stopped", "reply-observed"):
        states = (root / f"timeout-{kind}.threads").read_text()
        if board:
            candidate_pid = traces["timeout-" + kind][0][0]["candidate_pid"]
            broker_pid = int((root / f"timeout-{kind}.broker-pid").read_text())
            wait = re.compile(rf"^ *{broker_pid} +(\d+) +.* +REPLY +{candidate_pid}(?: +|$)", re.M)
            observations[kind] = any(int(thread) > 1 for thread in wait.findall(states))
        else:
            check(re.search(r"\sREPLY\s", states), f"{kind}: REPLY state was not captured")
    check(not re.search(r"shadow-(?:demo|worker|candidate)", (root / "timeout-finished.processes").read_text()),
          "Timeout probe left a process")
    return observations


def verify_pi4(root):
    traces = {p.stem: load(p) for p in sorted(root.glob("functional-*.jsonl"))}
    lifecycle = root / "lifecycle"
    traces.update({p.stem: load(p) for p in sorted(lifecycle.glob("*.jsonl"))})
    expected = verify_scenarios(traces, root, board=True)
    for name in ("policy", "staging", "functional-live", "lifecycle", "timeout"):
        check(int((root / f"{name}.exit").read_text()) == 0, f"{name}: unexpected exit")
    for name, qualify, probation in (("functional-live", 20, 40), ("cancel-retry", 3, 15),
                                     ("replay-retry", 3, 15)):
        meta = traces[name][0][0]
        check((meta["period_ms"], meta["deadline_ms"], meta["qualification_samples"], meta["probation_samples"])
              == (200, 150, qualify, probation), f"{name}: live run settings changed")
    stage = event(traces["functional-live"][0], "stage_requested")
    check(stage["after_sequence"] == 20, "Wrong late-attachment sequence")
    verify_live(traces, (("functional-live", 1), ("cancel-retry", 2), ("replay-retry", 7)), board=True)
    verify_lifecycle(lifecycle, traces)
    final_processes = (root / "final.processes").read_text()
    check(not re.search(r"shadow-(?:demo|worker|candidate)|qnx-ipc-(?:tests|receiver)", final_processes),
          "Final process leak")
    observations = verify_timeout(lifecycle, traces, ("stopped", "reply-observed"), board=True)
    print(json.dumps({"functional_scenarios_passed": len(expected), "live_runs_passed": 3,
                      "legacy_timeout_behavior_passed": 2,
                      "delayed_reply_state": "observed" if observations["reply-observed"] else "inconclusive"},
                     indent=2))
    return 0 if observations["reply-observed"] else 77


def verify(root):
    traces = {p.stem: load(p) for p in sorted(root.glob("*.jsonl"))}
    expected = verify_scenarios(traces, root / "functional-exits.txt")
    verify_live(traces, (("default-live", 1), ("functional-live", 1),
                         ("cancel-retry", 2), ("replay-retry", 7)))
    verify_lifecycle(root, traces)
    final_processes = (root / "final.processes").read_text()
    check(not re.search(r"shadow-(?:demo|worker|candidate)|qnx-ipc-(?:tests|receiver)", final_processes),
          "Final process leak")
    verify_timeout(root, traces, ("send", "reply", "stopped", "reply-observed"))
    report = {"native_trace_count": len(traces), "functional_scenarios_passed": len(expected),
              "live_and_lifecycle_checks_passed": 4,
              "connection_cleanup_checks_passed": True,
              "timeout_probes_passed": 4, "observed_timeout_wait_state": "REPLY",
              "isolated_send_timeout": "unverified",
              "default_scenarios": {name: {"phase": traces["default-" + name][0][-1]["phase"],
                 "last_reason": traces["default-" + name][1][-1]["reason"]} for name in expected}}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--pi4-functional":
        sys.exit(verify_pi4(Path(sys.argv[2])))
    verify(Path(sys.argv[1]))
