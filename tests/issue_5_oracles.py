"""Check issue 5 qualification oracles with deterministic, synthetic records."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from verify_cancel_retry_cycles import (
    require_input_source, require_qualification_profile, verify_final_qualification, verify_timing,
)
from verify_output_receipts import assess_receipts, read_receipts


def timing_fixture(period=20, deadline=10):
    meta = dict(period_ms=period, deadline_ms=deadline, ipc_budget_ms=deadline * .75,
                release_policy="guarded-fixed-grid", requested_inputs=2, producer_run_id="42-80000000")
    samples = [
        dict(type="sample", sequence=sequence, acquired_offset_ns=offset, decision_offset_ns=offset + 100,
             scheduled_release_offset_ns=offset, time_s=offset / 1e9, decision_latency_ms=.0001,
             publication_miss=False, nominal_slot=sequence - 1, skipped_nominal_slots=0,
             acquisition_interval_ms=None if sequence == 1 else period, release_lateness_ms=0,
             nominal_slot_lateness_ms=0, nominal_grid_lateness_ms=0, source="baseline",
             output=float(sequence), output_delivered=True)
        for sequence, offset in enumerate((0, period * 1_000_000), 1)
    ]
    summary = dict(type="summary", requested_inputs=2, acquired_inputs=2, unprocessed_inputs=0,
                   outputs=2, selected_outputs=2, missing_outputs=0, missing_selected_outputs=0,
                   initial_unoccupied_slots=0, skipped_nominal_slots=0, publication_misses=0,
                   decision_deadline_misses=0, last_nominal_slot=1, max_acquisition_interval_ms=period,
                   max_scheduling_lateness_ms=0, max_nominal_slot_lateness_ms=0, max_nominal_grid_lateness_ms=0,
                   max_selected_interval_ms=period, effective_selected_rate_hz=1000 / period,
                   external_delivery="unmeasured", telemetry_records_dropped=0, samples=2,
                   delivered_outputs=2, last_delivered_sequence=2, last_selected_sequence=2)
    return meta, samples, summary


class CycleTiming(unittest.TestCase):
    def test_strict_rejects_widened_profile_without_breaking_functional_check(self):
        meta, samples, summary = timing_fixture(200, 150)
        verify_timing(meta, summary, samples)
        with self.assertRaisesRegex(ValueError, "20 ms period"):
            require_qualification_profile(meta)

    def test_sensor_trace_input_requires_explicit_guarded_fixed_grid(self):
        meta, _, _ = timing_fixture()
        require_input_source(meta, sensor_trace=False)
        with self.assertRaisesRegex(ValueError, "sensor-trace-mm"):
            require_input_source(meta, sensor_trace=True)
        meta["input_source"] = "sensor-trace-mm"
        require_input_source(meta, sensor_trace=True)
        with self.assertRaisesRegex(ValueError, "broker-generated"):
            require_input_source(meta, sensor_trace=False)
        with self.assertRaisesRegex(ValueError, "guarded fixed grid"):
            require_input_source(dict(meta, release_policy="acquisition-relative"), sensor_trace=True)
        with self.assertRaisesRegex(ValueError, "sensor-trace-mm"):
            require_input_source(dict(meta, input_source="live-range-mm"), sensor_trace=True)

    def test_sensor_trace_marker_cannot_convert_generated_rows(self):
        meta, samples, _ = timing_fixture()
        meta["input_source"] = "sensor-trace-mm"
        for sample in samples:
            sample.update(truth=12, input=12.01)
        with self.assertRaisesRegex(ValueError, "sensor trace input"):
            require_input_source(meta, samples, sensor_trace=True)
        for sample in samples:
            sample["truth"] = None
        require_input_source(meta, samples, sensor_trace=True)
        samples[0]["acquired_ns"] = 10
        with self.assertRaisesRegex(ValueError, "sensor trace input"):
            require_input_source(meta, samples, sensor_trace=True)

    def test_reported_latency_must_match_offsets(self):
        meta, samples, summary = timing_fixture()
        samples[1]["decision_latency_ms"] = 0
        with self.assertRaisesRegex(ValueError, "decision_latency_ms"):
            verify_timing(meta, summary, samples)

    def test_release_cannot_precede_previous_decision(self):
        meta, samples, summary = timing_fixture()
        samples[1]["scheduled_release_offset_ns"] = 0
        with self.assertRaisesRegex(ValueError, "no-burst|previous decision"):
            verify_timing(meta, summary, samples)

    def test_delivery_claim_needs_receiver_evidence(self):
        meta, samples, summary = timing_fixture()
        summary["external_delivery"] = "measured"
        with self.assertRaisesRegex(ValueError, "external_delivery"):
            verify_timing(meta, summary, samples)



class FinalQualification(unittest.TestCase):
    def fixture(self):
        samples = [dict(sequence=1)]
        for sequence in range(2, 6):
            samples.append(dict(sequence=sequence, generation=21, phase="shadowing",
                                candidate_check="ok", baseline_check="ok",
                                shadow_qualification_omitted=sequence == 3))
        samples[-2]["phase"] = "probation"
        samples[-1]["phase"] = "committed"
        return samples, dict(qualification_samples=2, probation_samples=2)

    def test_valid_omitted_shadow_observation_extends_qualification(self):
        samples, meta = self.fixture()
        # Two credit-earning observations must precede promotion at sequence 5.
        samples[3]["phase"] = "shadowing"
        samples[4]["phase"] = "probation"
        samples.append(dict(samples[-1], sequence=6, phase="committed"))
        verify_final_qualification(samples, 1, samples[4], samples[5], meta)

    def test_omitted_shadow_observation_does_not_earn_credit(self):
        samples, meta = self.fixture()
        with self.assertRaisesRegex(ValueError, "qualification"):
            verify_final_qualification(samples, 1, samples[3], samples[4], meta)


class ReceiverEvidence(unittest.TestCase):
    origin = 100_000_000

    def fixture(self):
        meta, samples, summary = timing_fixture()
        rows = [dict(meta, type="meta"), *samples, summary]
        phase = [
            {"type": "phase_timing", "sequence": sample["sequence"],
             "acquired_ns": self.origin + sample["acquired_offset_ns"],
             "selected_ns": self.origin + sample["decision_offset_ns"]}
            for sample in samples
        ]
        receipts = [
            (sample["acquired_ns"] + 10_000_000,
             dict(type="authoritative_output", schema=1, producer_run_id=meta["producer_run_id"],
                  sequence=sample["sequence"], source="baseline", position_m=float(sample["sequence"]),
                  deadline_ns=sample["acquired_ns"] + 10_000_000))
            for sample in phase
        ]
        return rows, receipts, phase

    def test_receipt_exactly_at_deadline_passes_one_nanosecond_later_fails(self):
        rows, receipts, phase = self.fixture()
        self.assertEqual(assess_receipts(rows, receipts, phase)["status"], "pass")
        ns, receipt = receipts[1]
        receipts[1] = ns + 1, receipt
        result = assess_receipts(rows, receipts, phase)
        self.assertEqual(result["receiver"], "fail")
        self.assertEqual(result["late_receipts"], 1)

    def test_missing_receipts_at_leading_middle_and_tail_positions(self):
        rows, receipts, phase = self.fixture()
        # Extend only this completeness fixture to expose a middle hole.
        summary = rows[-1]
        summary.update(requested_inputs=3, acquired_inputs=3, samples=3, outputs=3,
                       selected_outputs=3, delivered_outputs=3, last_delivered_sequence=3,
                       last_selected_sequence=3)
        rows[0]["requested_inputs"] = 3
        rows.insert(3, dict(rows[2], sequence=3, output=3, acquired_offset_ns=40_000_000,
                            decision_offset_ns=40_000_100))
        receipts.append((150_000_000, dict(receipts[1][1], sequence=3, position_m=3,
                                           deadline_ns=150_000_000)))
        for dropped in range(3):
            with self.subTest(dropped=dropped):
                subset = [receipt for index, receipt in enumerate(receipts) if index != dropped]
                result = assess_receipts(rows, subset)
                self.assertEqual(result["receiver"], "fail")
                self.assertEqual(result["missing_receipts"], 1)

    def test_wrong_run_source_position_deadline_are_rejected(self):
        for key, value in (("producer_run_id", "other"), ("source", "candidate"),
                           ("position_m", 6), ("deadline_ns", 111_000_000)):
            with self.subTest(key=key):
                rows, receipts, phase = self.fixture()
                receipts[0][1][key] = value
                with self.assertRaises(ValueError):
                    assess_receipts(rows, receipts, phase)

    def test_receiver_file_rejects_duplicate_json_keys(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "receipts"
            path.write_text('10 {"sequence":1,"sequence":2}\n')
            with self.assertRaisesRegex(ValueError, "duplicate JSON field"):
                list(read_receipts(path))

    def test_duplicate_and_reordered_or_preselection_receive_timestamps_rejected(self):
        rows, receipts, phase = self.fixture()
        with self.assertRaisesRegex(ValueError, "sequence"):
            assess_receipts(rows, [receipts[0], *receipts], phase)
        with self.assertRaisesRegex(ValueError, "timestamp"):
            assess_receipts(rows, [receipts[0], (receipts[0][0] - 1, receipts[1][1])], phase)
        with self.assertRaisesRegex(ValueError, "precedes selection"):
            assess_receipts(rows, [(self.origin + 99, receipts[0][1]), receipts[1]], phase)

    def test_receipt_cannot_precede_write_start_even_after_selection(self):
        rows, receipts, phases = self.fixture()
        phases[0]["authority_write_start_ns"] = self.origin + 102
        early = (self.origin + 101, receipts[0][1])
        with self.assertRaisesRegex(ValueError, "precedes authority write"):
            assess_receipts(rows, [early, receipts[1]], phases)

    def test_phase_only_rejected_or_withheld_write_cannot_have_receipt(self):
        rows, receipts, phases = self.fixture()
        for outcome in ("rejected", None):
            with self.subTest(outcome=outcome):
                phases[0]["authority_write_outcome"] = outcome
                with self.assertRaisesRegex(ValueError, "authority write outcome"):
                    assess_receipts(rows[:1], receipts, phases)

    def test_receipt_can_precede_write_return_including_accepted_late(self):
        rows, receipts, phases = self.fixture()
        deadline, record = receipts[0]
        phases[0].update(authority_write_start_ns=deadline - 2, authority_write_end_ns=deadline,
                         authority_write_outcome="accepted")
        early = [(deadline - 1, record), receipts[1]]
        self.assertEqual(assess_receipts(rows, early, phases)["status"], "pass")
        phases[0].update(authority_write_end_ns=deadline + 1, authority_write_outcome="accepted_late")
        result = assess_receipts(rows[:1], early, phases)
        self.assertEqual(result["receiver"], "pass")
        self.assertEqual(result["status"], "fail")

    def test_accepted_outcome_must_match_known_write_return_deadline(self):
        rows, receipts, phases = self.fixture()
        deadline, _ = receipts[0]
        for outcome, end in (("accepted", deadline + 1), ("accepted_late", deadline)):
            with self.subTest(outcome=outcome):
                phases[0].update(authority_write_start_ns=deadline - 2, authority_write_end_ns=end,
                                 authority_write_outcome=outcome)
                with self.assertRaisesRegex(ValueError, "outcome conflicts with write return"):
                    assess_receipts(rows, receipts, phases)

    def test_missing_summary_preserves_phase_only_selection_deadline_miss(self):
        rows, receipts, phases = self.fixture()
        phases[1].update(selected_ns=receipts[1][0] + 1, authority_write_outcome=None)
        self.assertEqual(assess_receipts(rows[:2], receipts[:1], phases)["status"], "fail")

    def test_phase_origin_detects_uniformly_shifted_receipt_deadlines(self):
        rows, receipts, phase = self.fixture()
        shifted = [(ns, dict(record, deadline_ns=record["deadline_ns"] + 1)) for ns, record in receipts]
        with self.assertRaisesRegex(ValueError, "deadline"):
            assess_receipts(rows, shifted, phase)

    def test_unanchored_receipts_cannot_prove_absolute_timing(self):
        rows, receipts, _ = self.fixture()
        result = assess_receipts(rows, receipts)
        self.assertEqual(result["receiver"], "pass")
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["absolute_timing"], "inconclusive")

    def test_broker_summary_origin_rejects_late_receipt_masked_by_shifted_deadlines(self):
        rows, receipts, _ = self.fixture()
        rows[-1]["timing_origin_ns"] = self.origin
        self.assertEqual(assess_receipts(rows, receipts)["status"], "pass")
        shifted = [(ns + 1, dict(record, deadline_ns=record["deadline_ns"] + 1)) for ns, record in receipts]
        with self.assertRaisesRegex(ValueError, "deadline"):
            assess_receipts(rows, shifted)

    def test_live_range_absolute_acquisitions_anchor_receipts_without_phase_trace(self):
        rows, receipts, phases = self.fixture()
        rows[0]["input_source"] = "live-range-mm"
        for sample, phase in zip(rows[1:-1], phases):
            sample["acquired_ns"] = phase["acquired_ns"]
        result = assess_receipts(rows, receipts)
        self.assertEqual(result["clock_origin"], "live_range")
        self.assertEqual(result["status"], "pass")
        rows[-2]["acquired_ns"] += 1
        with self.assertRaisesRegex(ValueError, "clock origins"):
            assess_receipts(rows, receipts)

    def test_missing_terminal_summary_preserves_observed_failures(self):
        rows, receipts, phase = self.fixture()
        incomplete = assess_receipts(rows[:-1], receipts[:-1], phase)
        self.assertEqual(incomplete["status"], "inconclusive")
        self.assertEqual(incomplete["receiver"], "inconclusive")
        self.assertIsNone(incomplete["unprocessed_inputs"])
        self.assertIsNone(incomplete["missing_receipts"])
        self.assertIsNone(incomplete["missing_sample_telemetry"])
        ns, receipt = receipts[0]
        late = assess_receipts(rows[:-1], [(ns + 1, receipt)], phase)
        self.assertEqual(late["status"], "fail")
        self.assertEqual(late["absolute_late_receipts"], 1)

    def test_historical_absence_of_split_output_fields_cannot_pass(self):
        rows, receipts, phases = self.fixture()
        rows[-1].pop("delivered_outputs")
        for sample in rows[1:-1]:
            sample.pop("output_delivered")
        result = assess_receipts(rows, receipts, phases)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["absolute_timing"], "pass")
        rows[1]["output_delivered"] = False
        with self.assertRaisesRegex(ValueError, "authority identity mismatch"):
            assess_receipts(rows, receipts, phases)

    def test_broker_deadline_and_summary_claims_must_be_consistent(self):
        for key in ("publication_misses", "decision_deadline_misses", "missing_outputs", "missing_selected_outputs"):
            with self.subTest(field=key):
                rows, receipts, phase = self.fixture()
                rows[-1][key] = 1
                with self.assertRaisesRegex(ValueError, key):
                    assess_receipts(rows, receipts, phase)
        rows, receipts, _ = self.fixture()
        rows[1]["decision_offset_ns"] = 10_000_001
        with self.assertRaisesRegex(ValueError, "publication_miss"):
            assess_receipts(rows, receipts)
        rows, receipts, phase = self.fixture()
        rows[1]["output_delivery_error"] = "output_write_returned_after_deadline"
        with self.assertRaisesRegex(ValueError, "terminal authority write error"):
            assess_receipts(rows, receipts, phase)

    def test_partial_telemetry_cannot_hide_conflicting_known_writes(self):
        rows, receipts, phases = self.fixture()
        rows.pop(1)
        rows[-1].update(telemetry_records_dropped=1, delivered_outputs=1)
        with self.assertRaisesRegex(ValueError, "authority write count"):
            assess_receipts(rows, receipts[:1], phases)
        # No visible write for sequence 2, but the terminal last write still proves it.
        rows.pop(1)
        rows[-1]["telemetry_records_dropped"] = 2
        with self.assertRaisesRegex(ValueError, "authority write count"):
            assess_receipts(rows, receipts[:1], phases)

    def test_broker_delivered_count_must_agree_with_receipts_and_sample_flags(self):
        rows, receipts, phase = self.fixture()
        rows[-1]["delivered_outputs"] = 1
        with self.assertRaisesRegex(ValueError, "authority write count"):
            assess_receipts(rows, receipts, phase)
        rows[-1]["delivered_outputs"] = 2
        rows[-2]["output_delivered"] = False
        with self.assertRaisesRegex(ValueError, "authority write count"):
            assess_receipts(rows, receipts[:-1], phase)

    def test_dropped_telemetry_keeps_receiver_verdict_separate(self):
        rows, receipts, phase = self.fixture()
        rows.pop(1)
        rows[-1]["telemetry_records_dropped"] = 1
        result = assess_receipts(rows, receipts, phase)
        self.assertEqual(result["receiver"], "pass")
        self.assertEqual(result["telemetry"], "inconclusive")
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["identity_matches"], 1)
        self.assertEqual(result["absolute_deadline_matches"], 2)
        rows[-1]["telemetry_records_dropped"] = 0
        with self.assertRaisesRegex(ValueError, "missing telemetry"):
            assess_receipts(rows, receipts, phase)

    def test_partial_failed_broker_trace_cannot_pass_missing_tail(self):
        rows, receipts, phase = self.fixture()
        rows.pop(2)
        rows[-1].update(phase="failed", acquired_inputs=1, samples=1, unprocessed_inputs=1,
                        outputs=1, selected_outputs=1,
                        delivered_outputs=1, last_delivered_sequence=1, last_selected_sequence=1)
        result = assess_receipts(rows, receipts[:1], phase[:1])
        self.assertEqual(result["receiver"], "pass")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_receipts"], 0)
        self.assertEqual(result["unprocessed_inputs"], 1)
        self.assertEqual(result["withheld_outputs"], 0)

    def test_zero_broker_writes_have_no_receiver_timeliness_evidence(self):
        rows, _, phase = self.fixture()
        for sample in rows[1:3]:
            sample.update(output=None, output_delivered=False)
        rows[-1].update(phase="failed", outputs=0, selected_outputs=0, delivered_outputs=0,
                        last_selected_sequence=0, last_delivered_sequence=0, missing_outputs=2, missing_selected_outputs=2)
        result = assess_receipts(rows, [], phase)
        self.assertEqual(result["receiver"], "inconclusive")
        self.assertEqual(result["absolute_timing"], "inconclusive")
        self.assertEqual(result["missing_receipts"], 0)
        self.assertEqual(result["withheld_outputs"], 2)
        self.assertEqual(result["status"], "fail")

    def test_terminal_write_fault_separates_selection_and_receiver_outcomes(self):
        rows, receipts, phase = self.fixture()
        rows[-2].update(output_delivered=False, output_delivery_error="output_write_deadline_exceeded")
        rows[-1].update(phase="failed", delivered_outputs=1, last_delivered_sequence=1,
                        output_delivery_error="output_write_deadline_exceeded")
        result = assess_receipts(rows, receipts[:1], phase)
        self.assertEqual(result["receiver"], "pass")
        self.assertEqual(result["undelivered_selected_outputs"], 1)
        self.assertEqual(result["status"], "fail")
        rows[-2].update(output_delivered=True, output_delivery_error="output_write_returned_after_deadline")
        rows[-1].update(delivered_outputs=2, last_delivered_sequence=2,
                        output_delivery_error="output_write_returned_after_deadline")
        result = assess_receipts(rows, receipts, phase)
        self.assertEqual(result["receiver"], "pass")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["output_delivery_error"], "output_write_returned_after_deadline")

if __name__ == "__main__":
    unittest.main()
