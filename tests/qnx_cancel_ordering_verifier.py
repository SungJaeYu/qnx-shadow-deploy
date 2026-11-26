"""Exercise observational verdicts with newly generated snapshot fragments."""

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from verify_qnx_cancel_ordering import observe, retirement
from verify_qnx_logs import TraceValidationError


class CancelOrderingEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.stem = Path(directory.name) / "live"
        self.write("-before.threads", "43 2 REPLY 47\n")
        self.write("-after.threads", "43 2 REPLY 47\n")
        self.ordering()
        fds = " 0 1 input\n 1 1 output\n 2 1 errors\n 7s 46 worker\n"
        self.write("-baseline.fds", fds)
        self.write("-cancelled.fds", fds)
        self.write("-cancelled.processes", "1 procnto\n43 broker\n46 baseline\n")
        self.write("-finished.processes", "1 procnto\n")

    def write(self, extension, text):
        Path(f"{self.stem}{extension}").write_text(text)

    def ordering(self, before_tid=2, after_tid=2, watermark=21, final=21):
        self.write("-ordering.txt", f"broker=43 peer=47 before_tid={before_tid} after_tid={after_tid} "
                   f"before_observation={watermark} before_write={watermark} after_write={watermark} "
                   f"after_observation={final}\n")

    def test_same_live_exchange(self):
        self.assertEqual(observe(self.stem, 43, 47, 22), (47, True))

    def test_single_observation_is_inconclusive(self):
        self.write("-after.threads", "43 2 READY 0\n")
        self.ordering(after_tid=0)
        self.assertEqual(observe(self.stem, 43, 47, 22), (47, False))

    def test_sample_published_between_observations_is_inconclusive(self):
        self.ordering(final=22)
        self.assertEqual(observe(self.stem, 43, 47, 22), (47, False))

    def test_different_sender_is_inconclusive(self):
        self.write("-after.threads", "43 4 REPLY 47\n")
        self.ordering(after_tid=4)
        self.assertEqual(observe(self.stem, 43, 47, 22), (47, False))

    def test_pidin_must_support_written_claim(self):
        self.write("-after.threads", "43 2 REPLY 46\n")
        with self.assertRaisesRegex(TraceValidationError, "disagree"):
            observe(self.stem, 43, 47, 22)

    def test_resources_return_to_baseline(self):
        retirement(self.stem, 43, 46, 47, 48)

    def test_leaked_cancelled_connection_fails(self):
        initial = Path(f"{self.stem}-baseline.fds").read_text()
        self.write("-cancelled.fds", initial + " 8s 47 candidate\n")
        with self.assertRaisesRegex(TraceValidationError, "extra broker descriptors"):
            retirement(self.stem, 43, 46, 47, 48)

    def test_cancelled_worker_surviving_fails(self):
        self.write("-cancelled.processes", "1 procnto\n43 broker\n46 baseline\n47 candidate\n")
        with self.assertRaisesRegex(TraceValidationError, "cancelled worker survived"):
            retirement(self.stem, 43, 46, 47, 48)


class CancelOrderingTraceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def fixture(self, case, *, peer_known=True, mutation=None):
        live = case in {"live", "live-near"}
        staged, cancel = 16, 23 if live else 18
        retry, ready = cancel + 3, cancel + 4
        promote, commit = ready + 2, ready + 11

        def event(name, generation, after, **fields):
            return dict(type="deployment", generation=generation, event=name, after_sequence=after, **fields)

        rows = [dict(type="meta", schema=2, backend="qnx-native-ipc", period_ms=500, deadline_ms=450,
                     ipc_budget_ms=337.5, qualification_samples=1, probation_samples=10, baseline_pid=46)]
        controls = {
            staged: event("stage_requested", 1, staged),
            cancel - 1: event("cancel_requested", 1, cancel - 1, reason="operator_cancelled"),
            retry: event("stage_requested", 2, retry),
            ready: event("replay_complete", 2, ready, replayed=17, through_sequence=ready, candidate_pid=48),
        }
        if case == "handoff-gated" and mutation != "missed-transfer":
            controls[cancel - 1] = [
                event("replay_complete", 1, cancel - 1, candidate_pid=47, replayed=17,
                      through_sequence=cancel - 1), controls[cancel - 1]
            ]
            if mutation == "acceptance-first":
                controls[cancel - 1].reverse()
        if live:
            controls[17] = event("replay_complete", 1, 17, candidate_pid=47)
        for sequence in range(1, 45):
            generation = 0 if sequence <= staged else (1 if sequence <= retry else 2)
            phase, source, name, peer = "baseline", "baseline", "", -1
            if generation == 1:
                phase = "warming"
                if live and 18 <= sequence < cancel:
                    phase, peer = "shadowing", 47
                    if sequence >= 19:
                        phase, source = "probation", "candidate"
                        if sequence == 19:
                            name = "candidate_promoted"
                if sequence >= cancel:
                    phase = "rolled_back" if live else "rejected"
                    if sequence == cancel:
                        name = "candidate_cancelled"
                        if live or (case == "handoff-gated" and mutation != "missed-transfer"):
                            peer = 47
            if generation == 2:
                if sequence <= ready:
                    phase = "warming"
                else:
                    peer = 48
                    phase = "shadowing" if sequence < promote else ("probation" if sequence < commit else "committed")
                if sequence >= promote:
                    source = "candidate"
                if sequence in (promote, commit):
                    name = "candidate_promoted" if sequence == promote else "candidate_committed"
            current = dict(type="sample", sequence=sequence, time_s=sequence * 0.5, generation=generation,
                           phase=phase, source=source, event=name, output=1.0, publication_miss=False,
                           decision_latency_ms=1.0, baseline_check="ok", baseline={"position": 1.0},
                           candidate_check="ok" if peer > 0 else "not_running",
                           candidate={"position": 1.0} if peer > 0 else None,
                           baseline_pid=46, candidate_pid=peer)
            if sequence == cancel:
                current["reason"] = "operator_cancelled"
            if sequence == ready + 1 and mutation == "qualifying-phase":
                current["phase"] = "warming"
            if sequence == ready + 1 and mutation == "qualifying-owner":
                current["candidate_pid"] = 47
            control = controls.get(sequence)
            if control and sequence == ready and mutation == "event-order":
                rows.append(control)
                control = None
            if case == "handoff-gated" and sequence == cancel - 1 and mutation == "transfer-before-sample":
                rows.append(control.pop(0))
            rows.append(current)
            if control:
                rows.extend(control if isinstance(control, list) else [control])
        rows.append(dict(type="summary", phase="committed", samples=44, outputs=44, missing_outputs=0,
                         publication_misses=0, interrupted=False))

        def write(suffix, data):
            (self.root / f"{case}{suffix}").write_text(data)

        write(".jsonl", "".join(json.dumps(row) + "\n" for row in rows))
        write(".broker-pid", "43\n")
        write(".exit", "0\n")
        pid, tid = (47, 2) if peer_known else (0, 0)
        watermark = cancel - 2 if live else cancel - 1
        write("-ordering.txt", f"broker=43 peer={pid} before_tid={tid} after_tid={tid} "
              f"before_observation={watermark} before_write={watermark} after_write={watermark} "
              f"after_observation={watermark}\n")
        if peer_known:
            write("-before.threads", "43 2 REPLY 47\n")
            write("-after.threads", "43 2 REPLY 47\n")
            if not live:
                write("-worker.processes", "47 /bin/shadow-candidate delay 0\n")
        fds = " 0 1 input\n 1 1 output\n 2 1 errors\n 7s 46 worker\n"
        write("-baseline.fds", fds)
        write("-cancelled.fds", fds + (" 8s 47 leak\n" if mutation == "leak" else ""))
        write("-cancelled.processes", "1 procnto\n43 broker\n46 baseline\n")
        write("-finished.processes", "1 procnto\n")

    def gated_witness(self, *, reply_rcvid=123, reply_ns=1_030_000_000, result=0, complete=True,
                      sender=43, token_matches=True, receipt_present=True):
        self.fixture("replay-gated")
        stem = self.root / "replay-gated"
        # Gated evidence needs no stock pidin thread snapshots or sample watermarks.
        for suffix in ("-ordering.txt", "-before.threads", "-after.threads"):
            Path(f"{stem}{suffix}").unlink()
        if not receipt_present:
            return
        receipt = f"received pid=47 sender_pid={sender} sequence=1 acquired_ns=50 rcvid=123 time_ns=1000000000\n"
        release = "released sequence=1 rcvid=123 time_ns=1020000000\n"
        reply = f"replied sequence=1 rcvid={reply_rcvid} time_ns={reply_ns} result={result}\n"
        stem.with_suffix(".release").write_text(receipt if token_matches else "incorrect token\n")
        stem.with_suffix(".witness").write_text(receipt + release + (reply if complete else ""))

    def boundary_witness(self, case, *, mutation=None, offset=300_000_000):
        self.fixture(case, mutation=mutation)
        stem = self.root / case
        live = case == "live-near"
        sequence = 22 if live else 17
        acquired = 1_000_000_000
        received = acquired + 1_000_000
        before = acquired + (offset if live else 2_000_000)
        after = before + 100
        released = after + 100
        replied = released + 100
        receipt = (f"received pid=47 sender_pid=43 sequence={sequence} acquired_ns={acquired} "
                   f"rcvid=123 time_ns={received}\n")
        stem.with_suffix(".release").write_text(receipt)
        stem.with_suffix(".witness").write_text(
            receipt + f"released sequence={sequence} rcvid=123 time_ns={released}\n"
            f"replied sequence={sequence} rcvid=123 time_ns={replied} result=0\n")
        stem.with_suffix(".write").write_text(f"before_ns={before} after_ns={after}\n")
        (self.root / f"{case}.write.exit").write_text("0\n")
        current = dict(type="phase_timing", sequence=sequence,
                       acquired_ns=acquired, ipc_deadline_ns=acquired + 337_500_000,
                       candidate=dict(ipc_start_ns=acquired, ipc_end_ns=replied))
        next_tick = dict(type="phase_timing", sequence=sequence + 1, acquired_ns=acquired + 500_000_000)
        stem.with_suffix(".stderr").write_text("".join(
            json.dumps(row, separators=(",", ":")) + "\n" for row in (current, next_tick)))

    def test_boundary_cases_pass_with_fifo_brackets_and_native_timing(self):
        self.boundary_witness("live-near")
        self.boundary_witness("handoff-gated")
        result = self.run_verifier("--boundary")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS live-near ordering", result.stdout)
        self.assertIn("PASS handoff-gated ordering", result.stdout)
        self.assertIn("PASS handoff-gated cleanup", result.stdout)

    def test_early_fifo_write_limits_live_ordering_verdict_only(self):
        self.boundary_witness("live-near", offset=200_000_000)
        self.boundary_witness("handoff-gated")
        result = self.run_verifier("--boundary")
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertIn("INCONCLUSIVE live-near ordering", result.stdout)
        self.assertIn("PASS live-near cleanup", result.stdout)

    def test_cancellation_while_still_warming_leaves_handoff_inconclusive(self):
        self.boundary_witness("live-near")
        self.boundary_witness("handoff-gated", mutation="missed-transfer")
        result = self.run_verifier("--boundary")
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertIn("INCONCLUSIVE handoff-gated ordering", result.stdout)
        self.assertIn("PASS handoff-gated cleanup", result.stdout)

    def test_handoff_acceptance_must_follow_transfer(self):
        self.boundary_witness("live-near")
        self.boundary_witness("handoff-gated", mutation="acceptance-first")
        result = self.run_verifier("--boundary")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FAIL handoff-gated ordering", result.stderr)

    def test_handoff_transfer_must_follow_witnessed_sample(self):
        self.boundary_witness("live-near")
        self.boundary_witness("handoff-gated", mutation="transfer-before-sample")
        result = self.run_verifier("--boundary")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FAIL handoff-gated ordering: replay transfer did not follow the witnessed sample",
                      result.stderr)

    def incomplete_gated_capture(self, *, code=130, preparation_failed=False, process_survives=False,
                                 snapshot=True, case="replay-gated", writer_code=None, candidate_rolled_back=False):
        self.fixture(case, peer_known=False)
        stem = self.root / case
        trace = stem.with_suffix(".jsonl")
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        sample_17 = next(i for i, row in enumerate(rows) if row.get("sequence") == 17)
        rows = rows[:sample_17 + 1]
        if preparation_failed:
            rows.append(dict(type="deployment", event="preparation_failed", generation=1, after_sequence=17))
        if candidate_rolled_back:
            rows[-1].update(event="candidate_rolled_back", reason="deadline_exceeded")
        rows.append(dict(type="summary", phase="warming", samples=17, outputs=17, missing_outputs=0,
                         publication_misses=0, interrupted=True))
        trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
        stem.with_suffix(".capture-inconclusive").write_text("reason=no-receipt term_sent=1\n")
        stem.with_suffix(".exit").write_text(f"{code}\n")
        if writer_code is not None:
            (self.root / f"{case}.write.exit").write_text(f"{writer_code}\n")
        Path(f"{stem}-cancelled.fds").unlink()
        Path(f"{stem}-cancelled.processes").unlink()
        finished = Path(f"{stem}-finished.processes")
        if not snapshot:
            finished.unlink()
        elif process_survives:
            finished.write_text("1 procnto\n46 baseline\n")

    def test_boundary_writer_error_overrides_inconclusive_marker(self):
        self.boundary_witness("live-near")
        self.incomplete_gated_capture(case="handoff-gated", writer_code=1)
        result = self.run_verifier("--boundary")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FAIL handoff-gated capture: FIFO writer exited 1", result.stderr)

    def test_boundary_no_receipt_only_inconclusive_if_no_broker_failure(self):
        self.boundary_witness("live-near")
        for rolled_back in (False, True):
            with self.subTest(rolled_back=rolled_back):
                self.incomplete_gated_capture(case="handoff-gated", writer_code=77,
                                              candidate_rolled_back=rolled_back)
                result = self.run_verifier("--boundary")
                self.assertEqual(result.returncode, 1 if rolled_back else 77, result.stdout)
                if rolled_back:
                    self.assertIn("broker failure was recorded", result.stderr)
                else:
                    self.assertIn("INCONCLUSIVE handoff-gated capture", result.stdout)

    def run_verifier(self, *args):
        verifier = Path(__file__).resolve().parents[1] / "tools" / "verify_qnx_cancel_ordering.py"
        return subprocess.run([sys.executable, str(verifier), str(self.root), *args],
                              text=True, capture_output=True, check=False)

    def test_stock_replay_ordering_is_inconclusive_with_two_matching_snapshots(self):
        self.fixture("live")
        self.fixture("replay")
        result = self.run_verifier()
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertIn("PASS live ordering", result.stdout)
        self.assertIn("PASS replay behavior", result.stdout)
        self.assertIn("PASS replay observation", result.stdout)
        self.assertIn("PASS replay cleanup", result.stdout)
        self.assertIn("INCONCLUSIVE replay ordering", result.stdout)

    def test_unknown_peer_does_not_hide_connection_leak(self):
        self.fixture("live")
        self.fixture("replay", peer_known=False, mutation="leak")
        result = self.run_verifier()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("PASS replay behavior", result.stdout)
        self.assertIn("FAIL replay cleanup", result.stderr)

    def test_unknown_peer_only_limits_child_retirement_verdict(self):
        self.fixture("live")
        self.fixture("replay", peer_known=False)
        result = self.run_verifier()
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertIn("PASS replay cleanup", result.stdout)
        self.assertIn("INCONCLUSIVE replay cleanup", result.stdout)

    def test_gated_first_request_can_pass(self):
        self.gated_witness()
        result = self.run_verifier("--gated")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS gated replay ordering", result.stdout)
        self.assertIn("PASS replay-gated cleanup", result.stdout)

    def test_gated_without_reply_is_inconclusive(self):
        self.gated_witness(complete=False)
        result = self.run_verifier("--gated")
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertIn("INCONCLUSIVE gated replay ordering", result.stdout)

    def test_gated_without_receipt_is_inconclusive(self):
        self.gated_witness(receipt_present=False)
        result = self.run_verifier("--gated")
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertIn("INCONCLUSIVE gated replay ordering", result.stdout)
        self.assertIn("INCONCLUSIVE replay-gated cleanup", result.stdout)

    def test_capture_expiration_preserves_independent_cleanup_limits(self):
        for snapshot in (False, True):
            with self.subTest(snapshot=snapshot):
                self.incomplete_gated_capture(snapshot=snapshot)
                result = self.run_verifier("--gated")
                self.assertEqual(result.returncode, 77, result.stderr)
                self.assertIn("no receipt witness; broker_exit=130", result.stdout)
                self.assertEqual("PASS replay-gated final process snapshot" in result.stdout, snapshot)
                self.assertIn("INCONCLUSIVE replay-gated cleanup", result.stdout)

    def test_capture_expiration_never_hides_known_failure(self):
        for changes, reason in ((dict(code=3), "unexpected broker exit 3"),
                                (dict(preparation_failed=True), "broker failure was recorded"),
                                (dict(process_survives=True), "owned process survived")):
            with self.subTest(changes=changes):
                self.incomplete_gated_capture(**changes)
                result = self.run_verifier("--gated")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(reason, result.stderr)

    def test_gated_requires_matching_request_success_and_budget(self):
        for changes in (dict(reply_rcvid=124), dict(reply_ns=1_340_000_000), dict(result=-1),
                        dict(sender=78), dict(token_matches=False)):
            with self.subTest(changes=changes):
                self.gated_witness(**changes)
                result = self.run_verifier("--gated")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("FAIL gated replay ordering", result.stderr)

    def test_retry_requires_sample_phase_current_candidate_and_order(self):
        self.fixture("live")
        for mutation in ("qualifying-phase", "qualifying-owner", "event-order"):
            with self.subTest(mutation=mutation):
                self.fixture("replay", mutation=mutation)
                result = self.run_verifier()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertRegex(result.stderr, r"FAIL replay behavior: retry (lacked fresh|deployment event)")


if __name__ == "__main__":
    unittest.main()
