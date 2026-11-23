# Verification record

## Scope

Initial host verification was performed on 2026-11-23 using macOS, AppleClang 21.0.0 and CMake 4.3.3. The runtime reported `host-posix-pipes`. A subsequent QNX cross-build produced all five target executables. **QEMU boot, native IPC execution and hardware timing measurements remain pending.**

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
| QNX cross-build: five executables, warnings treated as errors | Passed; inspected owner-generated artifacts and CMake/Ninja metadata |
| QNX guest image generation | Passed; boot image, raw disk, VMDK descriptor and buildfile inspected |
| QNX guest boot and target tests | Pending; host QEMU not installed |

The process suite checks output source identity, strictly increasing sample IDs, rejection before authority, rollback to the current baseline sample, failure with no valid fallback, post-commit failure and child reaping. Policy tests additionally cover exact deadline boundaries, malformed/late outputs, continuity across switching and rollback, invalid configuration and terminal deadline failure. Test checks remain active in release builds.

The repository configures sanitizer CI on Ubuntu 24.04 and macOS 15. Per-commit results are available in [Actions](https://github.com/SungJaeYu/qnx-shadow-deploy/actions). Host success and cross-compilation do not verify native QNX runtime behavior.

## QNX cross-build checkpoint

On 2026-11-23 the owner ran `qnx_container.sh image` and `qnx_container.sh build` from the Mac terminal. Docker engine access is unavailable to the automated session. The resulting files, CMake configuration and completed Ninja link records were inspected afterward; the compiler terminal transcript was not captured.

- Source revision: `429b05d`; Ubuntu 24.04 `linux/amd64` container on Apple Silicon.
- Compiler: QCC 12.2.0, `gcc_ntox86_64`, CMake 3.28.3, Debug, C++17, `-Werror`.
- Outputs: `shadow-demo`, `shadow-worker`, `shadow-candidate`, `policy-tests`, `staging-tests`.
- All five are x86-64 ELF PIE executables using `/usr/lib/ldqnx-64.so.2`.
- Broker/staging binaries import `ConnectAttach`, `MsgSend`, `TimerTimeout` and `posix_spawn`; worker/candidate binaries import `ChannelCreate`, `MsgReceive` and `MsgReply`.
- The generated manifest declares `os=qnx` and `architecture=x86_64`.

The [machine-readable record](evidence/qnx-cross-build.json) includes SHA-256 hashes, sizes and runtime dependencies. The SDK was installed after selecting the 8.0.6 patch set; complete package alignment still needs auditing. Dynamic imports demonstrate that the QNX code path was compiled, not that its runtime behavior passed.

## QNX image-generation checkpoint

The owner subsequently ran the container `image` and `vm-image` actions from revision `cc10aa2`. The generated build timestamp is `2026-11-23T08:45:05+00:00`. Inspection found a nonempty IFS, a 278,589,952-byte raw disk, and a VMDK descriptor whose 544,121 sectors exactly match the raw file size. The generated system buildfile lists all five project executables, their candidate manifest and the required C++ runtime. Image hashes and partition sizes are recorded in [qnx-image-build.json](evidence/qnx-image-build.json). The image has not been mounted or booted to inspect its installed files.

Image generation succeeded. Host `qemu-system-x86_64` is not installed, so boot and all target tests remain pending. The native Mac launch helper uses explicit TCG software emulation and an ephemeral disk snapshot, with no guest network. No Docker engine access is needed to launch this helper once host QEMU is installed. The helper's shell syntax, preflight and printed arguments were checked; the emulator command has not run.

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

1. Audit the installed SDK package set and guest version; native cross-compilation and image generation are complete.
2. Run policy and process scenarios on a QNX guest, including timeout/unblock semantics and cleanup.
3. Measure target timing under a declared scheduling/resource configuration and controlled load.
4. Separate selected-output delivery from telemetry and shadow waits; add target resource admission and validate repeated deployments under load.
5. Validate the final target demonstration and record its precise build/configuration.

See [QNX setup](qnx-setup.md) and [the roadmap](roadmap.md). No competition submission or target verification is implied by the host CI badge or screenshot.
