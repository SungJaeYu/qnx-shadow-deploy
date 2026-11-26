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

## Sustained QNX attempt with the existing image binaries

Using the same image binaries as the native 640-input comparison, one
10,000-input trial at 20/10 ms stopped at sample 92. It completed four
cancellations, reached generation 5 replay completion, and selected 91
consecutive outputs. Sample 92 had a candidate deadline failure and a
16.789 ms decision. The broker reported one acquired input without output,
9,908 unprocessed inputs, 12 skipped nominal slots and one decision miss.
Broker exit 3 and QEMU exit 0 distinguish the fail-closed application path
from a guest failure. The functional verifier rejected this run. No
promotion or commit occurred. Fresh trace SHA-256:
`ce2095ec56adf6183ca0658fd027f3fb5214cec28f8571954aa8efbebb8d2049`.
For the host 10,000-input control the trace SHA-256 was
`dc8474b9cfe3f0ffe159cf16600a72bac9a9b24752b655f881349445cc5c9faf`.

## Why zero decision misses can coexist with skipped releases

The first sample occupied nominal slot zero on both default-budget runs
below. Quantiles use nearest rank over actual acquisition intervals.
The selected-decision gap ends at the broker selection timestamp.

| Run | Intervals | Acquisition median / p99 / maximum (ms) | Decision p99 / maximum (ms) | Maximum selected-decision gap (ms) | Intervals of at least 40 ms |
| --- | ---: | --- | --- | ---: | ---: |
| QNX 640-input lifecycle | 639 | 21.874 / 27.229 / 28.121 | 1.496 / 5.636 | 28.859 | 0 |
| Host 10,000-input lifecycle | 9,999 | 27.296 / 30.049 / 34.661 | 0.435 / 6.371 | 34.905 | 0 |

Summing the interval excess over 20 ms gives 1,366.726 ms on QNX and
64,600.205 ms on the host. These totals explain exactly 68 and 3,230 empty
20 ms grid slots respectively after accounting for the residual offset
within the final slot. Every interval advanced either one or two slots.
Accumulated drift explains the counter without requiring any interval
to reach 40 ms. These timing records do not identify which scheduler,
timer, controller or application activity produced each excess.

Every next scheduled release equaled the preceding acquisition plus exactly
20 ms (639/639 guest intervals, 9,999/9,999 host intervals). All accumulated
excess therefore fell between the recorded release target and actual
acquisition. The records do not partition that interval between waking the
broker and work it performs before taking the acquisition timestamp.

## Final-source QNX sustained idle controls

Five independent fresh brokers each ran 10,000 live inputs at 20 ms period,
10 ms deadline and 7.5 ms IPC budget, with no injected load. They ran in one
fresh QNX 8 x86-64 QEMU TCG boot, so boot-to-boot variation was not sampled.
The runtime sources match `50589b7`, with `src/main.cpp` SHA-256
`754bd223d5191811fd899e5d424c3bae6f18798a374233a18cbc94be0e1869f8` and
`src/policy.cpp` SHA-256
`287523c267b1462ecc02bacad627fc68dc7391b671f895a7709536fbd9eee5a6`.
All runs passed functional verification: 20 cancellations, final promotion,
10,000 selected outputs and worker retirement. Each retirement record listed
the broker, 20 canceled candidates, baseline and final candidate; process-list
row counts were 20 before and after each run. The strict timing gate failed in
all five runs because of skipped nominal slots; decision deadline misses were
zero. Latency percentiles use nearest-rank over `decision_offset_ns -
acquired_offset_ns`. Selected-decision gaps use differences between consecutive decision timestamps.
The external delivery time was unmeasured on this stdout lifecycle path.

| Run | Commit sample | Decision latency min / median / p95 / p99 / max (ms) | Maximum selected-decision gap (ms) | Skipped slots | Decision misses |
| --- | ---: | --- | ---: | ---: | ---: |
| 1 | 449 | 0.153 / 0.273 / 0.610 / 0.948 / 2.848 | 34.496 | 1,434 | 0 |
| 2 | 474 | 0.162 / 0.256 / 0.588 / 0.891 / 3.980 | 38.379 | 1,444 | 0 |
| 3 | 449 | 0.161 / 0.253 / 0.570 / 0.899 / 2.126 | 30.261 | 1,448 | 0 |
| 4 | 446 | 0.157 / 0.251 / 0.563 / 0.815 / 3.741 | 30.410 | 1,457 | 0 |
| 5 | 437 | 0.160 / 0.256 / 0.582 / 0.850 / 3.680 | 30.234 | 1,438 | 0 |

The five fresh traces and compact per-run retirement/process summaries are
preserved in ignored `artifacts/qnx-final-10k-754bd223/`. The exported guest
archive, which also contains uploaded binaries, remains outside the repository;
its SHA-256 is
`e56e26a9bb81fad375bca841148187a7b9e9b8033c0ba6a0c63c0e45a44ebba0`.

## Baseline-first publication in dedicated mode

