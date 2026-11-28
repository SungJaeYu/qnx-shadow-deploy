#!/usr/bin/env python3
"""Assess independent authority FIFO receipts against available broker telemetry."""
import argparse
import gzip
import json
import math
from pathlib import Path

from verify_qnx_logs import check


def integer(value, minimum):
    return type(value) is int and value >= minimum


def sequence_map(rows, maximum, description):
    result = {}
    previous = 0
    for row in rows:
        sequence = row.get("sequence")
        check(integer(sequence, 1) and previous < sequence <= maximum,
              f"{description}: invalid, duplicate or out-of-order sequence")
        result[sequence] = row
        previous = sequence
    return result


def finite(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def assess_receipts(rows, receipts, phase_rows=()):
    check(rows and rows[0].get("type") == "meta", "missing broker metadata")
    meta = rows[0]
    summaries = [row for row in rows[1:] if row.get("type") == "summary"]
    check(len(summaries) <= 1 and (not summaries or summaries[0] is rows[-1])
          and not any(row.get("type") == "meta" for row in rows[1:]), "invalid broker trace envelope")
    summary = summaries[0] if summaries else None
    requested, deadline, period = (meta.get(key) for key in ("requested_inputs", "deadline_ms", "period_ms"))
    run_id = meta.get("producer_run_id")
    check(integer(requested, 1) and requested <= 1_000_000 and integer(deadline, 1)
          and integer(period, 2) and deadline < period <= 1000 and isinstance(run_id, str) and run_id,
          "invalid receiver qualification metadata")
    deadline_ns = deadline * 1_000_000
    acquired = summary.get("acquired_inputs") if summary is not None else requested
    check(integer(acquired, 0) and acquired <= requested, "invalid broker acquisition count")
    samples = sequence_map((row for row in rows if row.get("type") == "sample"), acquired, "broker sample")
    phases = sequence_map(phase_rows, acquired, "broker phase timing")
    missing_telemetry = acquired - len(samples)
    for sequence, phase in phases.items():
        check(integer(phase.get("acquired_ns"), 0) and integer(phase.get("selected_ns"), phase["acquired_ns"]),
              f"sample {sequence}: invalid absolute phase timestamps")
    drops = None
    if summary is not None:
        drops = summary.get("telemetry_records_dropped", 0)
        check(integer(drops, 0) and missing_telemetry <= drops, "missing telemetry without recorded drops")

    absolute_origins = set()
    summary_origin = summary.get("timing_origin_ns") if summary is not None else None
    if summary is not None and "timing_origin_ns" in summary:
        check(integer(summary_origin, 0), "invalid broker timing origin")
        absolute_origins.add(summary_origin)
    live_range_anchored = False
    previous_offset = -1
    visible_misses = 0
    for sequence, sample in samples.items():
        acquired_offset, decision_offset = (sample.get(key) for key in ("acquired_offset_ns", "decision_offset_ns"))
        check(integer(acquired_offset, previous_offset + 1) and integer(decision_offset, acquired_offset),
              f"sample {sequence}: invalid acquisition or selection offset")
        missed = decision_offset - acquired_offset > deadline_ns
        check(sample.get("publication_miss") is missed and (not missed or sample.get("output") is None),
              f"sample {sequence}: inconsistent publication_miss or selected output")
        check("output_delivered" not in sample or type(sample["output_delivered"]) is bool,
              f"sample {sequence}: invalid output_delivered")
        visible_misses += missed
        previous_offset = acquired_offset
        if meta.get("input_source") == "live-range-mm" and "acquired_ns" in sample:
            acquisition = sample["acquired_ns"]
            check(integer(acquisition, acquired_offset), f"sample {sequence}: invalid live acquisition timestamp")
            absolute_origins.add(acquisition - acquired_offset)
            live_range_anchored = True
        if sequence in phases:
            phase = phases[sequence]
            origin = phase["acquired_ns"] - acquired_offset
            check(origin >= 0 and phase["selected_ns"] == origin + decision_offset,
                  f"sample {sequence}: phase chronology mismatch")
            absolute_origins.add(origin)
    check(len(absolute_origins) <= 1, "broker timing uses inconsistent clock origins")

    received = list(receipts)
    records = sequence_map((record for _, record in received), acquired, "receiver")
    previous_time = 0
    late = identity_matches = 0
    inferred_origins = set()
    for received_ns, record in received:
        sequence = record["sequence"]
        check(record.get("type") == "authoritative_output" and type(record.get("schema")) is int
              and record["schema"] == 1, f"receipt {sequence}: invalid record schema")
        check(record.get("producer_run_id") == run_id, f"receipt {sequence}: wrong producer run ID")
        advertised = record.get("deadline_ns")
        check(record.get("source") in ("baseline", "candidate") and finite(record.get("position_m"))
              and integer(advertised, deadline_ns), f"receipt {sequence}: invalid authority record")
        check(integer(received_ns, previous_time), f"receipt {sequence}: invalid or out-of-order receive timestamp")
        check(received_ns >= advertised - deadline_ns, f"receipt {sequence}: receive timestamp precedes acquisition")
        previous_time = received_ns
        late += received_ns > advertised
        if sequence in samples:
            sample = samples[sequence]
            check(sample.get("output_delivered", True) is True and sample.get("publication_miss") is False
                  and record["source"] == sample.get("source") and finite(sample.get("output"))
                  and record["position_m"] == sample["output"], f"receipt {sequence}: authority identity mismatch")
            inferred_origins.add(advertised - deadline_ns - sample["acquired_offset_ns"])
            identity_matches += 1

    check(len(inferred_origins) <= 1 and all(origin >= 0 for origin in inferred_origins),
          "receipt deadlines imply inconsistent acquisition clock origins")
    origin = next(iter(absolute_origins or inferred_origins), None)
    clock = ("broker_summary" if summary_origin is not None else
             "live_range" if live_range_anchored else "phase_timing" if phases else
             "inferred_from_receipt_deadlines" if inferred_origins else "unavailable")
    absolute_matches = absolute_late = 0
    for received_ns, record in received:
        sequence = record["sequence"]
        if sequence in phases:
            phase = phases[sequence]
            acquisition, selection = phase["acquired_ns"], phase["selected_ns"]
        elif origin is not None and sequence in samples:
            sample = samples[sequence]
            acquisition = origin + sample["acquired_offset_ns"]
            selection = origin + sample["decision_offset_ns"]
        else:
            continue
        check(record["deadline_ns"] == acquisition + deadline_ns,
              f"receipt {sequence}: deadline disagrees with broker acquisition")
        check(received_ns >= selection, f"receipt {sequence}: receive timestamp precedes selection")
        if sequence in phases or absolute_origins:
            absolute_matches += 1
            absolute_late += received_ns > acquisition + deadline_ns

    selected = written = None
    split_output_complete = False
    errors = [sample["output_delivery_error"] for sample in samples.values()
              if sample.get("output_delivery_error") is not None]
    check(all(isinstance(error, str) and error for error in errors) and len(set(errors)) <= 1,
          "inconsistent sample authority write errors")
    error = errors[0] if errors else None
    if summary is not None:
        selected = summary.get("selected_outputs")
        written = summary.get("delivered_outputs")
        summary_error = summary.get("output_delivery_error")
        check(error is None or error == summary_error, "terminal authority write error disagrees with sample")
        error = summary_error
        check(integer(selected, len(records)) and selected <= acquired, "invalid broker selection count")
        check(written is None or (integer(written, len(records)) and written <= selected),
              "invalid broker authority write count")
        check(error is None or (isinstance(error, str) and error), "invalid terminal authority write error")
        known_selected = {seq for seq, sample in samples.items() if sample.get("output") is not None}
        check(len(known_selected) <= selected <= len(known_selected) + missing_telemetry,
              "broker selection count conflicts with sample telemetry")
        for key, expected in (("samples", acquired), ("requested_inputs", requested),
                              ("unprocessed_inputs", requested - acquired), ("outputs", selected),
                              ("missing_outputs", acquired - selected),
                              ("missing_selected_outputs", acquired - selected)):
            if key in summary:
                check(type(summary[key]) is int and summary[key] == expected, f"invalid broker {key}")
        reported_misses = None
        for key in ("publication_misses", "decision_deadline_misses"):
            if key in summary:
                count = summary[key]
                check(integer(count, visible_misses) and count <= min(acquired - selected,
                                                                     visible_misses + missing_telemetry)
                      and (reported_misses is None or reported_misses == count), f"invalid broker {key}")
                reported_misses = count
        if written is not None:
            known_written = {seq for seq, sample in samples.items() if sample.get("output_delivered") is True}
            unknown_write_count = sum("output_delivered" not in samples[seq] for seq in known_selected)
            split_output_complete = all("output_delivered" in sample for sample in samples.values())
            check(known_written <= known_selected and len(known_written) <= written <= (
                len(known_written) + missing_telemetry + unknown_write_count
            ), "broker authority write count conflicts with sample telemetry")
            for key, count, known in (("last_selected_sequence", selected, known_selected),
                                      ("last_delivered_sequence", written, known_written)):
                last = summary.get(key)
                check(integer(last, max(count, max(known, default=0))) and last <= acquired
                      and bool(last) == bool(count),
                      f"invalid broker {key}")
                if last in samples and (key == "last_selected_sequence" or "output_delivered" in samples[last]):
                    check(last in known, f"invalid broker {key}")
            last_written = summary["last_delivered_sequence"]
            known_written.update(records)
            if last_written:
                known_written.add(last_written)
            check(len(known_written) <= written, "broker authority write count conflicts with observed writes")
            check(max(records, default=0) <= last_written, "receipt sequence exceeds last broker authority write")

    missing = None if written is None else written - len(records)
    workload_missing = requested - len(records)
    receiver = ("fail" if late or missing else
                "pass" if (missing == 0 and records) or workload_missing == 0 else "inconclusive")
    telemetry = "pass" if summary is not None and drops == 0 and missing_telemetry == 0 else "inconclusive"
    absolute_timing = ("fail" if absolute_late else
                       "pass" if records and absolute_matches == len(records) else "inconclusive")
    observed_failure = visible_misses > 0 or any(sample.get("phase") == "failed" for sample in samples.values())
    failed_workload = summary is not None and (workload_missing or summary.get("phase") == "failed"
                                               or summary.get("interrupted"))
    return {
        "status": ("fail" if receiver == "fail" or error is not None or observed_failure or failed_workload
                   else "pass" if split_output_complete and telemetry == absolute_timing == "pass"
                   and identity_matches == requested
                   else "inconclusive"),
        "receiver": receiver,
        "telemetry": telemetry,
        "requested_outputs": requested,
        "acquired_inputs": None if summary is None else acquired,
        "selected_outputs": selected,
        "broker_authority_writes": written,
        "received_outputs": len(records),
        "requested_outputs_without_receipt": workload_missing,
        "unprocessed_inputs": None if summary is None else requested - acquired,
        "withheld_outputs": None if selected is None else acquired - selected,
        "undelivered_selected_outputs": None if written is None else selected - written,
        "output_delivery_error": error,
        "missing_receipts": missing,
        "late_receipts": late,
        "identity_matches": identity_matches,
        "available_sample_telemetry": len(samples),
        "missing_sample_telemetry": None if summary is None else missing_telemetry,
        "telemetry_records_dropped": drops,
        "clock_origin": clock,
        "absolute_timing": absolute_timing,
        "absolute_deadline_matches": absolute_matches,
        "absolute_late_receipts": absolute_late,
    }


def read_text(path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open(encoding="utf-8")


def unique_fields(pairs):
    record = {}
    for key, value in pairs:
        check(key not in record, f"duplicate JSON field: {key}")
        record[key] = value
    return record


def decode_record(line):
    return json.loads(line, object_pairs_hook=unique_fields)


def read_receipts(path):
    with read_text(path) as stream:
        for line in stream:
            timestamp, record = line.split(" ", 1)
            check(timestamp.isascii() and timestamp.isdecimal(), "malformed receiver timestamp")
            yield int(timestamp), decode_record(record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("telemetry", type=Path, help="broker JSONL trace, optionally gzipped")
    parser.add_argument("receipts", type=Path, help="timestamp-prefixed receiver records, optionally gzipped")
    parser.add_argument("--phase-trace", type=Path, help="optional broker stderr containing phase_timing JSONL")
    args = parser.parse_args()
    try:
        with read_text(args.telemetry) as stream:
            rows = [decode_record(line) for line in stream]
        phases = []
        if args.phase_trace:
            with read_text(args.phase_trace) as stream:
                phases = [decode_record(line) for line in stream if line.startswith('{"type":"phase_timing",')]
            check(phases, "no phase_timing records in supplied phase trace")
        result = assess_receipts(rows, read_receipts(args.receipts), phases)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        parser.exit(1, f"FAIL: {error}\n")
    print(json.dumps(result, indent=2))
    return {"pass": 0, "fail": 1, "inconclusive": 77}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
