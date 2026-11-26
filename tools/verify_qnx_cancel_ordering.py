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
    if expected_peer and peer:
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


def verify_retry(rows, ticks, retry, ready, promote, commit):
    retry_after, ready_after = retry["after_sequence"], ready["after_sequence"]
    promotion, committed = promote["sequence"], commit["sequence"]
    qualification = rows[0]["qualification_samples"]
    probation = rows[0]["probation_samples"]
    second = ready["candidate_pid"]
    check(0 < retry_after < ready_after < promotion < committed <= len(ticks)
          and rows.index(ticks[retry_after - 1]) < rows.index(retry) < rows.index(ticks[retry_after])
          and rows.index(ticks[ready_after - 1]) < rows.index(ready) < rows.index(ticks[ready_after])
          and ready["replayed"] >= 16 and ready["through_sequence"] == ready_after
          and promotion == ready_after + qualification + 1
          and committed == promotion + probation - 1, "retry deployment event order or replay bounds")
    warming = ticks[retry_after:ready_after]
    qualifying = ticks[ready_after:promotion - 1]
    authoritative = ticks[promotion - 1:]
    check(all(t["generation"] == 2 and t["phase"] == "warming" and t["source"] == "baseline"
              and t["candidate_pid"] == -1 for t in warming), "retry warming gained authority")
    check(second > 1 and all(t["generation"] == 2 and t["phase"] == "shadowing"
                             and t["source"] == "baseline" and t["candidate_pid"] == second
                             and t["candidate_check"] == "ok" and t["candidate"]["position"] is not None
                             for t in qualifying), "retry lacked fresh qualifying samples")
    check(all(t["generation"] == 2 and t["candidate_pid"] == second and t["source"] == "candidate"
              and t["phase"] == ("probation" if t["sequence"] < committed else "committed")
              for t in authoritative), "retry probation or authority was invalid")
    return second


def verify_behavior(stem, case):
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
    check(sequence > 1, "cancellation has no preceding sample")
    prior = ticks[sequence - 2]
    check(stage["after_sequence"] < request["after_sequence"] == sequence - 1
          and rows.index(stage) < rows.index(prior) < rows.index(request) < rows.index(cancel) < rows.index(retry),
          "cancel wasn't processed between consecutive samples")
    check(request["reason"] == cancel["reason"] == "operator_cancelled" and cancel["source"] == "baseline",
          "cancel did not retain baseline authority")
    check(sequence + 2 <= retry["after_sequence"] < len(ticks)
          and all(t["generation"] == 1 and t["candidate_pid"] == -1 and t["source"] == "baseline"
                  for t in ticks[sequence:retry["after_sequence"]]),
          "cancelled worker was reused after the cancellation decision")
    second = verify_retry(rows, ticks, retry, ready, promote, commit)
    check(not any(r.get("event") in {"command_rejected", "preparation_failed"} for r in rows),
          "unexpected command or preparation failure")

    if case == "live":
        first_ready = one(rows, "replay_complete", 1)
        first_promotion = one(ticks, "candidate_promoted", 1)
        outstanding = prior["sequence"]
        first = first_ready["candidate_pid"]
        check(first > 1 and first_promotion["sequence"] <= outstanding and prior["source"] == "candidate"
              and prior["phase"] == "probation" and prior["candidate_check"] == "ok"
              and prior["candidate_pid"] == cancel["candidate_pid"] == first
              and cancel["phase"] == "rolled_back", "live cancellation missed the probation worker")
    else:
        first, outstanding = 0, None
        check(prior["phase"] == "warming" and cancel["phase"] == "rejected"
              and all(t["candidate_pid"] == -1 and t["source"] == "baseline"
                      for t in ticks if t["generation"] == 1)
              and not any(r.get("event") == "replay_complete" and r["generation"] == 1 for r in rows),
              "replay worker graduated or gained authority before cancellation")
    check(min(broker, baseline, second) > 1 and len({broker, baseline, second}) == 3,
          "process identity was invalid or reused")
    return broker, baseline, first, second, outstanding, sequence, commit["sequence"], ticks


def verify_observation(stem, broker, first, outstanding):
    peer, bracket = observe(stem, broker, first, outstanding)
    if not first and peer:
        workers = Path(f"{stem}-worker.processes").read_text()
        check(re.search(rf"^\s*{peer}[ \t]+(?:\S*/)?shadow-candidate[ \t]+delay[ \t]+0[ \t]*$",
                        workers, re.M), "REPLY peer wasn't the staged delay worker")
    return peer, bracket


def verify_cleanup(stem, broker, baseline, first, second, ticks, sequence):
    if first:
        check(first > 1 and first not in {broker, baseline, second},
              "cancelled worker process identity was invalid or reused")
        check(all(t["candidate_pid"] != first for t in ticks[sequence:]),
              "cancelled candidate PID appeared after the cancellation decision")
    retirement(stem, broker, baseline, first, second)



def main():
    if len(sys.argv) != 2:
        print("usage: verify_qnx_cancel_ordering.py FRESH_TRACE_DIR", file=sys.stderr)
        return 2
    verdict = 0
    malformed = (OSError, ValueError, KeyError, IndexError, TypeError)
    for case in ("live", "replay"):
        stem = Path(sys.argv[1]) / case
        try:
            broker, baseline, first, second, outstanding, cancelled, committed, ticks = verify_behavior(stem, case)
        except malformed as error:
            print(f"FAIL {case} behavior: {error}", file=sys.stderr)
            verdict = 1
            continue
        print(f"PASS {case} behavior: cancelled_sample={cancelled} retry_committed_sample={committed}")
        bracket = False
        try:
            peer, bracket = verify_observation(stem, broker, first, outstanding)
            first = first or peer
            if case == "replay":
                print(f"{'PASS' if bracket else 'INCONCLUSIVE'} replay observation: matching REPLY sender and peer")
            else:
                print(f"{'PASS' if bracket else 'INCONCLUSIVE'} live ordering: same exchange across the write")
        except malformed as error:
            print(f"FAIL {case} observation: {error}", file=sys.stderr)
            verdict = 1
        if case == "replay":
            print("INCONCLUSIVE replay ordering: no witness identifies the same replay request across the write")
        try:
            verify_cleanup(stem, broker, baseline, first, second, ticks, cancelled)
        except malformed as error:
            print(f"FAIL {case} cleanup: {error}", file=sys.stderr)
            verdict = 1
        else:
            print(f"PASS {case} cleanup: descriptors restored and known children retired")
            if not first:
                print(f"INCONCLUSIVE {case} cleanup: cancelled worker PID was unobserved")
        if (case == "replay" or not bracket or not first) and verdict == 0:
            verdict = 77
    return verdict


if __name__ == "__main__":
    sys.exit(main())
