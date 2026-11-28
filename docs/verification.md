# Verification record

## Scope

Initial host verification was performed on 2026-11-23 using macOS, AppleClang 21.0.0 and CMake 4.3.3. A subsequent QNX cross-build produced all five target executables. **The existing image later booted and native functional tests passed in QEMU using `qnx-native-ipc`. The complete installed package inventory matches the cached 8.0.6 definition, with inspected guest components aligned. Default 10 ms timing failures, supported-host installation reproduction and hardware timing validation remain open.**

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
| QNX guest boot, runtime libraries and writable storage | Passed on native macOS QEMU 11.1.3 / TCG / 2 CPUs / 1 GiB |
| QNX policy and staging suites | Passed; 23 policy cases and manifest/replay-overflow checks |
| QNX eleven functional scenarios | Passed at explicit 100 ms period / 75 ms budget |
| QNX live attachment, cancellation/retry and repeated replay rejection | Passed; baseline PID continuity and connection cleanup checked |
| QNX default 20/10 ms scenarios | Five early deadline failures retained; not a timing pass |
| Native timeout probes | Four completed with expected rejection; REPLY state captured; isolated SEND still open |
| Fresh host rerun in restricted session | Four suites passed; HTTP setup blocked by socket permission before any HTTP test |

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

The [machine-readable record](evidence/qnx-cross-build.json) includes SHA-256 hashes, sizes and runtime dependencies. The SDK was installed after selecting the 8.0.6 patch set; the subsequent [release audit](evidence/qnx-release-audit-2026-11-23/README.md) matches all 260 installed package versions to its cached definition. Dynamic imports demonstrate that the QNX code path was compiled, not that its runtime behavior passed.

## QNX image-generation checkpoint

The owner subsequently ran the container `image` and `vm-image` actions from revision `cc10aa2`. The generated build timestamp is `2026-11-23T08:45:05+00:00`. Inspection found a nonempty IFS, a 278,589,952-byte raw disk, and a VMDK descriptor whose 544,121 sectors exactly match the raw file size. The generated system buildfile lists all five project executables, their candidate manifest and the required C++ runtime. Image hashes and partition sizes are recorded in [qnx-image-build.json](evidence/qnx-image-build.json). The image has not been mounted or booted to inspect its installed files.

At the image-generation checkpoint, host `qemu-system-x86_64` was not installed and the launch command had not run. The subsequent native execution below used the same image hashes and the existing launcher with explicit TCG emulation, temporary disk writes and no guest network.

## Native QNX execution, 2026-11-23

The source checkout was clean at `af72fba664ec813e071572ac80495bf0e70c6edb`. The restricted session could read that checkout but could not write its Git configuration, so scoped documentation, evidence and validation tools were prepared in `/Users/yuseungjae/Desktop/qnx-shadow-deploy-validation`. The original checkout, five cross-built binaries and three image files were preserved. Runtime C++ code and all deadlines/assertions remained unchanged; neither cross-build nor image regeneration was needed.

The owner installed Homebrew QEMU after the automated installation failed to resolve `formulae.brew.sh`. Docker socket access was checked and denied; native QEMU operated independently. The [environment record](evidence/qnx-validation-2026-11-23/environment.json) records exact arguments, executable hash, compiler metadata, package inventory and runtime build IDs. All [original target traces and diagnostics](evidence/qnx-validation-2026-11-23/README.md) were exported before VM shutdown and archive checksums matched on the host.

