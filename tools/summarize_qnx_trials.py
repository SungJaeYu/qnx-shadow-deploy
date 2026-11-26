#!/usr/bin/env python3
"""Summarize fixed native QNX trial plans without emitting raw trace records."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys

from verify_qnx_logs import TraceValidationError, check, validate_rows


def quantiles(values):
    ordered = sorted(values)
    return {
        label: ordered[math.ceil(len(ordered) * p) - 1] if ordered else None
        for label, p in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99), ("max", 1))
    }


def validate_plan(values):
    trials, samples, period, deadline, loops = values
    check(0 < trials <= 100000 and 0 < samples <= 100000
          and 0 < deadline < period <= 1000 and 0 < loops <= 64, "invalid trial plan")
    return values


def legacy_exits(root):
    entries = {}
    for line in (root / "exits.txt").read_text().splitlines():
        fields = line.split()
        if fields and re.fullmatch(r"(?:baseline|load)-[1-9][0-9]*", fields[0]):
            name, status = fields
            check(name not in entries, "duplicate legacy process exit")
            entries[name] = int(status)
    return entries


def process_snapshot(path):
    lines = path.read_text().splitlines()
    check(lines and lines[0].lower().split() == ["pid", "arguments"], "invalid process snapshot header")
    pids = set()
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split(maxsplit=1)
        check(len(fields) == 2 and re.fullmatch(r"[1-9][0-9]*", fields[0]), "invalid process snapshot row")
        pid = int(fields[0])
        check(pid not in pids, "duplicate PID in process snapshot")
        pids.add(pid)
    check(pids, "empty process snapshot")
    return pids


def binary_checksums(path):
    entries = {}
    for line in path.read_text().splitlines():
        fields = line.split(maxsplit=2)
        check(len(fields) == 3 and fields[0].isascii() and fields[0].isdecimal()
              and re.fullmatch(r"[1-9][0-9]*", fields[1]), "invalid binary checksum record")
        checksum, size, binary = fields
        name = Path(binary).name
        check(0 <= int(checksum) <= 0xFFFFFFFF and Path(binary).is_absolute()
              and name in ("shadow-demo", "shadow-worker") and name not in entries,
              "invalid binary checksum identity")
        entries[name] = (int(checksum), int(size), binary)
    check(len(entries) == 2, "missing binary checksum")
    return entries


def environment_metadata(path):
    fields = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        check(separator and re.fullmatch(r"[a-z][a-z0-9_]*", key) and value.strip() and key not in fields,
              "invalid environment metadata field")
        fields[key] = value.strip()
    check(fields.get("target") in ("qemu", "pi4"), "missing target identity")
    mapped = fields.get("build_provenance")
    revision = fields.get("source_revision", "")
    check((mapped == "source_mapped" and re.fullmatch(r"[0-9a-f]{40}", revision))
          or (mapped == "unmapped" and revision == "unknown"), "invalid source provenance")
    check(re.fullmatch(r"[0-9a-f]{64}", fields.get("image_sha256", "")), "invalid image SHA-256")
    if "disk_sha256" in fields:
        check(re.fullmatch(r"[0-9a-f]{64}", fields["disk_sha256"]), "invalid disk SHA-256")
    return {key: fields[key] for key in ("target", "build_provenance", "source_revision", "image_sha256")}


def controlled_context(root):
    context = {
        "environment_file": "environment.txt",
        "identity_file": "uname.txt",
        "cpu_and_memory_file": "pidin-info.txt",
        "binary_checksum_file": "binaries.cksum",
        "provenance_valid": False,
        "binary_checksums_unchanged": False,
        "collector_completed": False,
    }
    try:
        context.update(environment_metadata(root / "environment.txt"))
        uname = (root / "uname.txt").read_text().strip()
        check(re.fullmatch(r"QNX\s+[^\s]+\s+[0-9.]+\s+[^\n]+", uname), "invalid QNX uname record")
        if context["target"] == "pi4":
            check(uname.split()[-1].lower() in ("aarch64", "aarch64le"), "invalid Pi architecture")
        info = (root / "pidin-info.txt").read_text()
        check(re.search(r"CPU:\S+\s+Release:\S+.*FreeMem:", info)
              and re.search(r"^Processor[1-9][0-9]*:", info, re.M), "invalid QNX pidin info")
        process_snapshot(root / "initial.processes")
        initial = binary_checksums(root / "binaries.cksum")
        context["provenance_valid"] = True
        final = binary_checksums(root / "final-binaries.cksum")
        context["binary_checksums_unchanged"] = final == initial
        status = (root / "collection-status").read_text().strip()
        check(status in ("complete", "interrupted", "aborted_cleanup"), "invalid collection status")
        context["collector_completed"] = status == "complete"
    except TraceValidationError as error:
        context["error"] = str(error)
    except (OSError, ValueError) as error:
        context["error"] = type(error).__name__
    return context


def owned_processes(root, name, baseline_pid, expected_loops):
    broker = int((root / f"{name}.broker-pid").read_text())
    loads = [int(pid) for pid in (root / f"{name}.load-pids").read_text().split()]
    owned = {broker, baseline_pid, *loads}
    check(min(owned) > 0 and len(owned) == 2 + len(loads), "invalid owned PIDs")
    check(len(loads) == expected_loops, "wrong number of load processes")
    check((root / f"{name}.load-complete").read_text().strip() == "yes", "load process exited early")
    check((root / f"{name}.cleanup").read_text().strip() == "verified", "collector cleanup unverified")
    check(not (root / f"{name}.unreaped-pids").exists(), "collector had unreaped children")
    running = process_snapshot(root / f"{name}.processes")
    check(not (owned & running), "owned process visible after trial")


def verify_baseline_only(meta, summary, ticks, row_count):
    check(meta["scenario"] == "live-deployment" and meta["schema"] == 2
          and meta["baseline_pid"] > 0 and meta["candidate_pid"] == -1
          and meta["position_unit"] == "m" and "input_source" not in meta
          and meta["ipc_budget_ms"] == 0.75 * meta["deadline_ms"]
          and (meta["qualification_samples"], meta["probation_samples"]) == (20, 40)
          and row_count == len(ticks) + 2 and summary["phase"] in ("idle", "failed"),
          "trial was not baseline-only")
    checks = {
        "ok", "unavailable", "deadline_exceeded", "worker_disconnected", "protocol_mismatch",
        "stale_or_mismatched_output", "non_finite_output", "position_out_of_range",
        "velocity_out_of_range", "position_step_exceeded", "measurement_residual_exceeded",
    }
    for index, row in enumerate(ticks):
        check(row["candidate"] is None and row["candidate_pid"] == -1
              and row["baseline_pid"] == meta["baseline_pid"]
              and type(row["generation"]) is int and row["generation"] == 0,
              "candidate present or baseline PID changed")
        failed = index == len(ticks) - 1 and summary["phase"] == "failed"
        baseline = row["baseline_check"]
        candidate = row["candidate_check"]
        reason = row["reason"]
        if failed:
            check(row["phase"] == "failed" and row["source"] == "none"
                  and row["event"] == "output_stopped" and row["output"] is None, "invalid terminal sample")
            invalid_input = baseline == candidate == ""
            check(invalid_input or (baseline in checks and candidate == "not_running"),
                  "invalid terminal worker checks")
            if row["publication_miss"]:
                check(reason == "decision_deadline_exceeded", "decision miss reason mismatch")
            elif invalid_input:
                check(reason == "invalid_or_non_monotonic_input", "invalid input reason mismatch")
            elif baseline == "ok":
                check(reason == "output_discontinuity", "output stop reason mismatch")
            else:
                check(reason == "baseline_" + baseline, "baseline stop reason mismatch")
        else:
            check(row["phase"] == "idle" and row["source"] == "baseline" and row["output"] is not None
                  and row["event"] == reason == "" and baseline == "ok" and candidate == "not_running",
                  "invalid baseline output semantics")


def measure(ticks, meta):
    latencies = [row["decision_latency_ms"] for row in ticks]
    release = [row["release_lateness_ms"] for row in ticks]
    ipc = [row["baseline"]["latency_ms"] for row in ticks if row["baseline"] is not None]
    for value in latencies + release + ipc:
        check(type(value) in (int, float) and math.isfinite(value) and value >= 0,
              "invalid timing measurement")
    failures = []
    for row in ticks:
        check(type(row["sequence"]) is int and type(row["time_s"]) in (int, float)
              and math.isfinite(row["time_s"]) and row["time_s"] >= 0, "invalid sample identity")
        output = row["output"]
        check(output is None or (type(output) in (int, float) and math.isfinite(output)),
              "invalid output measurement")
        miss = row["publication_miss"]
        check(type(miss) is bool, "invalid publication miss flag")
        # The producer's printed precision can round a near-boundary miss to the deadline.
        decision_ms = row["decision_latency_ms"]
        budget = meta["deadline_ms"]
        check((miss and decision_ms >= budget) or (not miss and decision_ms <= budget),
              "decision timing conflicts with miss flag")
        checks = [row[key] for key in ("baseline_check", "candidate_check")]
        if miss or "deadline_exceeded" in checks:
            failures.append({
                "sequence": row["sequence"],
                "decision_deadline_miss": miss,
                "baseline_deadline_failure": checks[0] == "deadline_exceeded",
                "candidate_deadline_failure": checks[1] == "deadline_exceeded",
                "stop": row["event"] == "output_stopped",
            })
    return {
        "decision_latency_ms": quantiles(latencies),
        "release_lateness_ms": quantiles(release),
        "baseline_acquisition_to_ipc_completion_ms": quantiles(ipc),
        "baseline_ipc_observations": len(ipc),
        "deadline_failure_samples": failures,
        "unexpected_deadline_failure_samples": len(failures),
        "decision_deadline_misses": sum(row["decision_deadline_miss"] for row in failures),
    }


def trial_continuity(meta, summary, count, deadline_failures):
    if "requested_inputs" not in meta:
        return None
    check(meta["requested_inputs"] == count, "continuity request count differs from trial plan")
    continuity = {key: summary[key] for key in (
        "initial_unoccupied_slots", "skipped_nominal_slots", "max_acquisition_interval_ms",
        "max_scheduling_lateness_ms", "max_nominal_grid_lateness_ms",
        "max_selected_interval_ms", "effective_selected_rate_hz", "external_delivery",
    )}
    continuity["passed"] = (summary["acquired_inputs"] == summary["selected_outputs"] == count
                            and not summary["initial_unoccupied_slots"]
                            and not summary["skipped_nominal_slots"]
                            and not deadline_failures)
    return continuity


def trial(root, name, condition, count, period, deadline, loops, legacy, exits):
    result = {
        "trial": name, "condition": condition, "busy_loops": loops, "requested_samples": count,
        "exit_code": None, "status": "missing", "stop_reason": None, "process_cleanup_checked": False,
        "observed_samples": None, "observed_outputs": None, "missing_outputs_vs_requested": None,
        "trace_observed_samples": None, "trace_observed_outputs": None, "trial_fully_validated": False,
    }
    watchdog_validated = False
    path = root / f"{name}.jsonl"
    try:
        stop_marker = root / f"{name}.operator-stop"
        result["operator_stop_requested"] = not legacy and stop_marker.is_file()
        watchdog = root / f"{name}.watchdog"
        result["watchdog_marker_present"] = not legacy and watchdog.is_file()
        result["watchdog_expired"] = False
        if result["watchdog_marker_present"]:
            expected_polls = (count * period + 999) // 1000 + 10
            check(watchdog.read_text().strip() == str(expected_polls), "invalid watchdog marker")
            watchdog_validated = True
            result["watchdog_expired"] = True
            result["status"] = "watchdog_expired"
        if not path.is_file():
            return result
        # Retain counts of framed records before checking terminal trial evidence.
        rows = []
        with path.open() as file:
            result.update(trace_observed_samples=0, trace_observed_outputs=0)
            for number, line in enumerate(file, 1):
                if not line.endswith("\n"):
                    if not watchdog_validated:
                        result["status"] = "partial"
                    result["error"] = f"unterminated trace record at line {number}"
                    return result
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    if not watchdog_validated:
                        result["status"] = "invalid"
                    result["error"] = f"malformed JSON at line {number}"
                    return result
                check(isinstance(row, dict), "trace record must be an object")
                rows.append(row)
                if row.get("type") == "sample":
                    result["trace_observed_samples"] += 1
                    output = row.get("output")
                    if type(output) in (int, float) and math.isfinite(output):
                        result["trace_observed_outputs"] += 1
        if not rows or rows[-1].get("type") != "summary":
            if not watchdog_validated:
                result["status"] = "partial"
            return result
        check(all(row.get("type") not in ("meta", "summary") for row in rows[1:-1]),
              "duplicate metadata or summary")
        interrupted = rows[-1].get("interrupted") is True
        rows, ticks = validate_rows(path, rows, allow_interrupted=interrupted)
        meta, summary = rows[0], rows[-1]
        check(type(summary["interrupted"]) is bool, "invalid interruption flag")
        check(all(type(summary[key]) is int for key in
                  ("samples", "outputs", "missing_outputs", "publication_misses")),
              "invalid summary counts")
        check(all(type(meta[key]) is int for key in ("period_ms", "deadline_ms", "baseline_pid",
                                                    "candidate_pid")), "invalid numeric metadata")
        check((meta["period_ms"], meta["deadline_ms"]) == (period, deadline), "timing plan mismatch")
        check((ticks or interrupted) and len(ticks) <= count, "sample plan mismatch")
        check(interrupted or summary["phase"] == "failed" or len(ticks) == count,
              "complete trial did not reach requested sample count")
        check(not ticks or summary["phase"] == ticks[-1]["phase"], "inconsistent terminal phase")
        if legacy:
            check(meta["scenario"] == "rollback" and meta["candidate_pid"] > 0, "not a legacy rollback trace")
        else:
            verify_baseline_only(meta, summary, ticks, len(rows))
        reason = None
        if summary["phase"] == "failed":
            check(ticks[-1]["event"] == "output_stopped" and summary["missing_outputs"] == 1
                  and all(row["phase"] != "failed" and row["event"] != "output_stopped"
                          for row in ticks[:-1]), "invalid output stop")
            reason = ticks[-1]["reason"]
            check(isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", reason),
                  "invalid output stop reason")
        else:
            check(all(row["event"] != "output_stopped" for row in ticks), "unreported output stop")

        exit_path = root / f"{name}.exit"
        if legacy:
            result["exit_code"] = exits.get(name)
        elif exit_path.exists():
            status = exit_path.read_text().strip()
            check(re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", status) and int(status) <= 255,
                  "invalid broker exit record")
            result["exit_code"] = int(status)
        expected_exit = 130 if interrupted else (3 if summary["phase"] == "failed" else 0)
        check(result["exit_code"] is None or result["exit_code"] == expected_exit,
              "broker exit conflicts with trace termination")
        measurements = measure(ticks, meta)
        continuity = trial_continuity(meta, summary, count, measurements["unexpected_deadline_failure_samples"])
        result.update(measurements, continuity=continuity)
        result.update(observed_samples=len(ticks), observed_outputs=summary["outputs"],
                      missing_outputs_vs_requested=count - summary["outputs"],
                      observed_samples_without_output=summary["missing_outputs"],
                      unobserved_requested_samples=count - len(ticks), stop_reason=reason)
        if watchdog_validated:
            return result
        if not legacy:
            check((root / f"{name}.stderr").is_file(), "missing broker stderr")
            if result["operator_stop_requested"]:
                check(stop_marker.read_text().strip() == "requested", "invalid operator stop marker")
            times = []
            for suffix in ("started", "finished"):
                timestamp = (root / f"{name}.{suffix}").read_text().strip()
                check(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", timestamp),
                      "invalid trial timestamp")
                times.append(datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ"))
                result[suffix + "_utc"] = timestamp
            check(times[0] <= times[1], "reversed trial timestamps")
            owned_processes(root, name, meta["baseline_pid"], loops)
            result["process_cleanup_checked"] = True

        result["trial_fully_validated"] = result["exit_code"] is not None
        if result["exit_code"] is None:
            result["status"] = "partial"
        elif interrupted:
            result["status"] = "operator_stopped" if result["operator_stop_requested"] else "interrupted"
        elif result["operator_stop_requested"]:
            result["status"] = "partial"
        elif summary["phase"] == "failed" and result["exit_code"] == 3:
            result["status"] = "unexpected_stop"
        elif (result["exit_code"] == 0 and summary["phase"] != "failed"
              and summary["outputs"] == count and not result["unexpected_deadline_failure_samples"]
              and (continuity is None or continuity["passed"])):
            result["status"] = "pass"
        else:
            result["status"] = "failed"
    except TraceValidationError as error:
        if not watchdog_validated:
            result["status"] = "invalid"
        result["error"] = str(error)
    except FileNotFoundError:
        if not watchdog_validated:
            result["status"] = "partial"
        result["error"] = "missing trial sidecar"
    except (ValueError, KeyError, IndexError, TypeError, OverflowError, OSError) as error:
        if not watchdog_validated:
            result["status"] = "invalid"
        result["error"] = type(error).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--legacy-plan", type=int, nargs=5,
                        metavar=("TRIALS", "SAMPLES", "PERIOD_MS", "DEADLINE_MS", "LOOPS"),
                        help="Analyze historical candidate-enabled rollback traces and exits.txt")
    args = parser.parse_args()
    root = args.directory
    legacy = args.legacy_plan is not None
    try:
        plan = args.legacy_plan if legacy else [int(x) for x in (root / "plan.txt").read_text().split()]
        count, samples, period, deadline, loops = validate_plan(plan)
        exits = legacy_exits(root) if legacy and (root / "exits.txt").is_file() else {}
        context = None if legacy else controlled_context(root)
    except (OSError, ValueError):
        parser.exit(2, "Missing or invalid collection plan or metadata.\n")

    conditions = ("baseline", "load") if legacy else ("idle", "cpu")
    results = [
        trial(root, f"{name}-{index}", condition, samples, period, deadline, load_count, legacy, exits)
        for index in range(1, count + 1)
        for name, condition, load_count in zip(conditions, ("idle", "cpu"), (0, loops))
    ]
    print(json.dumps({
        "workload": "legacy_candidate_enabled_rollback" if legacy else "baseline_only",
        "period_ms": period, "deadline_ms": deadline, "samples_per_trial": samples,
        "requested_trials_per_condition": count, "planned_stops": 0,
        "context": context,
        "conditions": {
            name: {
                "requested_attempts": count,
                "requested_samples": count * samples,
                "trace_observed_samples": sum(row["trace_observed_samples"] or 0 for row in runs),
                "trace_observed_outputs": sum(row["trace_observed_outputs"] or 0 for row in runs),
                "attempts_with_readable_trace": sum(row["trace_observed_samples"] is not None for row in runs),
                "fully_validated_attempts": sum(row["trial_fully_validated"] for row in runs),
                "observed_samples": sum(row["observed_samples"] or 0 for row in runs),
                "observed_outputs": sum(row["observed_outputs"] or 0 for row in runs),
                "missing_outputs_vs_requested": (sum(row["missing_outputs_vs_requested"] for row in runs)
                                                  if all(row["missing_outputs_vs_requested"] is not None
                                                         for row in runs) else None),
                "attempts_without_validated_counts": sum(row["observed_samples"] is None for row in runs),
                "unexpected_deadline_failure_samples_observed":
                    sum(row.get("unexpected_deadline_failure_samples", 0) for row in runs),
                "passed_attempts": sum(row["status"] == "pass" for row in runs),
            }
            for name in ("idle", "cpu")
            for runs in [[row for row in results if row["condition"] == name]]
        },
        "trials": results,
    }, indent=2, allow_nan=False))
    if context is not None and not all(context[key] for key in (
            "provenance_valid", "binary_checksums_unchanged", "collector_completed")):
        return 1
    return 0 if all(row["status"] == "pass" for row in results) else 1


if __name__ == "__main__":
    sys.exit(main())
