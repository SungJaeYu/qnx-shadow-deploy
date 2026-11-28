# Issue 5 qualification oracle replay

All runs below used a 20 ms period, a 10 ms decision deadline and a 7.5 ms
IPC budget. The lifecycle checker counts promotion credits independently of
elapsed sample count. The receipt checker tests the timestamp of the read by
a separate process on the same guest monotonic clock.

## Four matched x86-64 runs

The QNX 8 x86-64 QEMU TCG guest used two vCPUs and 1 GiB of RAM. The
unchanged Release broker and workers came from `53cf1fa`. Each run held
384 MiB with a priority-5 memory holder through broker exit. The candidate
used the 16 MiB guest profile. A polled the log for the terminal summary.
B polled the broker PID after commit, then checked the summary once.
The four runs used the same binaries, receiver and 50 ms polling interval.

| Run | Outputs received | Commit sample | Acquisition to receipt p99 / max (ms) |
| --- | ---: | ---: | ---: |
| A1 | 2,000 | 438 | 1.327 / 2.529 |
| B2 | 2,000 | 444 | 1.461 / 6.274 |
| B3 | 2,000 | 434 | 1.477 / 2.755 |
| A4 | 2,000 | 452 | 1.529 / 5.740 |

Each run cancelled 20 candidates, committed generation 21, and retired the
recorded workers. The receiver observed consecutive outputs within their
advertised deadlines. There were zero skipped slots, decision misses or
telemetry drops. These historical runs lack a separate absolute clock anchor.
Receipt latencies in the table use acquisition times inferred from the
advertised deadlines.

B2 reproduced a verifier defect before the two polling treatments diverged.
Replay ended after sample 383. At sample 399 the broker selected the
baseline while a candidate reply was pending. The later valid reply
preserved the qualification count and earned zero credit. The broker
therefore accumulated 20 credits over 21 shadow samples, promoted on 405
and committed on 444. The old verifier expected promotion on 404. The
corrected verifier accepts all four and still rejects an early promotion.
These runs do not establish a polling-related cause for previous misses.

## Known failures retained

| Archived QNX run | Requested / acquired / selected / received | Observation |
| --- | --- | --- |
| Diagnostic ring, sample 105 | 10,000 / 105 / 105 / 105 | Decision took 2.075 ms. The receiver read the last output 7.808 ms beyond its advertised deadline. The broker also recorded a late write return. |
| Diagnostic ring, sample 118 | 10,000 / 118 / 117 / 117 | The 10.703 ms baseline-only decision withheld output. Broker REPLY and READY intervals on the same CPU accounted for 5.059 and 4.836 ms. |
| Clock capture, sample 8,494 | 10,000 / 8,494 / 8,493 / 8,493 | Output 8,492 arrived 0.717 ms late. The terminal decision at 8,494 withheld output. |
| Stalled capture | 10,000 / 3,668 / 3,667 / 3,667 | All accepted writes reached the receiver. One output was withheld and 6,332 inputs were unprocessed. The telemetry capture has gaps. |

All four workloads still fail. A fail-closed decision, a late receiver
read and an unprocessed tail require different diagnoses. The guest
trace at 118 identifies the time spent blocked and runnable. It does
not resolve how much CPU time the host granted the guest during that interval.

## AArch64 comparison

The QNX 8.0.6 AArch64 `virt` guest booted on QEMU TCG with two vCPUs
and 1 GiB RAM. The broker added only the terminal monotonic clock origin
to the timed workload's telemetry. The input was a prerecorded, deterministic
millimetre trace. A separate FIFO reader timestamped each complete record.
The idle smoke received 640/640 on time, cancelled 20 generations,
committed on sample 422 and retired all workers. Initial empty slots,
skipped slots and decision misses were zero. The maximum acquisition
interval was 28.153 ms and the maximum acquisition-to-receipt latency
was 4.624 ms. The independently anchored receipt and lifecycle checks passed.

Under a priority-5 holder with 384 MiB touched, the same AArch64 workload
requested 10,000 samples. Generation 21 committed after 20 cancellations.
At sample 1,684 the broker selected the candidate at 3.000 ms. The receiver
read the record at 5.790 ms, with 4.210 ms of margin. The broker observed
the successful write return after 10 ms and stopped with exit 4. All
1,684 writes reached the receiver on time, with zero skipped slots,
decision misses or dropped telemetry rows. The other 8,316 inputs were
unprocessed. The independent receipt verdict passed and the workload
verdict failed. The write-completion observation alone cannot locate the
delay inside or after the write syscall. The broker's documented fail-stop
rule remains in effect.

See the [verifier commands](../cancel-retry-cycles.md) for the two verdicts.
Use the sensor-trace flag on the AArch64 fixture. Physical Pi 4B timing,
live VL53L0X acquisition and the repeated load matrix remain open.
