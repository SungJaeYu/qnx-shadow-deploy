# Demonstration script

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
- `reject-switch`: reject a transition between individually valid but discontinuous streams.
- `rollback`: candidate exits during probation; use a valid current baseline output.
- `baseline-failure`: stop rather than granting authority to an unqualified candidate.
- `no-fallback`: both versions fail during probation; stop output.
- `post-commit-failure`: candidate fails after standby retirement; stop output.

## Submission preparation

The intended category is **Developer Tooling & Host Tools**. Review the [official rules](https://qnx.devpost.com/rules) before submitting. Provide the public repository, open-source license, reproducible QNX build/run instructions, evidence of meaningful QNX use, and a public demonstration video. No submission has been made by creating this repository.
