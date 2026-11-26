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
its earlier generation has retired. If the controller misses the shadowing
window, the run fails and preserves the observed transitions.

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

## Verdicts

The verifier prints the configured timing, skipped nominal slots, decision
misses and maximum acquisition interval separately from the lifecycle verdict.
Pass `--require-timing` to require zero empty nominal slots and zero decision misses
in that run. A decision deadline miss that suppresses selected output also
fails the functional continuity check. The report describes broker-selected
outputs, process retirement and timing for the recorded run. It does not measure
external delivery, QNX connection counts or worst-case execution time.
