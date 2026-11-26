# QNX default-budget qualification follow-up

## CPU-load controls

Six idle controls and six loaded runs requested 100 baseline-only inputs per run.
The period was 20 ms, the decision budget 10 ms and the IPC allowance 7.5 ms.
Each pair used the same x86-64 QNX guest binaries. Trial maxima below are in
milliseconds. Every early stop reported decision_deadline_exceeded. The paired
idle results show run-to-run variation.

| Arm | Completed runs | Acquired / selected by trial | Maximum decision latency by trial |
| --- | ---: | --- | --- |
| Idle controls for one loop | 1/3 | 76/75, 88/87, 100/100 | 21.213, 11.532, 2.622 |
| One busy loop | 2/3 | 100/100, 39/38, 100/100 | 1.003, 13.878, 8.499 |
| Idle controls for two loops | 1/3 | 100/100, 94/93, 45/44 | 4.743, 13.919, 15.777 |
| Two busy loops | 0/3 | 1/0, 1/0, 1/0 | 14.559, 17.716, 14.183 |

Cleanup checks passed after every run. Guest artifact checksums (POSIX cksum)
were 6686849 for the 1,660,448-byte broker and 1706457004 for the 159,848-byte
worker. The control archive SHA-256 is
9c341a524d56f92e4963eb30dce1dd86f5d0059fec52bd4a09118b04aacdee78.

These runs used native QNX 8 x86-64 IPC under QEMU TCG, two vCPUs,
1 GiB of guest RAM, and no network. The IFS SHA-256 was
`dcfe3f9e0746064d249c1aab534adcfda4aa87c9cb63bc415d3c0bba2c2f225b`.
The snapshot base disk SHA-256 was
`51710f559810b5564a328769ad76636a928210bc6592689233d21101bd7b3e38`.
A candidate was absent in every trial. These observations establish that
candidate computation is unnecessary to reproduce this failure pattern.

## First-sample execution markers

A separate comparison at source revision `8633437` used 180 requested
inputs and the same period and cutoffs. A diagnostic broker marked its
first acquisition, dispatch, IPC and decision with x86 NOPs. A QEMU
plugin timestamped executed markers on the same host monotonic clock.
Two bounded arithmetic loads were alive when each loaded broker exited.

| Diagnostic arm | Acquired / selected in each run | First decision latency (ms) |
| --- | --- | --- |
| Stock broker, loaded | 1/0, 2/1, 1/0 | 19.355, 9.808, 15.825 |
| Marked broker with plugin disabled, loaded | 1/0, 1/0, 2/1 | 17.646, 17.614, 8.502 |
| Marked broker with plugin enabled, idle | 180/180 | Host bracket 2.238–2.254 |

The three-trial loaded plugin run dropped 47,759 and 50,640 marker
events on vCPUs 0 and 1. In its first trial the baseline exchange ended
7.537 ms after acquisition, past the IPC cutoff. The host bracket through
selection was 9.983–9.999 ms, and the broker withheld output. In its third
trial the guest recorded a 19.051 ms decision with incomplete host markers.
The two failure reasons and trace loss matter when interpreting this run.
Thread CPU clock resolution alone supplies no validated absolute error bound.

| Exported guest archive | SHA-256 |
| --- | --- |
| Stock, loaded | `159bf52c0b01b49e1fabe83850679e029063dfe1a2aa3e66a79467f34fa2f49b` |
| Marked, plugin disabled, loaded | `c62846c2ad2fb344c8c7bd32d154935deea97a157a40b92cc4db871ad4b0f4cd` |
| Marked, plugin enabled, loaded | `62e70b3bb318375b36d06113ab2cf15ad08b67c83719d6498845e035752fd07b` |
| Marked, plugin enabled, idle | `14cac740cf60d88dbafe7c2563e88d0a05469380bb48c4568b0eda8f44c93350` |

## Same-broker cancellation and retry

The [driver and independent verifier](../cancel-retry-cycles.md) require
20 canceled generations with a fresh live shadow sample, followed by
qualification, probation and commit for generation 21. They check baseline
PID continuity, selected source, worker retirement and summary counters.

| Platform | Period / deadline (ms) | Final commit sequence | Selected / requested | Skipped nominal slots | Decision misses |
| --- | --- | ---: | ---: | ---: | ---: |
| macOS host | 20 / 10 | 177 | 640/640 | 198 | 0 |
| macOS host | 200 / 150 | 141 | 640/640 | 24 | 0 |
| QNX x86-64 guest | 20 / 10 | 393 | 640/640 | 68 | 0 |
| QNX x86-64 guest | 200 / 150 | 151 | 640/640 | 8 | 0 |

Each run passed lifecycle verification and failed strict service cadence.
Five mutations of a copy of each fresh run were rejected. Earlier
anonymous-pipe controller attempts produced zero stage events. With a
supervised named FIFO, an earlier 320-sample run reached generation 16
in shadowing with 15 cancellations and 33 skipped slots. It exhausted
its inputs before the final generation. The 640-sample test retains
the same decision and IPC limits. No physical board participated.

The 640-input native archives have SHA-256 identities
`a1d0ef989ebb8c2992e86423e34d8871168be354b34c40ca4331b8417b236aed`
(default) and `af1de50be813001c01f10276122b90a2b1fc0dd635e3ce58c1cace2e204396ac` (wide).

## Dedicated output under telemetry pressure

QNX x86-64 Release at `9799d75`, 150 baseline-only inputs per condition,
20 ms period and 10 ms deadline. The rebuilt broker's source file had
SHA-256 `49c13a14b94176d7bf6fe66858673b5effa1d662082e78efdad0dedb4bf754cd`.
The binary's guest and host checksums matched.

| Telemetry consumer | Authority records received | Accepted write phases | Telemetry records dropped | Broker exit |
| --- | ---: | ---: | ---: | ---: |
| Drained | 150/150 | 150 | 0 | 0 |
| Undrained | 150/150 | 150 | 65 | 0 |

The authority reader observed consecutive sequences 1–150 in both runs.
Each recorded write interval surrounds kernel pipe acceptance. The consumer
receipt time is unmeasured. This comparison exercises an available authority
consumer. It does not exercise candidate admission or validate a hardware
latency bound. Guest export SHA-256:
`1bc11cda3d2dd6a048ce9aff9491766eef9974ad750853a269fd1e5661a1d29e`.
An earlier transfer exited 126 before the trials because the uploaded broker
lacked execute permission. The rerun verified file permissions and hashes.

## Sustained host control

One macOS host run at 20/10 ms with the explicit 10,000-input profile
canceled 20 generations, committed generation 21 at sample 194, selected
10,000/10,000 outputs and passed worker retirement. Maximum acquisition
interval was 34.661 ms, with 3,230 skipped nominal slots and zero decision
deadline misses. The functional verifier passed and `--require-timing`
failed. One host trial cannot complete the target load matrix. The broker
spaces acquisitions from their actual start; cumulative drift relative to
the nominal grid contributes to skipped slots even when a decision finishes
inside its per-sample deadline.
