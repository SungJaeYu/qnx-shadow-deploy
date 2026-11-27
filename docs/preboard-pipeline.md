# Pre-board sensor and telemetry demo

The broker uses its synthetic input by default. The commands below use a Linux or macOS workbench and a separate QNX producer. A host JSONL stream can exercise the same relay with a `host-smoke` source label.

## Replay a sensor trace

Create a regular ASCII file with one signed base-10 integer in millimetres per LF-terminated line. Use exactly as many lines as `--samples`. Each line can contain at most 16 bytes before LF, with positions in the inclusive range ±1,000,000 mm. The broker accepts up to 100,000 trace samples. It rejects an invalid, truncated, overlong, or extra row before it starts workers or writes metadata. The file contains only measured position. It carries neither reference truth nor acquisition timestamps.

```sh
printf '10250\n10250\n10250\n' > trace.mm
./build/shadow-demo --worker ./build/shadow-worker --scenario promote \
  --samples 3 --period-ms 100 --deadline-ms 80 --sensor-trace trace.mm > run.jsonl
python3 tools/serve.py --samples 3 --period-ms 100 --deadline-ms 80 --sensor-trace trace.mm
```

Choose **Promote and commit a healthy version** for the three-sample workbench example. With the default qualification settings, three samples are enough to inspect both workers' positions but too few to promote. Longer traces can exercise deployment. The local HTTP run watchdog allows the configured sample count times period plus 15 seconds for startup and overhead. The broker validates and converts the trace to metres before starting the run, then assigns one broker-local monotonic timestamp and sequence at each paced release. Both workers receive the same `Sample`, including during live deployment. Telemetry marks the source as `sensor-trace-mm`, logs measurement as `input`, and sets `truth` to null. The workbench labels the plotted measurement **Sensor input**.

A future QNX sensor capture adapter should read from the BSP's configured I²C resource manager, use the selected sensor's documented register and calibration map, and convert its measurements to integer millimetres before writing the trace. QNX SDP 8 exposes `hw/i2c.h` and `DCMD_I2C_SENDRECV` for driver-supported write/read transactions. Configure the bus, slave address, transaction timeout and conversion for the actual device. Stop the capture and discard the partial file on invalid readings or bus errors. Publish the finished capture as a regular file only after successful validation. This replay path measures broker behavior at its configured rate. I²C wiring, capture latency and live acquisition remain hardware qualification tasks.

## Watch board telemetry

On QNX, start a finite run in a private directory on an approved writable filesystem with sufficient space. Use a new file per run. The broker writes to that file, with stderr retained in a separate file. The command uses a functional demo timing profile.

```sh
umask 077
mkdir /tmp/shadow-run-001
cd /tmp/shadow-run-001
/path/to/shadow-demo --worker /path/to/shadow-worker --scenario rollback \
  --samples 180 --period-ms 200 --deadline-ms 150 > run.jsonl 2> run.stderr
```

From the workstation, attach a read-only SSH tail. Keep the producer session and the SSH reader separate. Replace `board` with the QNX host's configured SSH alias.

```sh
ssh -T board 'tail -n +1 -f /tmp/shadow-run-001/run.jsonl' |
  python3 tools/serve.py --board-stdin pi4
```

Open the printed loopback URL and select **Watch board stream**. Use `cat` in place of `tail` for a completed run when the reader should exit at EOF. For a host check, pipe `cat run.jsonl` into `python3 tools/serve.py --board-stdin host-smoke`. The source label identifies the upstream operator alias. The broker backend field distinguishes native QNX IPC from host POSIX IPC.

- The relay reads frames of at most 8,192 bytes including LF. It skips an oversized frame through its next LF. It drops malformed or non-monotonic rows, and requires metadata at the start of each upstream read.
- Each metadata frame starts a relay run and clears the prior window. New broker logs carry a producer run ID. A reconnect should replay the spool from metadata. It gets a fresh relay run ID and preserves the producer run ID for the same spool. Let SSH exit and close the local stdin pipe on disconnect, then restart the pipe manually to reconnect. The workbench remains available after EOF and displays the disconnect. A reconnect loop that keeps the pipe open would hide that EOF. A stream that resumes mid-file cannot be attributed to a run.
- `GET /api/board` serves a read-only snapshot on loopback. It retains the newest 256 accepted rows plus metadata, reports sequence gaps, evictions, malformed frames and oversized frames, and marks summary completion separately from EOF or read error. An evicted row remains in the target spool. Malformed and oversized counts accumulate for the life of the relay. Gaps and evictions reset with each metadata frame.
- Board charts break across sequence gaps. Download is available for a completed snapshot with no sample gaps or row evictions. Use the original target spool for complete evidence when the snapshot has losses. EOF without a summary leaves the run incomplete. With `tail -f`, the upstream stays connected until the tail/SSH reader exits, even after a summary.
- Browser requests do not drive the producer or the stdin reader. A slow SSH reader can stall its own tail. A slow or disconnected HTTP viewer can stall only its bounded request, which has a two-second socket timeout. This workflow uses a finite independent spool. Broker stdout still writes synchronously and can be delayed by local filesystem pressure. Producer-side telemetry isolation belongs to issue #4. Keep the spool on a dedicated path with sufficient space and preserve it separately from relay snapshots.

