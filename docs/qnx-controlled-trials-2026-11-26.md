# QNX controlled timing on QEMU, 2026-11-26

Both collections used three attempts of 100 requested samples per
condition, a 20 ms period and a 10 ms decision deadline. CPU attempts added
two busy loops inside the QNX guest. Idle attempts added no deliberate
guest load. The baseline-only broker had no candidate worker.

| Broker mode | Condition | Passing attempts | Observed samples | Outputs / requested samples | Decision misses |
| --- | --- | ---: | ---: | ---: | ---: |
| Baseline-only | Idle | 2/3 | 209 | 208/300 | 1 |
| Baseline-only | Two busy loops | 0/3 | 3 | 0/300 | 3 |
| Candidate-enabled rollback | Idle | 3/3 | 300 | 300/300 | 0 |
| Candidate-enabled rollback | Two busy loops | 0/3 | 3 | 0/300 | 3 |

In the baseline-only collection, the first idle attempt exited 3 at
sequence 9 after eight outputs. Its maximum observed decision latency
was 31.452 ms. The two completed idle attempts had observed maxima of
3.803 and 4.584 ms. The three CPU attempts exited 3 on sequence 1 with
decision latencies of 17.850, 15.783 and 15.105 ms. The original collection
completed all six attempts, captured exit status and found no owned PIDs
after the attempts. The idle denominator includes 92 missing outputs
(one observed sample without output and 91 unobserved slots following
the stop). The CPU denominator includes 300 missing outputs (three
observed samples without output and 297 unobserved slots).

The earlier candidate-enabled rollback collection had all 300 idle
outputs and three loaded first-sample decision misses. Both collections
showed misses with two busy loops. The repeated misses with a candidate
absent show that a candidate is unnecessary for that failure pattern in
this environment. These attempts did not isolate effects of the guest,
host scheduler, QEMU TCG or evidence filesystem and do not establish
timing on Pi 4 hardware or worst-case execution time.

## Provenance

- Host: Darwin 25.6.0 ARM64. QEMU 11.1.3 x86-64 TCG, 2 vCPUs,
  1024 MiB, snapshot mode, guest network disabled.
- Guest: QNX `shadowdeploy-qnx 8.0.0 2026/10/30-09:10:05EDT x86pc x86_64`.
  IFS SHA-256 `568094ce7d41d5ac58e2db5a3b65fcae4faa4f2cc718b61ba9a13a57ee699e78`.
  Disk SHA-256 `59855de8b29f450d32954275825a76fbb7e84b7b62a873e9b2c66d7019fcd49c`.
- The guest binaries were reused from the image. Workspace HEAD was
  `9f30c4936823515cd368b835329a076b2513580d` and the exact binary-to-source
  mapping remains unknown. Before and after the baseline-only collection,
  broker POSIX `cksum` was `3374818193 1446928` and worker `cksum`
  was `1199416101 157272`.
- The original baseline-only archive was exported as
  `artifacts/native-cancel-20261126/controlled-trials.tgz`, with matching
  guest and host POSIX `cksum` `1958255913 18378`. It predates the stricter
  cleanup sidecar and provenance format. The candidate-enabled traces
  are in the separate `native-evidence.tgz` export from the same run series.
