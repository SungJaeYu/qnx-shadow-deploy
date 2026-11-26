# Native cancellation and timing probe, 2026-11-26

These observations are from a reused QNX x86-64 guest image on QEMU 11.1.3
TCG, Darwin 25.6.0 ARM64 host, two virtual CPUs, 1024 MiB RAM, serial console,
snapshot disk and disabled networking. The guest reported
`QNX shadowdeploy-qnx 8.0.0 2026/10/30-09:10:05EDT x86pc x86_64`.
Workspace HEAD was `9f30c4936823515cd368b835329a076b2513580d`. Guest binaries
were reused without rebuild or independent mapping to that revision.

| Artifact | SHA-256 |
| --- | --- |
| IFS | `568094ce7d41d5ac58e2db5a3b65fcae4faa4f2cc718b61ba9a13a57ee699e78` |
| Disk | `59855de8b29f450d32954275825a76fbb7e84b7b62a873e9b2c66d7019fcd49c` |
| Exported guest tarball | `2cc63d1034deb2e20057fd3e5d0acc2dac5ed6b78933779361df300b4a8419c8` |

The retained source archive is
`artifacts/native-cancel-20261126/native-evidence.tgz` in the local
qualification checkout. Guest and host POSIX checksum and byte count matched
(`1401561923 29623`). The local directory also retains the invocation,
parser and extraction. Hardware timing remains unmeasured.

## Lifecycle protocol and result

Run the [lifecycle orchestrator](../../tools/qnx_lifecycle_checks.sh) in the
guest. It writes `cancel` to the control FIFO after observing promotion.
Check the trace for selected-output identity, authority at the boundary,
connection snapshot equality and process cleanup. Keep original logs, exit
records and snapshots before stopping the guest.

The first candidate promoted at sequence 27. The broker accepted the
external cancellation at sequence 31 and selected the baseline on that
boundary. Seven consecutive baseline outputs started at sequence 31.
A new candidate promoted at sequence 38. The baseline and cancelled
connection snapshots matched, and no owned process remained after exit.
The interval between command receipt and an in-flight native IPC wait
was unobserved.

The separate replay-retry attempt reached `replay_deadline_exceeded` in
generation 1 and `replay_stale_or_mismatched_output` in generation 2.
Generation 3 was requested after sequence 32. At sequence 33 the broker
stopped with `decision_deadline_exceeded` and 32 outputs from 33 inputs.
The driver failed waiting for the third preparation failure, which never
arrived. There was no replay-retry exit record. Keep this failed retry
alongside the completed cancellation trace.

## Default-budget software comparison

Six runs used `--samples 100 --period-ms 20 --deadline-ms 10`. Three ran with
no deliberate guest load. Two busy loops ran concurrently for all three
loaded attempts. [Trace verification](../../tools/verify_qnx_logs.py)
checked trace structure before the quantiles were calculated.

| Condition | Completed runs | Inputs / outputs | Misses | Per-run observations |
| --- | ---: | ---: | ---: | --- |
| No deliberate load | 3/3 | 300 / 300 | 0 | Max 8.348, 1.712 and 4.627 ms. Median 0.765, 0.718 and 0.725 ms. |
| Two guest busy loops | 0/3 | 3 / 0 | 3 | First decision took 19.131, 14.187 and 17.045 ms. All runs stopped and exited 3. |

These observations combine guest execution, emulation and host scheduling.
The timing root cause remains unresolved. QSC 1.7.0 was installed locally
on macOS ARM64. The documented requirements for supported reproduction are
a supported x86-64 host and QSC 2.0.4 or later.

## Separate gated transport fixture

The follow-up fixture run used the same QEMU IFS and disk with separately
built test executables from `d979bec3ea4b7c6d1865825cf2e492c0839a2333`.
All 12 attempts passed at 112.5 ms. All 12 default 7.5 ms attempts were
inconclusive (exit 77). The [fixture protocol](../qnx-ipc-fixture.md)
defines the wait-state observations and per-attempt cleanup gates. The
longer-budget result does not establish default-budget behavior.

| Fixture artifact | SHA-256 |
| --- | --- |
| `qnx-ipc-tests` | `9171099ea08956e47fd1a1b996af5b64f462aa11bffbbc2f737f051c5ff4db81` |
| `qnx-ipc-receiver` | `f8daa57711708b6a53ebfe3fbd728d8ab7645637d06fd95d73440d672e242ebb` |
| `cleanup-evidence.tgz` | `d0d0be8ce02536451139e56aac08f83c7bfb4e4cd63480c53a278da1bfb8f884` |

The local archive and invocation manifest are in
`artifacts/qnx-ipc-fixture-cleanup-20261126/` in the qualification checkout.
