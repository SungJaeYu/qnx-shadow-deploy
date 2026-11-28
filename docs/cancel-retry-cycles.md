# Repeated cancellation and retry

This check runs 640 fresh live samples by default through one stock broker. It stages the
stock candidate, waits for historical replay to catch up and observes a valid
live shadow sample before cancelling. It repeats that sequence for 20 consecutive
generations. Generation 21 completes qualification and probation, commits,
and serves subsequent samples. The baseline must supply every selected output
before the final promotion.

The driver keeps broker telemetry in a local regular file and sends commands
through a local FIFO. The parent supervises broker and controller startup and
exit with bounded waits. The driver checks that each cancelled PID disappears while the
broker and baseline are still alive, that the baseline disappears after the
final commit while the broker and final candidate are still alive, and that
the final candidate disappears after broker exit. The verifier independently
checks generation ordering, stage and cancel acceptance, candidate and baseline
identities, the retirement observations, and selected-source continuity.
A shadow observation flagged as omitted earns no qualification credit even
if the candidate result is valid after the broker drains it. Promotion
requires the configured number of prior credits and a valid current result.
Candidate identity is scoped to each generation. A PID can be reused after
its earlier generation has retired. The controller sends cancel as soon as its bounded scan finds replay completion
and the first valid live shadow sample. It checks the candidate PID and
retirement afterward. If cancellation takes effect after promotion, the
verifier fails the requested shadow-phase coverage and preserves the trace.

## Host

Build the usual stock binaries and manifest. After a successful build, run
from the repository root with a new absolute output directory.

```sh
run=/tmp/shadow-cancel-cycles-host-$(date +%Y%m%d-%H%M%S)
sh tools/cancel_retry_cycles.sh ./build "$run"
python3 tools/verify_cancel_retry_cycles.py "$run"
python3 tests/cancel_retry_cycles_verifier.py "$run"
python3 tests/cancel_retry_cycles_driver.py
```

For an explicitly wider functional run, set
`SHADOW_PERIOD_MS=200 SHADOW_DEADLINE_MS=150` for the driver command and use
a fresh output directory. The default uses the broker's 20 ms period and
10 ms decision deadline. IPC receives three quarters of the decision budget.
The wider run takes approximately 128 seconds before host or target scheduling
delays. The negative verifier check mutates temporary copies of this run.

## QNX

Copy the shell driver to the target and run it against the directory containing
the target-native `shadow-demo`, `shadow-worker`, `shadow-candidate`, and
`candidate.manifest`. Create the parent output directory if needed.

```sh
run=/data/shadow-cancel-cycles-$(date +%Y%m%d-%H%M%S)
sh /data/cancel_retry_cycles.sh /system/bin/shadowdeploy "$run"
```

Export the whole run directory and run the same Python verifier on the host
against that fresh directory. Preserve the original trace, stderr and any
partial retirement observations if a command fails.

## Extended qualification

Pass `10000` as the driver's third argument and verify with `--samples 10000`.
The extended run uses the same 20 cancellation generations and final commit.
It continues through 10,000 live samples and takes about 200 seconds at the
default 20 ms period. The default verifier still requires exactly 640 samples. To replay the
2,000-sample comparison captures, use `--samples 2000`.

```sh
run=/tmp/shadow-cancel-cycles-10000-$(date +%Y%m%d-%H%M%S)
sh tools/cancel_retry_cycles.sh ./build "$run" 10000
python3 tools/verify_cancel_retry_cycles.py "$run" --samples 10000 --require-timing
```

### Profiled QNX guest

Use the [QNX guest candidate profile](qnx-setup.md) on the configured
x86-64 guest. Run the current driver with the profile option and set the
campaign timing explicitly.

```sh
run=/data/shadow-cancel-cycles-profile-$(date +%Y%m%d-%H%M%S)
SHADOW_PERIOD_MS=20 SHADOW_DEADLINE_MS=10 \
  sh /data/cancel_retry_cycles.sh --qnx-guest-profile /system/bin/shadowdeploy "$run" 10000
```

After exporting that directory, verify on the host with both gates:

```sh
python3 tools/verify_cancel_retry_cycles.py "$exported_run" \
  --samples 10000 --qnx-guest-profile --qualification-profile
```

The guest profile gate checks the native QNX backend and the
`qnx-guest-16-mib` launch marker. The qualification timing gate also checks
the guarded fixed grid and the exact 20 ms period, 10 ms decision deadline
and 7.5 ms IPC budget. The driver saves `pidin` descriptor
snapshots before the first stage, after each candidate exits and after
the baseline exits. Use the snapshots to check that the initial
side-channel set returns after each cancellation, then that the final
candidate replaces the baseline connection after commit. Preserve
the campaign's workload, timing profile and exact build identity with
the trace.

## Verdicts

The verifier prints the configured timing, skipped nominal slots, decision
misses and maximum acquisition interval separately from the lifecycle verdict.
Pass `--require-timing` to require zero empty nominal slots and zero decision misses
at the run's configured budget. Use `--qualification-profile` to additionally
bind the configured timing to the stock guarded-grid profile. A decision deadline
miss that suppresses selected output also fails the functional continuity check.
Use `--sensor-trace` for a comparison driven by a prerecorded millimetre trace
on the guarded grid. The default expects broker-generated inputs. Preserve the
input file and digest with the trace so the verifier's source marker can be
corroborated. The report describes broker-selected
outputs, process retirement and timing for the recorded run. The optional
descriptor snapshots provide independent connection evidence. External
delivery and worst-case execution time require separate measurements.

## Independent authority receipts

For split-output captures, use an independent reader on the same target
monotonic clock. Keep the receipt file with the broker trace. Each receipt
line contains the receiver timestamp in integer nanoseconds, a space, and
the complete authority JSON record. The offline checker accepts plain or
gzip-compressed inputs.

```sh
python3 tools/verify_output_receipts.py "$run/cycles.jsonl.gz" "$run/receipts.gz"
```

The receiver verdict compares observed receipts with broker-reported
authority writes and the deadlines advertised in their records. The workload
verdict also accounts for unprocessed inputs, withheld outputs, selected
outputs that the broker could not write, and terminal write errors. Telemetry
drops can leave receiver coverage complete while some source, position or
deadline joins remain unverified.

New broker summaries carry the monotonic timing origin. Live-range samples
can anchor the clock from their absolute acquisition timestamps. For other
captures without a summary origin, pass `--phase-trace` with the broker
stderr containing `phase_timing` records when available. Historical captures without either anchor can verify
receipt counts and deadlines advertised in the records. The absolute timing
verdict remains inconclusive. If the terminal summary is absent, counts that
depend on broker totals remain unknown. Observed late receipts or explicit
broker failures still fail the check. The checker exits 0 for a fully corroborated
receipt check, 1 for a failure, and 77 for incomplete corroboration. Use the
lifecycle and cadence verifier separately for deployment qualification.
