# Live range input

`shadow-demo --live-range-fd FD` reads from an inherited, dedicated read-only
pipe or FIFO descriptor above 2. The producer owns the write end and must run
on the same host and use the same `CLOCK_MONOTONIC` clock as the broker.
The input works with the existing scenarios, deployment controls and
`--output-fd`. Select either live range input or `--sensor-trace`.

Each record is ASCII with one space between fields and a newline terminator.
The maximum record size is 64 bytes including the newline.

```text
mm <sequence> <acquired_ns> <range_mm>\n
```

- Start the producer sequence at 1 on each broker run and advance by exactly
  one for each valid acquisition.
- Supply the original measurement timestamp in nanoseconds from the shared
  monotonic clock. Timestamps must be positive, strictly increasing and no
  later than the broker's current time. The adapter must preserve the actual
  acquisition timestamp across any handoff before writing the record.
- Supply an unsigned integer distance from 0 to 1,000,000 millimetres,
  inclusive. The broker converts the value to metres exactly once before
  passing the same sample to each attached worker.
- Produce one fresh measurement around each configured period. Buffering
  consumes the acquisition-based IPC budget. At acceptance the age must be
  less than three quarters of the decision deadline. IPC and publication
  deadlines are still measured from acquisition. The broker starts its next
  read at the later of work completion and one period after the preceding
  acquisition. A queued record can therefore have an acquisition timestamp
  before the read release. Its original timestamp still governs the age check
  and continuity. Synthetic and file-trace inputs use a broker-owned fixed grid.
- The broker waits up to one configured period per input read. The deadline
  covers the entire record, even when writes are fragmented. The wait is
  separate from the acquisition-based decision budget.

Wait for broker metadata before starting the example producer. The host
launcher does this automatically and emits the broker's JSON Lines to stdout.

```sh
python3 tools/live_range_example.py --build-dir build
```

The example writes a known constant from user space to exercise the adapter
contract. A real adapter should emit a record only after a valid hardware
measurement. A bad record, a missing or stalled record, or a disconnected
producer stops the run before dispatching that input. Previously selected
outputs remain in the evidence. Exit status 5 and the summary's
`input_error` identify the input failure. Stderr includes the expected sequence.
The summary counts the failed input and subsequent inputs as unprocessed.
`--no-telemetry` still leaves the diagnostic on stderr.

Metadata identifies the input source as `live-range-mm`. For this source each
sample row includes the supplied `acquired_ns` and uses `truth: null`.
Metadata reports `release_policy: acquisition-relative`. If the first
accepted acquisition predates the broker's initial read release, its
timestamp is the origin of nominal slot zero. The authority pipe retains
its established record schema.

## Hardware validation status

The interface specifies a local software handoff. Actual driver access,
sensor model and calibration, acquisition timestamp origin, QNX BSP and
pin configuration, LED mapping, hardware timing and external authority
delivery require validation on the selected target. The host launcher and
cross-compilation do not provide that evidence.
