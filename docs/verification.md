# Verification record

## Scope

Initial local verification was performed on 2026-11-23 using macOS, AppleClang 21.0.0 and CMake 4.3.3. The runtime reported `host-posix-pipes`. **No QNX SDK build, QEMU boot, native IPC test or hardware timing measurement has been completed.**

## Executed checks

| Check | Result |
| --- | --- |
| C++17 debug build, warnings treated as errors | Passed |
| Policy suite: 23 named boundary/state/scheduling cases | Passed |
| Process integration: 11 scenarios plus argument/startup checks | Passed |
| Staging: manifest admission and bounded replay overflow | Passed |
| Live deployment: separate artifact, replay, cancellation/retry, stalled startup and PID continuity | Passed on the host; see timing limitations below |
| Workbench HTTP: static assets, origin/path checks, real run evidence | Passed |
| AddressSanitizer + UndefinedBehaviorSanitizer build and five CTest suites | Passed |
| JavaScript syntax and Python compilation | Passed |
| Browser: late artifact attachment, replay, promotion and JSONL download | Passed; 100 inputs / 100 outputs in the captured run |
| Source, documentation and interface language | English |
| QNX preflight | Blocked: SDK tools and environment unavailable |

The process suite checks output source identity, strictly increasing sample IDs, rejection before authority, rollback to the current baseline sample, failure with no valid fallback, post-commit failure and child reaping. Policy tests additionally cover exact deadline boundaries, malformed/late outputs, continuity across switching and rollback, invalid configuration and terminal deadline failure. Test checks remain active in release builds.

The repository configures sanitizer CI on Ubuntu 24.04 and macOS 15. Per-commit results are available in [Actions](https://github.com/SungJaeYu/qnx-shadow-deploy/actions). Host success does not verify the native QNX branch.

One earlier macOS run ended the HTTP scenario in `failed` rather than `rejected`. That first assertion did not preserve the decision reason, so its exact cause was not established; a diagnostic-only follow-up passed. The test now saves JSONL evidence and includes decision details in failures.

A later macOS [CI run](https://github.com/SungJaeYu/qnx-shadow-deploy/actions/runs/45119687685) exposed a real scheduling bug: after a late wake-up, acquisitions at 0.329732 s and 0.330208 s were only 0.476 ms apart. Chasing the old release grid caused a catch-up burst and an invalid velocity estimate. The scheduler now bases the next release on the actual acquisition time and never queues missed inputs. Deterministic regression tests cover late wake-up and processing overrun; process tests also enforce minimum acquisition spacing.

That run separately recorded a 27.545 ms decision against a 25 ms budget during a deliberately delayed candidate exchange. The runtime correctly stopped output instead of publishing late. Functional process/API suites now explicitly use a 100 ms period and a 75 ms budget on shared sanitizer runners. The injected worker delay remains 250 ms, while policy tests check exact deadline boundaries without wall-clock dependence. The HTTP suite runs 12 samples; process scenarios run 22. The interactive defaults remain 20/10 ms. These test settings do not establish target timing robustness.

## Live-deployment timing observations

During development on the non-real-time macOS host, a 100 ms period / 75 ms budget run stopped at sequence 19 with a 92.530 ms decision latency. A subsequent sanitizer run stopped at sequence 29 after commit with an 86.802 ms decision latency; its candidate exchange completed at 86.182 ms and release lateness was 36.226 ms. The precise source of host scheduling interference was not established. Both runs failed closed as required and are not counted as successful timing evidence. The failed normal run's last records were preserved in CTest diagnostics; the sanitizer run's [complete failed JSONL](evidence/host-deployment-deadline-failure.jsonl) was retained before further testing. It can be imported in the workbench and ends in `failed`.

Live-deployment **functional** integration tests now declare a 200 ms period / 150 ms decision budget (112.5 ms IPC cutoff). This follows the observed host delays and separates lifecycle checks from target timing claims. The deliberate worker stall is still 250 ms and must time out. Exact acceptance/deadline boundaries remain in deterministic policy tests. The original scenario/API tests use 100/75 ms. Interactive defaults remain 20/10 ms. The CLI accepts `0 < deadline-ms < period-ms <= 1000` for explicit experiments.

Startup, replay and cancellation cleanup run in the background. Live shadow exchanges, selected-output computation and stdout telemetry still share scheduling dependencies; this milestone does not establish active-path or CPU isolation. No runtime budget was silently reinterpreted, and a late result still stops output.

## Captured host example

Original trace, regenerated after the acquisition-spacing fix: [host-rollback.jsonl](evidence/host-rollback.jsonl).

```sh
./build/shadow-demo --worker ./build/shadow-worker --scenario rollback \
  > docs/evidence/host-rollback.jsonl
```

| Item | Observed result |
| --- | --- |
| Backend | Host POSIX pipes |
| Inputs / selected outputs | 180 / 180 |
| Missing outputs / decision budget misses | 0 / 0 |
| Promotion | Sequence 21 |
| Candidate exit and rollback | Sequence 41 |
| Final phase | `rolled_back` |
| Median decision latency | 0.083 ms |
| 95th-percentile decision latency | 0.195 ms |
| Maximum observed decision latency | 1.072 ms |

Percentiles use sorted samples with zero-based index `floor((N-1)*p)`. Decision latency runs from synthetic acquisition to the broker's publication decision. It excludes JSON encoding, stdout delivery, browser rendering and actuator response. This is one short host run under an uncontrolled host workload; it is not a benchmark, WCET bound, QNX result or hardware guarantee.

## Captured late-deployment example

The [workbench screenshot](workbench.jpg) and [host-live-deployment.jsonl](evidence/host-live-deployment.jsonl) come from the same browser-driven run. The server was configured with `--samples 100 --period-ms 100 --deadline-ms 75`; the selected demonstration stages the generated candidate manifest after 20 inputs.

| Item | Observed result |
| --- | --- |
| Backend | Host POSIX pipes; schema 2 |
| Baseline PID / candidate PID | 52844 / 52847 |
| Stage requested | After sequence 20 |
| Replay caught up / first live qualification | Through sequence 21 / sequence 22 |
| Promotion / commit | Sequence 42 / sequence 81 |
| Inputs / selected outputs / missing outputs | 100 / 100 / 0 |
| Median / p95 / maximum decision latency | 0.091 / 0.170 / 28.562 ms |

The baseline process continued unchanged through preparation, qualification and probation, then retired at commit. This one successful host run does not erase the failed runs above or establish the default 10 ms budget as reliable.

## Reproduce verification

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure

cmake -S . -B build-sanitize -DCMAKE_BUILD_TYPE=Debug -DSHADOW_SANITIZERS=ON
cmake --build build-sanitize --parallel 2
ctest --test-dir build-sanitize --output-on-failure
```

Under heavy scheduling interference, a timing scenario may fail closed. Preserve the failed log and record host load before interpreting the result. Do not relax thresholds silently or turn a failed run into a success claim.

## Remaining validation gates

1. Install and activate QNX SDP 8.0.6 on a supported host; compile the native branch.
2. Run policy and process scenarios on a QNX guest, including timeout/unblock semantics and cleanup.
3. Measure target timing under a declared scheduling/resource configuration and controlled load.
4. Separate selected-output delivery from telemetry and shadow waits; add target resource admission and validate repeated deployments under load.
5. Validate the final target demonstration and record its precise build/configuration.

See [QNX setup](qnx-setup.md) and [the roadmap](roadmap.md). No competition submission or target verification is implied by the host CI badge or screenshot.