| Configuration | Observed value |
| --- | --- |
| Host | macOS 26.6.2, build 25G83, arm64 |
| Emulator | Homebrew QEMU 11.1.3; native Mac TCG; `-cpu max`, 2 CPUs, 1024 MiB |
| I/O | Serial console, `-snapshot`, `-nic none`, no host directory sharing |
| Guest | `QNX shadowdeploy-qnx 8.0.0 2026/10/30-09:10:05EDT x86pc x86_64` |
| Guest privileges/scheduling | Development root account; observed broker/worker priority `10r`; no explicit scheduling/resource isolation |
| Storage | `/system` and `/data` QNX6 mounts; `/tmp -> /data/var/tmp`; both temporary locations writable |
| Compiler evidence | Existing QCC 12.2.0, `gcc_ntox86_64`, Debug, C++17, `-Werror`; compiler transcript not rerun |
| SDK integrity scope | 260 package records; nine selected assets match package size/SHA-512 metadata |
| Runtime matching | libc, loader, libm, libgcc_s and libc++ build IDs match installed SDK package records |

`qversion` and `uname` report 8.0.0. The later [release audit](evidence/qnx-release-audit-2026-11-23/README.md) separately matches all 260 installed IDs/versions to the cached 8.0.6 package definition. A fresh guest scan matches 164 distinct build IDs and the kernel package/version to that SDK. The boot kernel exposes no GNU build ID through `use -i`; no byte-for-byte guest kernel match is claimed. No missing, extra or mismatched package was found. QSC 1.7 and macOS/ARM remain outside the current 8.0.6 installation requirements. The no-network launcher caused expected `vtnet0` startup errors; the guest reached `Startup complete`. Boot also reported absent ACPI MCFG and PCID support. Host load measurement with `ps` was denied, so these runs used an uncontrolled host workload.

Policy and staging executables returned 0 on QNX. The eleven scenarios were first run at their unchanged 20 ms period / 10 ms decision budget / 7.5 ms IPC cutoff, with 180 requested samples and default qualification/probation. Five stopped early due to timing:

| Default run | Stopped sequence | Reason |
| --- | --- | --- |
| `reject-delay` | 23 | `decision_deadline_exceeded` |
| `reject-stale` | 89 | `baseline_deadline_exceeded` |
| `reject-protocol` | 14 | `decision_deadline_exceeded` |
| `reject-switch` | 49 | `decision_deadline_exceeded` |
| `no-fallback` | 21 | `decision_deadline_exceeded`, before the intended fault |

The first four had already rejected their candidates. For example, the `reject-delay` baseline completed at 29.694 ms and its decision took 30.306 ms against the unchanged 10 ms budget. These are observed deadline failures, not evidence that the rejection logic passed the entire run. The precise cause of scheduling interference was not isolated. No runtime fix or threshold change is claimed to resolve them; controlled scheduling/load and active-path isolation remain work. Original `default-*.jsonl` files are retained without rewriting.

Separate functional runs reused the repository's existing host scenario parameters: 22 samples, qualification 3, probation 8, period 100 ms, decision budget 75 ms and IPC cutoff 56.25 ms. All eleven reached the expected phase and exit code. Promotion occurred at sequence 4; rollback at sequence 8 used that sequence's valid baseline result. `baseline-failure`, `no-fallback` and `post-commit-failure` correctly stopped output and returned 3. The others returned 0. The delayed candidate returned a timeout observation after 57.284 ms, before its 250 ms injected reply; the 57.770 ms decision still published the current baseline within the configured 75 ms budget. The timed-out candidate was retired and no subsequent sample reused it.

Four live runs each produced 100 inputs and 100 selected outputs with no missing outputs:

| Run | Baseline PID | Promotion / commit | Result |
| --- | --- | --- | --- |
| Default 20/10 ms late attachment | 991250 | 44 / 83 | Committed; max observed decision 4.226 ms |
| Functional 200/150 ms late attachment | 1302546 | 42 / 81 | Committed |
| Cancel during probation, then retry | 1437716 | Generation 2 committed | Current baseline selected on cancellation; unchanged PID |
| Three delayed and three stale replay rejections, then retry | 3080213 | Generation 7: 55 / 69 | Only the seventh candidate gained authority |

