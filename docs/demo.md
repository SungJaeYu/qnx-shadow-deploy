# Demonstration script

[Play the archived QNX guest demonstration](native-qnx-demo.mp4) or see [how to reproduce it](evaluator.md#archived-native-demo).

## Core message

"A process can be alive and still be wrong. ShadowDeploy tests a candidate on the same input stream before allowing it to supply output."

Use an actual QNX build and target recording for a competition submission claiming QNX execution. The current host workbench is a development demonstration. Keep the backend label visible.

## Three-minute sequence

| Time | Action | Explanation |
| --- | --- | --- |
| 0:00–0:20 | Show baseline-only operation, then attach the separate candidate | The baseline PID stays unchanged while recent inputs prepare candidate state |
| 0:20–0:55 | Show replay completion/live qualification, then run **Reject a unit conversion error** | The candidate is alive but returns the wrong scale; the baseline retains authority |
| 0:55–1:25 | Run **Reject a delayed response** | A bounded wait rejects a late candidate; show the observed missing-output count |
| 1:25–2:05 | Run **Promote, then recover from a crash** | Show qualification, boundary promotion and fallback to the current baseline sample |
| 2:05–2:35 | Show the native IPC source, target console and a recorded trace | Explain where QNX message passing and timeout behavior are used |
| 2:35–2:55 | Show limitations and next milestone | Fallback exists only during probation; target timing is measured under a declared workload |

The script should stay below three minutes. Use an English voiceover or English captions. Do not edit out a missing-output count or substitute simulated timing for measured target timing.

In the host workbench, **Attach a candidate to a running baseline** invokes the generated manifest after 20 inputs. The event list shows replay completion and subsequent live promotion; the downloaded JSONL includes replay counts and the last replayed sequence. For cancellation/retry use the stdin workflow in the README; the browser does not yet send live operator commands.

## Reproducible scenario outcomes

- `promote`: qualify, promote at the next sample, commit after probation, retire baseline.
- `reject-units`: reject a position outside the contract range.
- `reject-delay`: reject a response that exceeds its bounded IPC budget.
- `reject-nan`: reject a non-finite value.
- `reject-stale`: reject an incorrect sequence.
- `reject-protocol`: reject an incompatible message version.
- `reject-switch` adds 2.75 m to the candidate on the last qualifying sample and 5.5 m at promotion, producing a discontinuous switch on the built-in trajectory while keeping the candidate's individual steps within range.
- `semantic-bias` adds a 3 m candidate position bias starting on the sample after promotion.
- `semantic-freeze` holds the candidate position starting on the sample after promotion. A moving input trace can expose the freeze through the sustained residual check.
- `rollback`: candidate exits during probation; use a valid current baseline output.
- `baseline-failure`: stop rather than granting authority to an unqualified candidate.
- `no-fallback`: both versions fail during probation; stop output.
- `post-commit-failure`: candidate fails after standby retirement; stop output.

## Phase timing diagnostics

Add `--phase-timing` to a direct `shadow-demo` invocation to emit
`phase_timing` JSON Lines on stderr after the sample loop and stdout summary.
The broker reserves space for up to the first 4096 samples before the run
and leaves the stdout schema and configured deadlines unchanged. Match
records to stdout samples by sequence within the same invocation.

Timestamps are absolute monotonic nanoseconds. Each row records acquisition,
the IPC deadline, selection and the final write/flush of the sample line.
Each submitted worker has timestamps for submission, dispatch, IPC, receipt
completion, the beginning and return of the broker's future get, and
retirement if it occurs within that sample. A worker without a submission
is represented by null. Null timestamps mark phases that were not reached.
A pre-expiry timestamp identifies a deadline already reached at the dispatch
check before entering transport.

On QNX the IPC interval starts just before arming `TimerTimeout` and ends
just after `MsgSend` returns. The start is recorded only when `MsgSend`
is called. On the host the interval covers the pipe write and any read.
Since futures are collected in baseline-then-candidate order, an already
completed candidate can have a return timestamp later than its completion.
Retirement covers the explicit per-sample teardown. End-of-run cleanup and
staging replay are outside these records.

The extra clock reads can perturb short runs. Compare instrumented and
uninstrumented runs at the same workload and contract before attributing
timing failures to a phase.

## Submission preparation

The intended category is **Developer Tooling & Host Tools**. Review the [official rules](https://qnx.devpost.com/rules) before submitting. Provide the public repository, open-source license, reproducible QNX build/run instructions, evidence of meaningful QNX use, and a public demonstration video. No submission has been made by creating this repository.
