#!/usr/bin/env python3
"""Verify fresh QNX operator cancellation traces and process snapshots."""

from pathlib import Path
import json
import re
import sys

from verify_lifecycle import read_manifest
from verify_qnx_logs import check, load, validate_rows


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
    text = Path(f"{stem}-ordering.txt").read_text().strip()
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

    if case in {"live", "live-near"}:
        first_ready = one(rows, "replay_complete", 1)
        first_promotion = one(ticks, "candidate_promoted", 1)
        outstanding = prior["sequence"]
        first = first_ready["candidate_pid"]
        check(first > 1 and first_promotion["sequence"] <= outstanding and prior["source"] == "candidate"
              and prior["phase"] == "probation" and prior["candidate_check"] == "ok"
              and prior["candidate_pid"] == cancel["candidate_pid"] == first
              and cancel["phase"] == "rolled_back", "live cancellation missed the probation worker")
    elif case == "handoff-gated":
        first, outstanding = cancel["candidate_pid"], prior["sequence"]
        check(prior["phase"] == "warming" and cancel["phase"] == "rejected"
              and all(t["source"] == "baseline" and t["candidate_pid"] == -1
                      for t in ticks[:sequence - 1] if t["generation"] == 1),
              "handoff cancellation gained authority or missed warming")
    else:
        first, outstanding = 0, None
        check(prior["phase"] == "warming" and cancel["phase"] == "rejected"
              and all(t["candidate_pid"] == -1 and t["source"] == "baseline"
                      for t in ticks if t["generation"] == 1)
              and not any(r.get("event") == "replay_complete" and r["generation"] == 1 for r in rows),
              "replay worker graduated or gained authority before cancellation")
    check(min(broker, baseline, second) > 1 and len({broker, baseline, second}) == 3,
          "process identity was invalid or reused")
    return broker, baseline, first, second, outstanding, sequence, commit["sequence"], ticks, stage["after_sequence"]


def verify_observation(stem, case, broker, first, outstanding):
    peer, bracket = observe(stem, broker, first, outstanding)
    if case == "replay" and peer:
        workers = Path(f"{stem}-worker.processes").read_text()
        check(re.search(rf"^\s*{peer}[ \t]+(?:\S*/)?shadow-candidate[ \t]+delay[ \t]+0[ \t]*$",
                        workers, re.M), "REPLY peer wasn't the staged delay worker")
    return peer, bracket


def phase_timings(stem):
    timings = {}
    for line in stem.with_suffix(".stderr").read_text().splitlines():
        if line.startswith('{"type":"phase_timing",'):
            row = json.loads(line)
            check(row["sequence"] not in timings, "duplicate native phase timing")
            timings[row["sequence"]] = row
    return timings