## Drive a demonstration LED

The LED consumer runs separately from the broker and takes broker JSONL on stdin. Copy `tools/led_events.py` and `tools/board_relay.py` into the same target directory to run it on a QNX image with Python 3.9 or newer. Dry-run emits JSON on transitions.

```sh
python3 tools/led_events.py --hold-samples 10 < run.jsonl
tail -n +1 -f /tmp/shadow-run-001/run.jsonl |
  python3 /path/to/led_events.py --hold-samples 10
```

The state is `qualification` during warming and shadowing, `normal` when an eligible version owns output outside qualification, and `rollback` for the rollback or probation-cancellation sample and the next nine sequence slots by default. A sequence gap consumes elapsed slots. Failed state, summary, new metadata, malformed input, normal EOF and handled termination reset the indication to `off`.

To connect hardware, install a target adapter executable and pass `--adapter /absolute/path/to/adapter`. The consumer invokes it with exactly one argument from `off`, `normal`, `qualification` or `rollback`. Each call must set the complete desired LED state idempotently and return success within one second. The adapter should map those states to approved BSP GPIO operations, initialize its output pins safely, and turn all indicator outputs off for `off`. Example mappings on a three-channel LED are green for normal, amber for qualification and red for rollback. A command failure triggers a final attempt to set `off`. Use a board-specific driver or resource manager and verify pin mapping, voltage, and driver privileges before enabling the adapter. GPIO hardware operation and the required Python runtime on a deployment image have yet to be verified on the board.

## Periodic service continuity

The broker measures acquisition and decision timing on its monotonic clock.
For synthetic and file-trace inputs, slot zero begins at the first scheduled
release after metadata is written. Slot `n` covers the half-open interval
`[n × period, (n + 1) × period)` from that origin. The actual schedule uses
this same fixed grid. After processing, the broker chooses the earliest grid
boundary at or after work completion and at least half a period after the
actual acquisition. The spacing guard limits short estimator intervals while
allowing ordinary late wake-ups to recover the original cadence. A long
oversleep or overrun can jump directly to a later slot. Each release processes
one input, and continuity counts slots based on actual acquisition timestamps.

Live range FD reads retain producer-relative pacing. If the first accepted
producer timestamp precedes the initial broker release, that timestamp is
the nominal slot origin for continuity. See [Live range input](live-range-input.md)
for acquisition and read deadlines.

The summary horizon ends at the nominal slot containing the last acquisition.
`initial_unoccupied_slots` counts slots before the first acquisition.
`skipped_nominal_slots` counts empty slots strictly between successive
acquisitions. The first acquisition contributes zero to that second count.
With no acquisitions the last slot is null and both counts are zero.
Remaining requested acquisitions are reported separately as `unprocessed_inputs`
after an early stop or interruption. The nominal horizon can exceed the request
count when the broker takes longer than planned to acquire all requested inputs.

- Metadata records `requested_inputs` and `release_policy` (`fixed-grid` or
  `acquisition-relative`). Earlier traces without the policy field use
  acquisition-relative scheduling. Samples record `nominal_slot`,
  `acquired_offset_ns`, `scheduled_release_offset_ns` and `decision_offset_ns`.
  The offsets are elapsed nanoseconds from slot zero. The decision timestamp
  covers the selection attempt, including attempts that stop service.
- Each sample records `release_lateness_ms` relative to its actual scheduled
  release, clamped to zero when a live measurement precedes the read release,
  and `nominal_slot_lateness_ms` relative to the start of its occupied
  nominal slot. This second value is the offset within the slot. The slot index
  and skipped-slot counts show drift across slot boundaries.
  `nominal_grid_lateness_ms` measures total drift from the scheduled time of
  that input sequence on the original grid, including whole skipped slots.
- `acquisition_interval_ms` is the elapsed time since the preceding acquisition
  and is null on the first sample. The final summary gives the maximum and the
  workbench labels latest and maximum separately.
- The summary accounts for requested, acquired, selected, missing selected and
  unprocessed inputs. A decision deadline miss counts a selection attempt that
  exceeded the acquisition-relative deadline. The older `samples`, `outputs`,
  `missing_outputs` and `publication_misses` keys retain their meanings.
- `max_selected_interval_ms` is the maximum elapsed time between successive
  successful broker selections. `effective_selected_rate_hz` is the number of
  successful selection intervals divided by the elapsed seconds from first to
  last selection. Both values are null until at least two successful selections
  exist. The maximum acquisition interval likewise requires two acquisitions.
  Lateness maxima require at least one acquisition.

The broker reports `external_delivery: "unmeasured"`. The relay window shows
frames received by the workbench and counts missing and evicted frames separately.
Once a broker summary arrives, its continuity values describe the producer run
even if the relay window contains a subset of frames. Older recordings display
unavailable continuity values.

The #5 controlled-trial analyzer checks these fields against the emitted
acquisition and selection timestamps. A new run passes the continuity check if
every requested input was acquired and selected, no observed nominal slot in
the measured horizon was unoccupied, and no unexpected deadline failure occurred.
Older traces retain their original criteria with continuity marked unavailable.
