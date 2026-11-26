#!/usr/bin/env python3
"""Verify fresh QNX operator cancellation traces and process snapshots."""

from pathlib import Path
import re
import sys

from verify_qnx_logs import check, load


def one(rows, event, generation):
    matches = [r for r in rows if r.get("event") == event and r["generation"] == generation]
    check(len(matches) == 1, f"expected exactly one {event} for generation {generation}")
    return matches[0]


def reply_tids(path, broker, peer):
    if not path.exists():
        return []
    return [int(tid) for pid, tid, blocked in re.findall(
        r"^\s*(\d+)[ \t]+(\d+)[ \t]+REPLY[ \t]+(\d+)[ \t]*$", path.read_text(), re.M)
        if int(pid) == broker and int(tid) > 1 and int(blocked) == peer]


def observe(stem, broker, expected_peer, outstanding):
    names = ("broker", "peer", "before_tid", "after_tid", "before_observation", "before_write",
             "after_write", "after_observation")
    text = stem.with_suffix(".ordering.txt").read_text().strip()
    match = re.fullmatch(" ".join(name + r"=(\d+)" for name in names), text)
    check(match, "malformed command-write observation")
    b, peer, before_tid, after_tid, *watermarks = map(int, match.groups())
    check(b == broker and watermarks == sorted(watermarks), "inconsistent observation chronology")
    if expected_peer:
        check(peer == expected_peer, "observation targets a different candidate")
    before = reply_tids(Path(f"{stem}-before.threads"), broker, peer)
    after = reply_tids(Path(f"{stem}-after.threads"), broker, peer)
    check(before == ([before_tid] if before_tid else []) and after == ([after_tid] if after_tid else []),
          "REPLY claims disagree with pidin snapshots")
    if not peer or not before_tid or before_tid != after_tid:
        return peer, False
    if outstanding is not None:
        return peer, watermarks == [outstanding - 1] * 4
    return peer, True


def connections(path):
    entries = re.findall(r"^[ \t]*(\d+s?)[ \t]+(\d+)[ \t]", path.read_text(), re.M)
    check({"0", "1", "2"} <= {fd for fd, _ in entries}, "incomplete broker descriptors")
    return sorted((fd, int(pid)) for fd, pid in entries)


def processes(path):
    rows = re.findall(r"^[ \t]*(\d+)[ \t]+(\S+)", path.read_text(), re.M)
    result = {int(pid) for pid, command in rows if Path(command).name != "pidin"}
    check(result, "empty process snapshot")
    return result


def retirement(stem, broker, baseline, first, second):
    initial = connections(Path(f"{stem}-baseline.fds"))
    cancelled = connections(Path(f"{stem}-cancelled.fds"))
    check(any(fd.endswith("s") and pid == baseline for fd, pid in initial), "missing baseline side channel")
    check(cancelled == initial, "cancelled generation left extra broker descriptors or connections")
    running = processes(Path(f"{stem}-cancelled.processes"))
    check({broker, baseline} <= running and first not in running,
          "cancelled worker survived or cleanup snapshot missed the live broker")
    finished = processes(Path(f"{stem}-finished.processes"))
    check(not finished & {broker, baseline, first, second}, "owned process survived broker exit")
    check((running - {broker, baseline}) & finished, "process snapshots lack common system processes")