def verify_exact_replay(stem, broker, stage_after):
    code = int(stem.with_suffix(".exact.exit").read_text())
    path = stem.with_suffix(".exact")
    check(code in {0, 77}, f"native stock replay observer exited {code}")
    if code == 77:
        check(not path.exists(), "missed pre-write observation contradicts exact replay evidence")
        return 0, False
    lines = path.read_text().splitlines()
    check(len(lines) in {4, 5}, "incomplete exact replay evidence")
    stage_times = None
    if len(lines) == 5:
        fields = re.fullmatch(r"stage before_ns=(\d+) after_ns=(\d+)", lines.pop(1))
        check(fields, "malformed stock replay stage write timestamps")
        stage_times = tuple(map(int, fields.groups()))
    owner = re.fullmatch(r"candidate parent=(\d+) pid=(\d+) start_ns=(\d+)", lines[0])
    check(owner, "malformed candidate ownership evidence")
    parent, peer, start = map(int, owner.groups())
    check(parent == broker and peer > 1 and peer != broker and start > 0,
          "stock replay candidate did not belong to the broker")
    workers = Path(f"{stem}-worker.processes").read_text()
    check(re.search(rf"^\s*{peer}[ \t]+(?:\S*/)?shadow-candidate[ \t]+delay[ \t]+0[ \t]*$",
                    workers, re.M), "stock replay peer was not the staged delay worker")

    def snapshot(line, label):
        matched = re.fullmatch(label + r" pid=(\d+) tid=(\d+) thread_start_ns=(\d+) peer=(\d+) "
                               r"chid=(\d+) coid=(\d+) start_ns=(\d+) end_ns=(\d+) blocked_ns=(\d+)", line)
        check(matched, f"malformed {label} snapshot")
        pid, tid, thread_start, receiver, chid, coid, low, high, age = map(int, matched.groups())
        check(pid == broker and tid > 1 and thread_start > 0 and receiver == peer
              and 0 < low <= high, f"invalid {label} identity or time bracket")
        return (tid, thread_start, chid, coid), low, high, age

    pre, pre_start, pre_end, pre_age = snapshot(lines[1], "pre_reply")
    write = re.fullmatch(r"write before_ns=(\d+) after_ns=(\d+)", lines[2])
    check(write, "malformed stock replay write timestamps")
    before, after = map(int, write.groups())
    check(pre_end <= before <= after, "invalid stock replay pre-write chronology")
    if stage_times:
        stage_before, stage_written = stage_times
        check(0 < stage_before <= stage_written <= pre_start, "invalid stock replay stage chronology")
        manifest = read_manifest(stem.with_suffix(".manifest"))
        check(Path(manifest["executable"]).name == "shadow-candidate"
              and manifest["mode"] == "delay" and manifest["fault_after"] == "0"
              and manifest["replay_samples"] == "16", "stock replay stage used an unexpected fixture")
        timing = phase_timings(stem)
        check(timing[stage_after]["acquired_ns"] <= stage_before <= stage_written
              <= timing[stage_after + 1]["acquired_ns"], "stage write did not precede its recorded acceptance")
    if lines[3] == "post_unobserved":
        return peer, False
    post, post_start, post_end, post_age = snapshot(lines[3], "post_reply")
    check(after <= post_start, "invalid stock replay post-write chronology")
    if pre != post:
        return peer, False
    if stage_times:
        # The candidate cannot receive a request before its deployment is sent.
        # The stock worker delays 250 ms before replying to every request.
        # Both observations therefore identify its first request in this window.
        return peer, post_end - stage_before < 248_000_000
    rounding_ns = 2_000_000
    # The post snapshot may have been sampled as late as its end and the pre
    # snapshot as early as its start. Its entire blocked interval must cover both.
    age_covers_pre = post_age - rounding_ns > post_end - pre_start
    # A reset between snapshots can only be ruled out if both elapsed ages
    # admit an overlapping interval for the native block's start.
    earliest = max(pre_start - pre_age, post_start - post_age) - rounding_ns
    latest = min(pre_end - pre_age, post_end - post_age) + rounding_ns
    plausible_ages = 5_000_000 <= pre_age <= 339_500_000 and post_age <= 339_500_000
    return peer, age_covers_pre and plausible_ages and earliest <= latest


