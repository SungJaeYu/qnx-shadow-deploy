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
`artifacts/lifecycle-host-2026-11-26` retains the broker trace and
retirement records. The trace SHA-256 is
`5253f8b36b8ab83aecbd8aee56702a536fff29a8daff1e004557d61eb71a9061`.
The full host CTest run passed all eight suites, including the lifecycle
functional check.

## QNX x86-64 guest observation

The project executables were built from clean commit
`ebd2c9c7cedd33d9d43b41fd56efe213e502b78d` with QCC 12.2.0 and ran on a
QNX 8.0.0 guest under QEMU 11.1.3 TCG with two vCPUs, 1 GiB RAM, no NIC and
a snapshot disk. The host used lifecycle verifier `699aac9`.
The run used the same 90 inputs, 200 ms period, 150 ms decision budget and
112.5 ms IPC cutoff as the host run.

| Observed result | Value |
| --- | ---: |
| Generation 1 promoted | Sequence 25 |
| Rollback to the current baseline | Sequence 27 |
| Generation 2 promoted | Sequence 32 |
| Generation 2 committed | Sequence 55 |
| Selected / requested outputs | 90 / 90 |
| Decision deadline misses | 0 |
| Maximum decision latency | 8.311676 ms |
| Skipped nominal slots | 1 |
| Max nominal-grid lateness | 283.124197 ms |
| Effective selected rate | 4.923900181 Hz |

The runner exited 0. The verifier confirmed the baseline PID remained
876566 through commit. Worker side channels connected to baseline 876566
and first candidate 1527831 during probation, reverted to the baseline
after rollback, and connected to second candidate 1798167 after commit.
Ordinary descriptors counted 5 at baseline, 7 during probation, and 5 after
both rollback and commit. The finished process snapshot contained none of
the broker or worker PIDs. Strict timing exited 1 for the skipped nominal
slot. These measurements characterize this guest run.

The local archive
`artifacts/qnx-final-ebd2c9c7/native-run.tgz` retains the trace, stderr and
`pidin` snapshots. Its SHA-256 is
`20eb9f3b48e3f42a716eb7cecc70e6bf7cc5d1ab3be2c974755188a27a7954f4`.
The adjacent README records compiler, artifact and image hashes. Physical
hardware timing remains unmeasured.

## QNX protocol v2 conformance

A separate QNX 8.0.0 x86-64 guest under QEMU 11.1.3 TCG ran the
`qnx-reply-conformance` executable from the same clean source commit.
The owned loopback channel exercised 77 exchanges. The decoder accepted 4
canonical replies and rejected all 73 noncanonical replies in this matrix.
The executable exited 0, as did `protocol-tests` in the same guest.

| Artifact | SHA-256 |
| --- | --- |
| `qnx-reply-conformance` | `5c2f1ee409df65f03098f6334411696b1d2f1cd8e0247809c610bec1b0a13926` |
| `protocol-tests` | `dd7862db72e8b499b15eecc1a123dec55c4ed170e73bb0256e38f1714aa26ba` |
| IFS | `6dcc0d0b4fe05fdb450b38dc4b71abd7bcfb43f92a138d48c2fe427b277d6e25` |
| Disk | `457ddb726310421aba9edbbb44422a139deb8dc5bc36e71e99b05aca3a2c8992` |
| `system_files.shadowdeploy` image build snippet | `2d4401f85213cc56c94bdb64b795d85e722a0d8c9d9dc34c160366d9d10ce91a` |

The local run record and provenance are retained in
`artifacts/native-conformance-2026-11-26/`. See the
[protocol contract](../architecture.md#sample-and-output-contracts) for the
limits on observing oversized replies through QNX IPC.
