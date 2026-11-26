# Same-broker crash and retry, 2026-11-26

The [driver](../../tools/lifecycle_checks.sh) starts a baseline, waits for
20 inputs, stages the candidate using a generated manifest, and signals the
broker's owned candidate PID after observing promotion. It waits for rollback
and candidate reaping before staging a second candidate with the same artifact.
The healthy generation completes probation and the broker retires the baseline.
The [verifier](../../tools/verify_lifecycle.py) checks current-sample baseline
rollback, fresh qualification after replay, continuity, retirement and native
side-channel PIDs when available.

## Host functional observation

The macOS POSIX pipe run used 90 requested inputs, a 200 ms period, a 150 ms
decision budget and a 112.5 ms IPC cutoff. The broker ran the dispatch lifetime
fix (`a10c783`). The two generations shared one broker and one baseline
through the second generation's commit.

| Observed result | Value |
| --- | ---: |
| Generation 1 promoted | Sequence 25 |
| Rollback to the current baseline | Sequence 26 |
| Generation 2 promoted | Sequence 31 |
| Generation 2 committed | Sequence 54 |
| Selected / requested outputs | 90 / 90 |
| Decision deadline misses | 0 |
| Skipped nominal slots | 1 |
| Max nominal-grid lateness | 301.137 ms |
| Effective selected rate | 4.916812233 Hz |

The broker exited 0 and the functional checks passed. The strict verifier
exited 1 because of the skipped nominal slot. The run directory at
`/private/tmp/qnx-lifecycle-final-current` retains the broker trace and
retirement records. The trace SHA-256 is
`5253f8b36b8ab83aecbd8aee56702a536fff29a8daff1e004557d61eb71a9061`.
The full host CTest run passed all eight suites, including the lifecycle
functional check.