def verify_gate(stem, broker, stage_after, case="replay-gated", outstanding=None, first=0):
    path = stem.with_suffix(".witness")
    lines = path.read_text().splitlines() if path.exists() else []
    if not lines:
        return 0, False
    receipt = re.fullmatch(r"received pid=(\d+) sender_pid=(\d+) sequence=(\d+) acquired_ns=(\d+) "
                           r"rcvid=(\d+) time_ns=(\d+)", lines[0])
    check(receipt, "malformed gated replay receipt")
    peer, sender, sequence, acquired, rcvid, received_ns = map(int, receipt.groups())
    expected = {"replay-gated": stage_after - 15, "handoff-gated": stage_after + 1,
                "live-near": 22}[case]
    check(peer > 1 and sender == broker and sequence == expected and rcvid > 0
          and (first <= 0 or first == peer),
          "gated witness does not identify the broker and expected request")

    token = stem.with_suffix(".release")
    if token.exists():
        check(token.read_text() == lines[0] + "\n", "gate token does not match the immutable receipt")
    if len(lines) < 3:
        return peer, False
    check(len(lines) == 3 and token.is_file(), "unexpected gated replay witness or missing release token")
    release = re.fullmatch(r"released sequence=(\d+) rcvid=(\d+) time_ns=(\d+)", lines[1])
    reply = re.fullmatch(r"replied sequence=(\d+) rcvid=(\d+) time_ns=(\d+) result=(-?\d+)", lines[2])
    check(release and reply, "malformed gated replay release or reply")
    released_sequence, released_rcvid, released_ns = map(int, release.groups())
    replied_sequence, replied_rcvid, replied_ns, result = map(int, reply.groups())
    check(sequence == released_sequence == replied_sequence and rcvid == released_rcvid == replied_rcvid,
          "gated witness changed request identity")
    check(0 < acquired < received_ns <= released_ns <= replied_ns and result == 0,
          "gated reply failed or has inconsistent timestamps")
    if case == "replay-gated":
        check(released_ns - received_ns < 300_000_000 and replied_ns - received_ns < 337_500_000,
              "gated replay reply exceeded observation budget")
        return peer, True

    write = re.fullmatch(r"before_ns=(\d+) after_ns=(\d+)\n", stem.with_suffix(".write").read_text())
    check(write, "malformed FIFO write timestamps")
    before, after = map(int, write.groups())
    check(received_ns <= before <= after <= released_ns, "FIFO write was outside the held reply")
    timings = phase_timings(stem)
    current = timings[sequence]
    check(current["acquired_ns"] == acquired and current["ipc_deadline_ns"] == acquired + 337_500_000,
          "gate witness disagrees with native acquisition or IPC budget")
    if case == "live-near":
        ipc = current["candidate"]
        check(ipc["ipc_start_ns"] <= received_ns <= released_ns <= ipc["ipc_end_ns"]
              <= current["ipc_deadline_ns"], "witness did not match a successful native live exchange")
        return peer, (outstanding == sequence and
                      current["ipc_deadline_ns"] - 40_000_000 <= before <= after < current["ipc_deadline_ns"])

    check(released_ns - received_ns < 300_000_000 and replied_ns - received_ns < 337_500_000,
          "catch-up reply exceeded observation budget")
    rows, ticks = load(stem.with_suffix(".jsonl"))
    acceptance = one(rows, "cancel_requested", 1)
    ready = [row for row in rows if row.get("event") == "replay_complete" and row["generation"] == 1]
    # A successful command during replay may be accepted before replay catches up.
    if not ready:
        check(first == -1, "unobserved replay transfer acquired a live candidate")
        return peer, False
    transfer = one(rows, "replay_complete", 1)
    check(transfer["candidate_pid"] == first == peer and transfer["through_sequence"] == transfer["after_sequence"]
          and transfer["replayed"] >= 17 and rows.index(transfer) < rows.index(acceptance),
          "replay transfer was inconsistent with acceptance or reused a different worker")
    check(0 < sequence <= len(ticks) and ticks[sequence - 1]["sequence"] == sequence
          and rows.index(ticks[sequence - 1]) < rows.index(transfer),
          "replay transfer did not follow the witnessed sample")
    return peer, (transfer["after_sequence"] == acceptance["after_sequence"] == sequence
                  and replied_ns < timings[sequence + 1]["acquired_ns"])


def verify_cleanup(stem, broker, baseline, first, second, ticks, sequence):
    if first:
        check(first > 1 and first not in {broker, baseline, second},
              "cancelled worker process identity was invalid or reused")
        check(all(t["candidate_pid"] != first for t in ticks[sequence:]),
              "cancelled candidate PID appeared after the cancellation decision")
    retirement(stem, broker, baseline, first, second)



def verify_incomplete_capture(stem):
    marker = stem.with_suffix(".capture-inconclusive").read_text().strip()
    matched = re.fullmatch(r"reason=no-receipt term_sent=([01])", marker)
    check(matched, "malformed capture-expiration marker")
    term_sent = matched.group(1) == "1"
    exit_path = stem.with_suffix(".exit")
    code = int(exit_path.read_text()) if exit_path.exists() else None
    check(code in {None, 0} or (code == 130 and term_sent), f"unexpected broker exit {code}")

    path = stem.with_suffix(".jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    check(rows and rows[0]["type"] == "meta" and rows[0]["backend"] == "qnx-native-ipc",
          "capture lacks native broker metadata")
    check(not any(r.get("phase") == "failed" or r.get("publication_miss") or
                  r.get("event") in {"command_rejected", "preparation_failed", "candidate_rejected",
                                     "candidate_rolled_back"} for r in rows),
          "broker failure was recorded before capture stopped")
    check(not any(r.get("event") in {"cancel_requested", "candidate_cancelled", "candidate_committed"}
                  for r in rows), "no-receipt marker contradicts the capture trace")
    if rows[-1]["type"] == "summary":
        validate_rows(path, rows, allow_interrupted=True)
        check((code != 130 or rows[-1]["interrupted"]) and (code != 0 or not rows[-1]["interrupted"]),
              "broker exit disagrees with the capture summary")

    finished_path = Path(f"{stem}-finished.processes")
    known_retired = False
    if finished_path.exists():
        broker = int(stem.with_suffix(".broker-pid").read_text())
        known = {broker, rows[0]["baseline_pid"]}
        known.update(r["candidate_pid"] for r in rows if r.get("candidate_pid", -1) > 1)
        check(min(known) > 1, "invalid owned process identity")
        check(not processes(finished_path) & known, "owned process survived capture termination")
        known_retired = True
    return code, known_retired



