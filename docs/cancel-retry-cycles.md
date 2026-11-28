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
default 20 ms period. The default verifier still requires exactly 640 samples.

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
  --samples 10000 --qnx-guest-profile --require-timing
```

The profile gate checks the native QNX backend and the
`qnx-guest-16-mib` launch marker. The driver saves `pidin` descriptor
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
in that run. A decision deadline miss that suppresses selected output also
fails the functional continuity check. The report describes broker-selected
outputs, process retirement and timing for the recorded run. The optional
descriptor snapshots provide independent connection evidence. External
delivery and worst-case execution time require separate measurements.