def verify(root, case):
    stem = root / case
    rows, ticks = load(stem.with_suffix(".jsonl"))
    meta, summary = rows[0], rows[-1]
    broker = int(stem.with_suffix(".broker-pid").read_text())
    baseline = meta["baseline_pid"]
    check((meta["schema"], meta["period_ms"], meta["deadline_ms"], meta["ipc_budget_ms"],
           meta["qualification_samples"], meta["probation_samples"]) == (2, 500, 450, 337.5, 1, 10),
          "unexpected native timing or deployment configuration")
    check(summary["phase"] == "committed" and summary["samples"] == summary["outputs"] == 44
          and stem.with_suffix(".exit").read_text().strip() == "0", "run did not complete with full output")

    stage = one(rows, "stage_requested", 1)
    request = one(rows, "cancel_requested", 1)
    cancel = one(ticks, "candidate_cancelled", 1)
    retry = one(rows, "stage_requested", 2)
    ready = one(rows, "replay_complete", 2)
    promote = one(ticks, "candidate_promoted", 2)
    commit = one(ticks, "candidate_committed", 2)
    sequence = cancel["sequence"]
    prior = ticks[sequence - 2]
    check(stage["after_sequence"] < request["after_sequence"] == sequence - 1
          and rows.index(prior) < rows.index(request) < rows.index(cancel) < rows.index(retry),
          "cancel wasn't processed between consecutive samples")
    check(request["reason"] == cancel["reason"] == "operator_cancelled" and cancel["source"] == "baseline",
          "cancel did not retain baseline authority")
    check(retry["after_sequence"] >= sequence + 2 and all(t["generation"] == 1 and t["candidate_pid"] == -1 and t["source"] == "baseline"
              for t in ticks[sequence:retry["after_sequence"]]), "cancelled worker was reused")
    second = ready["candidate_pid"]
    check(ready["replayed"] >= 16 and ready["through_sequence"] == ready["after_sequence"]
          and promote["sequence"] == ready["after_sequence"] + meta["qualification_samples"] + 1
          and commit["sequence"] == promote["sequence"] + meta["probation_samples"] - 1
          and all(t["candidate_pid"] == second and t["source"] == "candidate"
                  for t in ticks[promote["sequence"] - 1:]), "retry failed fresh replay, qualification or authority")
    check(not any(r.get("event") in {"command_rejected", "preparation_failed"} for r in rows),
          "unexpected command or preparation failure")

    if case == "live":
        first_ready = one(rows, "replay_complete", 1)
        first_promotion = one(ticks, "candidate_promoted", 1)
        outstanding = prior["sequence"]
        first, observed = observe(stem, broker, first_ready["candidate_pid"], outstanding)
        check(first_promotion["sequence"] <= outstanding and prior["source"] == "candidate"
              and prior["phase"] == "probation" and prior["candidate_check"] == "ok"
              and prior["candidate_pid"] == cancel["candidate_pid"] == first
              and cancel["phase"] == "rolled_back", "live cancellation missed the probation worker")
    else:
        first, observed = observe(stem, broker, None, None)
        check(prior["phase"] == "warming" and cancel["phase"] == "rejected"
              and all(t["candidate_pid"] == -1 and t["source"] == "baseline"
                      for t in ticks if t["generation"] == 1)
              and not any(r.get("event") == "replay_complete" and r["generation"] == 1 for r in rows),
              "replay worker graduated or gained authority before cancellation")
        if first:
            workers = Path(f"{stem}-worker.processes").read_text()
            check(re.search(rf"^\s*{first}[ \t]+(?:\S*/)?shadow-candidate[ \t]+delay[ \t]+0[ \t]*$",
                            workers, re.M), "REPLY peer wasn't the staged delay worker")
    if first:
        check(min(broker, baseline, first, second) > 1 and len({broker, baseline, first, second}) == 4,
              "process identity was invalid or reused")
        check(all(t["candidate_pid"] != first for t in ticks[sequence:]),
              "cancelled candidate PID appeared in a subsequent sample")
        retirement(stem, broker, baseline, first, second)
    return observed, sequence, commit["sequence"]


def main():
    if len(sys.argv) != 2:
        print("usage: verify_qnx_cancel_ordering.py FRESH_TRACE_DIR", file=sys.stderr)
        return 2
    verdict = 0
    for case in ("live", "replay"):
        try:
            observed, cancelled, committed = verify(Path(sys.argv[1]), case)
        except (OSError, ValueError, KeyError, IndexError) as error:
            print(f"FAIL {case}: {error}", file=sys.stderr)
            verdict = 1
            continue
        if not observed and verdict == 0:
            verdict = 77
        scope = "same live exchange" if case == "live" else "warming worker; individual replay request unspecified"
        print(f"{'PASS' if observed else 'INCONCLUSIVE'} {case}: REPLY evidence ({scope}), "
              f"cancelled_sample={cancelled} retry_committed_sample={committed}")
    return verdict


if __name__ == "__main__":
    sys.exit(main())