The QNX x86-64 Release build of `50589b7` used the dedicated output pipe,
with phase records on stderr. In the direct `reject-delay` scenario
(100 ms period, 50 ms decision deadline), the baseline supplied all six
consecutive authority records. At sequence 2 the write ended at
56,193,427,113 ns and candidate IPC ended at 56,240,838,006 ns. The baseline
pipe accepted output 47.411 ms before candidate IPC finished. The broker
omitted that sample from qualification, rejected the candidate on timeout
and continued baseline service (exit 0). In a separate healthy `promote`
run (100/75 ms), all 12 authority records arrived. The first two came from
the baseline; the candidate promoted at sequence 3 and committed at 4.

These are functional controls at the stated budgets. The host regression
also gated a candidate's reply on the consumer actually reading the
baseline record. Debug warnings-as-errors and ASan/UBSan each passed all
ten host CTest suites. A guest wrapper exited the persistent serial shell
after the behavior tests, losing its completion marker. A recovery boot
exported the completed cases and their exit codes from the same disk;
only regular files were extracted. Archive SHA-256:
`4b2f3f30ebf9703e8eb7db50ffd3d07d1a324b9bc6f0fd073247381c3a73bdcf`.

The same revision cross-compiled for `gcc_ntoaarch64le` with QCC 12.2.
The Pi artifact verifier accepted the seven executables it checks and the
QNX AArch64 manifest. The compiler driver warned about deprecated
`lang-c++` and an unnecessary `-pthread`. Physical execution remains
pending on the Raspberry Pi 4B.

## Two-loop kernel trace on the final runtime

With both arithmetic load processes alive before the logger, the baseline-only
Release broker stopped on its first decision at 11.766119 ms (10 ms deadline).
Baseline validation passed; no authority record was written. The decoded QNX
trace includes both load PIDs (409620 and 409622), broker PID 499735 and
worker PID 512024. Both load threads ran on the guest CPUs during the capture.
Their trace lifetime overlaps the broker. This capture lacks an application
marker synchronized to its first decision. Counts over a thread lifetime
cannot identify which thread ran during that particular 11.766 ms interval.
Logger, collector and wrapper exited zero and child cleanup passed. The
guest archive SHA-256 is
`2ee0f8e726a84c2ece63fc04793d1e1e5315eb9ac1a65c623673eda15bb779c7`.

An earlier 5-second capture included only the idle worker; the PID from
the loaded miss fell outside the recorded interval. That capture cannot
establish scheduler ownership during the loaded failure.

A fresh one-trial TCG marker buffer recorded zero drops on both vCPUs
with the two load workers alive. Its first decision passed at 9.968 ms
with a valid baseline and the second missed; the tracer instrumented only
the first sequence. This trial cannot assign the second miss to a host
or guest thread. Guest archive SHA-256:
`3672961a6875506687c38120129a7490cc731510db05fb97eab7b04bcae322a4`.

## Guest scheduler state during one missed decision

A [diagnostic-only patch](qnx-sequence-one-trace.patch) against `50589b7`
emitted QNX user events 720/721 around first acquisition, 722/723 around
baseline collection and 724/725 around selection. Two load loops were
alive before capture. Apply the patch to an isolated checkout before
rebuilding the x86-64 broker. Start tracelogger with USER and THREAD events
before releasing the broker. Use the marker sequence and broker PID together.
The regular Release build contains none of these trace calls.

The 20/10/7.5 ms baseline-only trial stopped at its first decision despite
a valid baseline. The broker measured 11.991589 ms. The inner/outer marker
bounds on the kernel trace clock (998,700,000 Hz) were 11.980575 and
12.019626 ms. The baseline exchange completed 6.195 ms after acquisition;
the broker returned from collecting its future at 11.382 ms. The raw trace
header and decoded trace agree on the clock frequency. Both CPUs had
contiguous trace buffer sequences and valid end cookies. In this specific
run, three nonoverlapping guest intervals
had the broker ready to run and awaiting dispatch:

| Broker thread and interval | READY to RUNNING (ms) | Guest load RUNNING-state overlap, CPU 0 / CPU 1 (ms) |
| --- | ---: | --- |
| Dispatch thread before the initial exchange | 3.535 | 3.500 / 3.355 |
| Dispatch thread after the baseline reply | 1.247 | 1.215 / 1.104 |
| Main thread after wakeup | 4.755 | 4.715 / 4.403 |

The three intervals account for 9.536 ms of the guest timeline. Overlap
uses the last recorded RUNNING thread on each guest CPU, clipped at a new
RUNNING thread or a state change for the original thread. CPU columns can
overlap in wall time. Interrupt handling can occur inside a RUNNING-state
interval. The trace establishes competition with runnable broker threads
at those points. It cannot assign host CPU residency, rule out concurrent
host descheduling or establish the cause of every earlier failure. The
added trace calls can change timing. Archive SHA-256:
`acff8ff847d3fbcdc404d6c05e6beb693a8e78e39e78a2eb66620e1efe13eb43`.
