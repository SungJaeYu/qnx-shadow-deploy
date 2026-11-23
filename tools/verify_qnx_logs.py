#!/usr/bin/env python3
"""Check exported native traces without running QNX binaries on the host."""
import json
from pathlib import Path
import re
import sys


def check(condition, description):
    if not condition:
        raise ValueError(description)


def load(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
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
    check(not summary["interrupted"], f"{path.name}: interrupted run")
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
    return rows, ticks


def event(rows, name):
    found = [r for r in rows if r.get("event") == name]
    check(bool(found), f"Missing event: {name}")
    return found[0]


def verify(root):
    traces = {p.stem: load(p) for p in sorted(root.glob("*.jsonl"))}
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
    exit_rows = [line.split() for line in (root / "functional-exits.txt").read_text().splitlines()]
    check(len(exit_rows) == len(expected), "Incomplete or duplicate scenario exit records")
    exits = {name: int(code) for name, code in exit_rows}
    for name, (phase, expected_event, code) in expected.items():
        rows, ticks = traces["functional-" + name]
        check((rows[0]["period_ms"], rows[0]["deadline_ms"]) == (100, 75), "Scenario budget changed")
        check((rows[0]["qualification_samples"], rows[0]["probation_samples"]) == (3, 8),
              "Scenario qualification or probation changed")
        check(rows[-1]["phase"] == phase and exits[name] == code, f"{name}: unexpected outcome")
        decision = event(rows, expected_event)
        if code == 0:
            check(rows[-1]["samples"] == rows[-1]["outputs"] == 22, f"{name}: incomplete run")
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
    for name, generation in (("default-live", 1), ("functional-live", 1),
                             ("cancel-retry", 2), ("replay-retry", 7)):
        rows, ticks = traces[name]
        check(rows[-1]["phase"] == "committed", f"{name}: not committed")
        check(rows[-1]["samples"] == rows[-1]["outputs"] == 100, f"{name}: missing output")
        check(event(rows, "candidate_committed")["generation"] == generation, f"{name}: wrong generation")
        for ready in (r for r in rows if r.get("event") == "replay_complete"):
            check(ready["replayed"] >= 16 and ready["through_sequence"] == ready["after_sequence"],
                  f"{name}: incomplete replay")
            promoted = next(r for r in rows if r.get("event") == "candidate_promoted"
                            and r["generation"] == ready["generation"])
            check(promoted["sequence"] == ready["after_sequence"] + rows[0]["qualification_samples"] + 1,
                  f"{name}: replay counted toward live qualification")
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
    final_processes = (root / "final.processes").read_text()
    check(not re.search(r"shadow-(?:demo|worker|candidate)", final_processes), "Final process leak")
    timeout_exits = dict(line.split() for line in (root / "timeout-exits.txt").read_text().splitlines())
    for kind in ("send", "reply", "stopped", "reply-observed"):
        rows, ticks = traces["timeout-" + kind]
        check((rows[0]["period_ms"], rows[0]["deadline_ms"]) == (1000, 150), "Timeout probe budget changed")
        check(timeout_exits[kind] == "0" and rows[-1]["phase"] == "rejected", "Timeout probe failed")
        check(rows[-1]["samples"] == rows[-1]["outputs"] == 4, "Timeout probe lost output")
        rejected = event(rows, "candidate_rejected")
        check(rejected["sequence"] == 2 and rejected["reason"] == "deadline_exceeded", "Missing native timeout")
        check(112.5 <= rejected["candidate"]["latency_ms"] < 250, "Timeout did not precede the injected reply")
        check(all(t["candidate_pid"] == -1 and t["candidate"] is None for t in ticks[2:]), "Timed-out child reused")
    for kind in ("stopped", "reply-observed"):
        states = (root / f"timeout-{kind}.threads").read_text()
        check(re.search(r"\sREPLY\s", states), f"{kind}: REPLY state was not captured")
    check(not re.search(r"shadow-(?:demo|worker|candidate)", (root / "timeout-finished.processes").read_text()),
          "Timeout probe left a process")
    report = {"native_trace_count": len(traces), "functional_scenarios_passed": len(expected),
              "live_and_lifecycle_checks_passed": 4,
              "connection_cleanup_checks_passed": True,
              "timeout_probes_passed": 4, "observed_timeout_wait_state": "REPLY",
              "isolated_send_timeout": "unverified",
              "default_scenarios": {name: {"phase": traces["default-" + name][0][-1]["phase"],
                 "last_reason": traces["default-" + name][1][-1]["reason"]} for name in expected}}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
