"""Bounded, read-only broker JSONL relay shared by workbench observers."""
from collections import deque
import json
import math
import threading
import uuid

MAX_FRAME_BYTES = 8192
RING_SIZE = 256
MAX_SEQUENCE = 1_000_000


def frames(stream):
    """Yield (row, error) per newline-delimited frame with bounded reads."""
    while line := stream.readline(MAX_FRAME_BYTES + 1):
        if len(line) > MAX_FRAME_BYTES:
            yield None, "oversize"
            while line and not line.endswith(b"\n"):
                line = stream.readline(MAX_FRAME_BYTES + 1)
            continue
        if not line.endswith(b"\n"):
            yield None, "malformed"
            break
        try:
            row = json.loads(line.decode("utf-8"), parse_float=finite_float, parse_constant=finite_float)
            if not isinstance(row, dict):
                raise ValueError("expected object")
        except (ValueError, RecursionError):
            yield None, "malformed"
            continue
        yield row, None


def finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_SEQUENCE


def finite_number(value):
    return type(value) in (int, float) and -1e15 <= value <= 1e15


def has_strings(row, *fields):
    return all(isinstance(row.get(field, ""), str) for field in fields)


def valid_meta(row):
    return (type(row.get("schema")) is int and row["schema"] in (1, 2)
            and row.get("backend") in ("host-posix-pipes", "qnx-native-ipc")
            and isinstance(row.get("scenario"), str)
            and has_strings(row, "producer_run_id", "input_source")
            and all(finite_number(row.get(field, 0)) for field in
                    ("baseline_pid", "candidate_pid", "deadline_ms", "qualification_samples", "probation_samples")))


def valid_sample(row):
    return (integer(row.get("sequence"), 1) and finite_number(row.get("time_s")) and row["time_s"] >= 0
            and finite_number(row.get("input")) and "truth" in row
            and (row["truth"] is None or finite_number(row["truth"]))
            and (row.get("output") is None or finite_number(row["output"]))
            and finite_number(row.get("decision_latency_ms", 0))
            and has_strings(row, "phase", "source", "event", "reason", "baseline_check", "candidate_check"))


class BoardRelay:
    def __init__(self, source):
        self.source = source
        self.lock = threading.Lock()
        self.upstream = "waiting"
        self.run_id = None
        self.meta = None
        self.rows = deque(maxlen=RING_SIZE)
        self.sequence = 0
        self.sequence_gaps = 0
        self.evicted = 0
        self.malformed = 0
        self.oversize = 0
        self.complete = False

    def consume(self, stream):
        with self.lock:
            self.upstream = "connected"
            # A fresh reader must establish identity with metadata before samples.
            established = False
        try:
            for row, error in frames(stream):
                with self.lock:
                    if error:
                        if error == "oversize":
                            self.oversize += 1
                        else:
                            self.malformed += 1
                    elif row.get("type") == "meta" and valid_meta(row):
                        self.run_id = uuid.uuid4().hex
                        self.meta = row
                        self.rows.clear()
                        self.sequence = self.sequence_gaps = self.evicted = 0
                        self.complete = False
                        established = True
                    elif not established or self.complete:
                        self.malformed += 1
                    elif not self._append(row):
                        self.malformed += 1
        except OSError:
            with self.lock:
                self.upstream = "read-error"
            return
        with self.lock:
            self.upstream = "disconnected"

    def _append(self, row):
        kind = row.get("type")
        if kind == "sample":
            if not valid_sample(row) or row["sequence"] <= self.sequence:
                return False
            sequence = row["sequence"]
            self.sequence_gaps += sequence - self.sequence - 1
            self.sequence = sequence
        elif kind == "deployment":
            if (not integer(row.get("after_sequence")) or not integer(row.get("generation", 0))
                    or not finite_number(row.get("candidate_pid", -1)) or not has_strings(row, "event", "reason")):
                return False
        elif kind == "summary":
            if not integer(row.get("samples")) or row["samples"] < self.sequence:
                return False
            self.sequence_gaps += row["samples"] - self.sequence
            self.complete = True
        else:
            return False
        if len(self.rows) == RING_SIZE:
            self.evicted += 1
        self.rows.append(row)
        return True

    def snapshot(self):
        with self.lock:
            return {"source": self.source, "upstream": self.upstream, "run_id": self.run_id,
                    "meta": self.meta, "rows": list(self.rows), "complete": self.complete,
                    "sequence_gaps": self.sequence_gaps, "evicted": self.evicted,
                    "malformed": self.malformed, "oversize": self.oversize}
