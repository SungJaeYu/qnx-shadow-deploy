#!/usr/bin/env python3
"""Summarize fixed native QNX trial plans without emitting raw trace records."""
import argparse
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


def controlled_context(root):
    files = ("environment.txt", "uname.txt", "pidin-info.txt", "initial.processes",
             "binaries.cksum")
    present = all((root / name).is_file() and (root / name).stat().st_size for name in files)
    stable = ((root / "final-binaries.cksum").is_file() and (root / "binaries.cksum").is_file()
              and (root / "final-binaries.cksum").read_bytes() == (root / "binaries.cksum").read_bytes())
    status = root / "collection-status"
    return {
        "environment_file": "environment.txt",
        "identity_file": "uname.txt",
        "cpu_and_memory_file": "pidin-info.txt",
        "binary_checksum_file": "binaries.cksum",
        "provenance_files_present": bool(present),
        "binary_checksums_unchanged": stable,
        "collector_completed": status.is_file() and status.read_text().strip() == "complete",
    }


def owned_processes(root, name, baseline_pid, expected_loops):
    broker = int((root / f"{name}.broker-pid").read_text())
    loads = [int(pid) for pid in (root / f"{name}.load-pids").read_text().split()]
    owned = {broker, baseline_pid, *loads}
    check(min(owned) > 0 and len(owned) == 2 + len(loads), "invalid owned PIDs")
    check(len(loads) == expected_loops, "wrong number of load processes")
    check((root / f"{name}.load-complete").read_text().strip() == "yes", "load process exited early")
    processes = (root / f"{name}.processes").read_text().splitlines()
    running = {int(line.split()[0]) for line in processes if line.split() and line.split()[0].isdecimal()}
    check(running, "missing process snapshot")
    check(not (owned & running), "owned process visible after trial")


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
        check(miss or row["decision_latency_ms"] <= meta["deadline_ms"], "unreported decision miss")
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


def trial(root, name, condition, count, period, deadline, loops, legacy, exits):
    result = {
        "trial": name, "condition": condition, "busy_loops": loops, "requested_samples": count,
        "exit_code": None, "status": "missing", "stop_reason": None, "process_cleanup_checked": False,
        "observed_samples": None, "observed_outputs": None, "missing_outputs_vs_requested": None,
    }
    path = root / f"{name}.jsonl"
    try:
        stop_marker = root / f"{name}.operator-stop"
        result["operator_stop_requested"] = not legacy and stop_marker.is_file()
        status_path = root / f"{name}.exit"
        if legacy:
            result["exit_code"] = exits.get(name)
        elif status_path.is_file():
            result["exit_code"] = int(status_path.read_text())
        if not path.is_file():
            return result
        # Inspect framing before the strict verifier rejects incomplete/interrupted files.
        rows = []
        with path.open() as file:
            for number, line in enumerate(file, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    result["status"] = "invalid" if line.endswith("\n") else "partial"
                    result["error"] = f"malformed JSON at line {number}"
                    return result
                check(isinstance(row, dict), "trace record must be an object")
                rows.append(row)
        if not rows or rows[-1].get("type") != "summary":
            result["status"] = "partial"
            return result
        check(all(row.get("type") not in ("meta", "summary") for row in rows[1:-1]),
              "duplicate metadata or summary")
        interrupted = rows[-1].get("interrupted") is True and result["exit_code"] == 130
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
        check(not ticks or summary["phase"] == ticks[-1]["phase"], "inconsistent terminal phase")
        if legacy:
            check(meta["scenario"] == "rollback" and meta["candidate_pid"] > 0, "not a legacy rollback trace")
        else:
            check(meta["scenario"] == "live-deployment" and meta["candidate_pid"] == -1
                  and meta["baseline_pid"] > 0 and len(rows) == len(ticks) + 2
                  and all(row["candidate_pid"] == -1 and row["candidate"] is None
                          and row["baseline_pid"] == meta["baseline_pid"]
                          and row["phase"] in ("idle", "failed")
                          and row["source"] in ("baseline", "none") for row in ticks),
                  "trial was not baseline-only")
        result.update(measure(ticks, meta))
        result.update(observed_samples=len(ticks), observed_outputs=summary["outputs"],
                      missing_outputs_vs_requested=count - summary["outputs"],
                      observed_samples_without_output=summary["missing_outputs"],
                      unobserved_requested_samples=count - len(ticks))

        if summary["phase"] == "failed":
            check(ticks[-1]["event"] == "output_stopped" and summary["missing_outputs"] == 1
                  and all(row["phase"] != "failed" and row["event"] != "output_stopped"
                          for row in ticks[:-1]), "invalid output stop")
            reason = ticks[-1]["reason"]
            check(isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", reason),
                  "invalid output stop reason")
            result["stop_reason"] = reason
        else:
            check(all(row["event"] != "output_stopped" for row in ticks), "unreported output stop")
        if not legacy:
            for suffix in ("started", "finished"):
                timestamp = (root / f"{name}.{suffix}").read_text().strip()
                check(timestamp, "missing trial timestamp")
                result[suffix + "_utc"] = timestamp
            owned_processes(root, name, meta["baseline_pid"], loops)
            result["process_cleanup_checked"] = True

        if interrupted:
            result["status"] = "operator_stopped" if result["operator_stop_requested"] else "interrupted"
        elif result["exit_code"] is None or result["operator_stop_requested"]:
            result["status"] = "partial"
        elif summary["phase"] == "failed" and result["exit_code"] == 3:
            result["status"] = "unexpected_stop"
        elif (result["exit_code"] == 0 and summary["phase"] != "failed"
              and summary["outputs"] == count and not result["unexpected_deadline_failure_samples"]):
            result["status"] = "pass"
        else:
            result["status"] = "failed"
    except TraceValidationError as error:
        result["status"] = "invalid"
        result["error"] = str(error)
    except FileNotFoundError:
        result["status"] = "partial"
        result["error"] = "missing trial sidecar"
    except (ValueError, KeyError, IndexError, TypeError, OverflowError, OSError) as error:
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
            "provenance_files_present", "binary_checksums_unchanged", "collector_completed")):
        return 1
    return 0 if all(row["status"] == "pass" for row in results) else 1


if __name__ == "__main__":
    sys.exit(main())