All live runs required fresh qualification after replay. `pidin fds` showed two worker side-channel connections during probation, returned to the exact initial descriptor/connection set after cancellation and every replay rejection, and showed only the committed candidate connection afterward. Final process lists contained no broker/worker/candidate. This demonstrates cleanup and no observed stale-result reuse in these runs; it is not an exhaustive kernel interleaving or leak-stress proof.

Additional timeout probes used a declared 1000 ms period, 150 ms decision budget and 112.5 ms IPC cutoff. A stopped receiver and a 250 ms delayed reply both caused rejection at sequence 2 with baseline output retained. Repeated polling captured broker dispatch threads in `REPLY` at priority `10r`. The initial stopped-receiver attempt was named `timeout-send`, but its single snapshot missed the wait; it is retained as inconclusive SEND evidence. A receiver already waiting in `MsgReceive` can accept a message before its stopped thread runs. The follow-up observed `REPLY`, so a distinct SEND-blocked timeout remains unverified. Neither failed snapshot capture nor default-budget failure was discarded.

The host verifier checks all 30 JSONL traces for native backend identity, sequence continuity, publication validity, counters and baseline PID continuity; it additionally asserts the functional outcomes, replay generations, timeout retirement and connection/process snapshots. The native image was stopped only after all logs were exported. The original image and executable SHA-256 values still match their earlier checkpoints.

```sh
python3 tools/verify_qnx_logs.py docs/evidence/qnx-validation-2026-11-23/shadow-validation
```

The fresh host Debug build and policy, staging, process and live-deployment suites passed. `workbench-api` failed during setup because the sandbox denied binding `127.0.0.1`; zero HTTP tests executed. The complete [CTest transcript](evidence/qnx-validation-2026-11-23/host-ctest.log) is preserved. Earlier host/API/sanitizer results above remain historical results, not a claim that this restricted rerun passed every suite.

## Pre-board QNX campaign, 2026-11-26

The [campaign record](evidence/qnx-preboard-campaign-2026-11-26.md)
documents fresh stock lifecycle and controlled-load runs, functional and
default-budget IPC fixture outcomes, and stock and test-only operator
cancellation. It includes failed and inconclusive attempts, configuration
and artifact hashes. The [English lifecycle video](qnx-lifecycle-demo.mp4)
is based on the separate [recorded same-broker run](evidence/live-lifecycle-2026-11-26.md),
including its skipped nominal slot and strict verifier failure.

## QNX guest timing and admission, 2026-11-27 to 2026-11-28

The [timing record](qnx-guest-timing-2026-11-27.md) captures the matched
stock Release comparison, release-grid and admission checks, and the
separate instrumented host scheduler analysis. It records stopped runs
at the unchanged 20 ms period, 10 ms decision budget and 7.5 ms IPC
cutoff alongside completed runs.

## Earlier host scheduling failures

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

## Workbench process timeout

The local workbench server allows the configured sample count times the configured period plus 15 seconds for broker startup, staging and output overhead. The watchdog terminates the owned process group when that duration expires. This wall-clock watchdog is separate from the broker's per-sample decision and IPC budgets. The HTTP integration check verifies that the configured run reaches the client with its declared decision budget. [Issue #11](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/11)

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

1. Reproduce the installation with QSC 2.0.4+ on a supported x86-64 development host and capture fresh compiler output. Cached release membership and inspected guest provenance passed; independent catalog signature validation and a whole-filesystem integrity audit were not performed.
2. Observe the SEND/REPLY timeout and cleanup matrix at the default IPC budget and broaden cancellation/unblock interleavings near the deadline.
3. Resolve remaining default-budget timing failures under the declared workloads and validate timing independently on the physical target.
4. Validate the dedicated output receiver and repeated deployments at target load. Set hardware resource budgets and resolve producer telemetry and retirement isolation.
5. Verify live sensor input, GPIO indication, and Ethernet relay on Pi 4B. Retain the board trace and build configuration.

See [QNX setup](qnx-setup.md) and [the roadmap](roadmap.md). No competition submission or target verification is implied by the host CI badge or screenshot.
