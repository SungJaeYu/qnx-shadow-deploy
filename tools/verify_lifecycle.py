#!/usr/bin/env python3
"""Verify a fresh tools/lifecycle_checks.sh run exported from a host or guest."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re

from verify_qnx_logs import validate_continuity


def check(condition, message):
    if not condition:
        raise ValueError(message)


def only(rows, event, generation):
    matches = [row for row in rows if row.get("event") == event and
               row["generation"] == generation]
    check(len(matches) == 1, f"expected one {event} for generation {generation}")
    return matches[0]


def read_manifest(path):
    fields = {}
    with path.open(encoding="utf-8", newline="\n") as source:
        for line in source:
            line = line.removesuffix("\n").removesuffix("\r")
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            check(sep and key and value and key not in fields,
                  f"invalid or duplicate manifest field in {path.name}: {key!r}")
            fields[key] = value
    check(fields.keys() == {"format", "artifact", "executable", "protocol", "architecture",
                           "os", "contract", "replay_samples", "mode", "fault_after"},
          f"unexpected manifest field set in {path.name}")
    return fields


class NativeEvidenceUnavailable(ValueError):
    pass


def verify_native(path, broker, baseline, first, second):
    connections = {}
    for phase in ("baseline", "probation", "rollback", "committed"):
        try:
            text = (path / f"{phase}.fds").read_text()
        except OSError as error:
            raise NativeEvidenceUnavailable(f"{phase} broker fd snapshot unavailable") from error
        connections[phase] = [(fd, int(pid)) for fd, pid in
                              re.findall(r"^[ \t]*(\d+s?)[ \t]+(\d+)[ \t]", text, re.M)]
        if not {"0", "1", "2"} <= {fd for fd, _ in connections[phase]}:
            raise NativeEvidenceUnavailable(f"{phase} broker fd snapshot lacks standard descriptors")
    expected = {"baseline": {baseline}, "probation": {baseline, first},
                "rollback": {baseline}, "committed": {second}}
    for phase, pids in expected.items():
        worker_connections = [pid for fd, pid in connections[phase]
                              if fd.endswith("s") and pid in {baseline, first, second}]
        check(sorted(worker_connections) == sorted(pids), f"{phase} side-channel identities")
    check(sorted(connections["rollback"]) == sorted(connections["baseline"]),
          "rollback left extra broker connections or descriptors")
    initial_resources = Counter((fd.endswith("s"), second if pid == baseline else pid)
                                for fd, pid in connections["baseline"])
    committed_resources = Counter((fd.endswith("s"), pid) for fd, pid in connections["committed"])
    check(committed_resources == initial_resources,
          "commit left extra descriptors or changed the expected connection peers")

    def process_ids(phase):
        try:
            text = (path / f"{phase}.processes").read_text()
        except OSError as error:
            raise NativeEvidenceUnavailable(f"{phase} process list unavailable") from error
        return {int(pid) for pid, command in re.findall(r"^[ \t]*(\d+)[ \t]+(\S+)", text, re.M)
                if Path(command).name != "pidin"}

    owned = {broker, baseline, first, second}
    initial_pids, remaining_pids = process_ids("baseline"), process_ids("finished")
    check(not (remaining_pids & owned), "owned process remained after broker exit")
    initial_system = initial_pids - owned
    if not {broker, baseline} <= initial_pids or not initial_system or not initial_system <= remaining_pids:
        raise NativeEvidenceUnavailable("process snapshots lack a comparable baseline and finished system view")
    return {phase: sorted(pids) for phase, pids in expected.items()}


def verify(path, loaded=False):
    rows = [json.loads(line) for line in (path / "lifecycle.jsonl").read_text().splitlines()]
    meta, summary = rows[0], rows[-1]
    check(meta["type"] == "meta" and meta["schema"] == 2 and summary["type"] == "summary",
          "incomplete live telemetry")
    check((meta["period_ms"], meta["deadline_ms"], meta["ipc_budget_ms"]) == (200, 150, 112.5),
          "unexpected lifecycle timing configuration")
    check((meta["qualification_samples"], meta["probation_samples"]) == (3, 24),
          "unexpected lifecycle qualification or probation configuration")
    backend = meta["backend"]
    check(backend in ("host-posix-pipes", "qnx-native-ipc"), f"unexpected backend {backend}")
    check((path / "exit").read_text().strip() == "0", "broker failed")
    ticks = [row for row in rows if row["type"] == "sample"]
    requested = meta["requested_inputs"]
    check(requested == 90 and [tick["sequence"] for tick in ticks] == list(range(1, requested + 1)),
          "incomplete or missing input sequence")
    check(validate_continuity(meta, summary, ticks) is True,
          "v2 lifecycle requires continuity metrics")
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
    retirement = f"PASS child retirement: baseline={baseline} first={first} second={second}"
    check((path / "retirement").read_text().splitlines() == [retirement],
          "retirement PIDs do not match observed workers")
    first_sequence, rollback_sequence = first_promote["sequence"], rollback["sequence"]
    second_sequence, commit_sequence = second_promote["sequence"], commit["sequence"]
    check(first_promote["sequence"] < rollback["sequence"] <
          first_promote["sequence"] + meta["probation_samples"] - 1,
          "candidate crash was not observed before the generation 1 commit boundary")
    check(rollback["phase"] == "rolled_back" and rollback["candidate_check"] == "worker_disconnected" and
          rollback["candidate"]["position"] is None, "rollback did not observe candidate process loss")
    check(commit["sequence"] == second_promote["sequence"] + meta["probation_samples"] - 1,
          "generation 2 did not finish probation")
    deployment = [row for row in rows if row["type"] == "deployment"]
    check([(row["event"], row["generation"]) for row in deployment] == [
        ("stage_requested", 1), ("replay_complete", 1),
        ("stage_requested", 2), ("replay_complete", 2),
    ], "unexpected deployment event or generation chronology")
    first_stage, first_ready, second_stage, second_ready = deployment
    if loaded:
        crash = read_manifest(path / "candidate-crash.manifest")
        retry = read_manifest(path / "candidate.manifest")
        check(first_stage["after_sequence"] == 20, "loaded run must stage after sample 20")
        check(crash == {**retry, "mode": "crash", "fault_after": str(rollback_sequence)} and
              retry["mode"] == "good" and retry["fault_after"] == "0" and rollback_sequence == 38,
              "injected crash sequence or healthy retry manifest does not match the loaded run")
    boundaries = []
    for stage, ready, promoted, pid, earliest in (
        (first_stage, first_ready, first_promote, first, 20),
        (second_stage, second_ready, second_promote, second, rollback_sequence),
    ):
        staged_after, through = stage["after_sequence"], ready["through_sequence"]
        check(earliest <= staged_after < through < promoted["sequence"] and
              ready["after_sequence"] == through,
              "stage, replay and promotion boundaries out of order")
        check(stage["candidate_pid"] == -1 and ready["candidate_pid"] == pid and ready["replayed"] >= 16,
              "replay did not catch up with its own candidate")
        for event, after in ((stage, staged_after), (ready, through)):
            check(rows.index(ticks[after - 1]) < rows.index(event) < rows.index(ticks[after]),
                  "deployment event is outside its reported sample boundary")
        check(promoted["sequence"] == through + meta["qualification_samples"] + 1,
              "promotion did not follow fresh qualification")
        boundaries.append((staged_after, through))
    (stage1, through1), (stage2, through2) = boundaries
    transitions = (
        (stage1 + 1, 1, "warming", -1),
        (through1 + 1, 1, "shadowing", first),
        (first_sequence, 1, "probation", first),
        (rollback_sequence, 1, "rolled_back", first),
        (rollback_sequence + 1, 1, "rolled_back", -1),
        (stage2 + 1, 2, "warming", -1),
        (through2 + 1, 2, "shadowing", second),
        (second_sequence, 2, "probation", second),
        (commit_sequence, 2, "committed", second),
    )
    sample_milestones = {
        stage1 + 1: "candidate_staged", through1 + 1: "candidate_ready",
        first_sequence: "candidate_promoted", rollback_sequence: "candidate_rolled_back",
        stage2 + 1: "candidate_staged", through2 + 1: "candidate_ready",
        second_sequence: "candidate_promoted", commit_sequence: "candidate_committed",
    }
    expected_baseline = None
    previous_output = None
    max_output_step = 0.0
    for tick in ticks:
        sequence = tick["sequence"]
        expected_state = (0, "idle", -1)
        for boundary, generation, phase, pid in transitions:
            if sequence < boundary:
                break
            expected_state = (generation, phase, pid)
        check((tick["generation"], tick["phase"], tick["candidate_pid"]) == expected_state,
              f"generation, phase or candidate PID chronology at {sequence}")
        check(tick["event"] == sample_milestones.get(sequence, ""),
              f"unexpected or missing milestone at {sequence}")
        if expected_state[1] == "shadowing":
            check(tick["candidate_check"] == "ok", f"invalid qualification sample at {sequence}")
        if expected_state[2] == -1:
            check(tick["candidate"] is None, f"candidate observation without an active PID at {sequence}")
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
    if loaded:
        evidence["validation_scope"] = "loaded-lifecycle"
        evidence["descriptor_snapshots"] = "not collected"
    elif backend == "qnx-native-ipc":
        try:
            evidence["native_connections"] = verify_native(path, broker, baseline, first, second)
        except NativeEvidenceUnavailable as error:
            evidence["native_cleanup"] = {"verdict": "inconclusive", "reason": str(error)}
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fresh_run_dir", type=Path)
    parser.add_argument("--functional-only", action="store_true",
                        help="report nominal-grid timing without gating the host functional check")
    parser.add_argument("--loaded", action="store_true",
                        help="verify the loaded driver and its fixed crash injection without descriptor snapshots")
    args = parser.parse_args()
    try:
        evidence = verify(args.fresh_run_dir, loaded=args.loaded)
    except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
        parser.exit(1, f"FAIL {args.fresh_run_dir}: {error}\n")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    if not args.functional_only and evidence["timing"]["verdict"] != "pass":
        return 1
    return 77 if "native_cleanup" in evidence else 0


if __name__ == "__main__":
    raise SystemExit(main())