def main():
    if not (len(sys.argv) == 2 or len(sys.argv) == 3 and sys.argv[2] in {"--gated", "--boundary", "--replay-exact"}):
        print("usage: verify_qnx_cancel_ordering.py FRESH_TRACE_DIR [--gated|--boundary|--replay-exact]", file=sys.stderr)
        return 2
    verdict = 0
    malformed = (OSError, ValueError, KeyError, IndexError, TypeError)
    mode = sys.argv[2] if len(sys.argv) == 3 else "stock"
    cases = {"stock": ("live", "replay"), "--gated": ("replay-gated",),
             "--boundary": ("live-near", "handoff-gated"), "--replay-exact": ("replay",)}[mode]
    for case in cases:
        stem = Path(sys.argv[1]) / case
        incomplete = stem.with_suffix(".capture-inconclusive").exists()
        if mode == "--boundary":
            try:
                writer_code = int(Path(f"{stem}.write.exit").read_text())
                check(writer_code == (77 if incomplete else 0), f"FIFO writer exited {writer_code}")
            except malformed as error:
                print(f"FAIL {case} capture: {error}", file=sys.stderr)
                verdict = 1
                continue
        if case not in {"live", "replay"} and incomplete:
            try:
                code, known_retired = verify_incomplete_capture(stem)
            except malformed as error:
                print(f"FAIL {case} capture: {error}", file=sys.stderr)
                verdict = 1
            else:
                end = f"broker_exit={code}" if code is not None else "broker exit unproven"
                print(f"INCONCLUSIVE {case} capture: no receipt witness; {end}")
                if known_retired:
                    print(f"PASS {case} final process snapshot: known owned processes absent")
                print(f"INCONCLUSIVE {case} cleanup: cancelled worker and post-cancel resources unproven")
                if verdict == 0:
                    verdict = 77
            continue
        try:
            broker, baseline, first, second, outstanding, cancelled, committed, ticks, stage_after = verify_behavior(stem, case)
        except malformed as error:
            print(f"FAIL {case} behavior: {error}", file=sys.stderr)
            verdict = 1
            continue
        print(f"PASS {case} behavior: cancelled_sample={cancelled} retry_committed_sample={committed}")
        bracket = False
        try:
            if case not in {"live", "replay"}:
                peer, bracket = verify_gate(stem, broker, stage_after, case, outstanding, first)
                first = peer or max(0, first)
                label = "gated replay" if case == "replay-gated" else case
                description = {
                    "replay-gated": "first request across the write",
                    "live-near": "write in last 40 ms of live IPC budget; subsequent boundary acceptance",
                    "handoff-gated": "catch-up exchange across write; transfer before same-boundary acceptance",
                }[case]
                print(f"{'PASS' if bracket else 'INCONCLUSIVE'} {label} ordering: {description}")
            elif mode == "--replay-exact":
                first, bracket = verify_exact_replay(stem, broker, stage_after)
                print(f"{'PASS' if bracket else 'INCONCLUSIVE'} replay ordering: same stock replay request across FIFO write")
            else:
                peer, bracket = verify_observation(stem, case, broker, first, outstanding)
                first = first or peer
                if case == "replay":
                    print(f"{'PASS' if bracket else 'INCONCLUSIVE'} replay observation: matching REPLY sender and peer")
                    print("INCONCLUSIVE replay ordering: no witness identifies the same replay request across the write")
                else:
                    print(f"{'PASS' if bracket else 'INCONCLUSIVE'} live ordering: same exchange across the write")
        except malformed as error:
            label = "gated replay" if case == "replay-gated" else case
            print(f"FAIL {label} ordering: {error}", file=sys.stderr)
            verdict = 1
        try:
            verify_cleanup(stem, broker, baseline, max(0, first), second, ticks, cancelled)
        except malformed as error:
            print(f"FAIL {case} cleanup: {error}", file=sys.stderr)
            verdict = 1
        else:
            print(f"PASS {case} cleanup: descriptors restored and known children retired")
            if not first:
                print(f"INCONCLUSIVE {case} cleanup: cancelled worker PID was unobserved")
        if (not bracket or not first or mode == "stock" and case == "replay") and verdict == 0:
            verdict = 77
    return verdict


if __name__ == "__main__":
    sys.exit(main())
