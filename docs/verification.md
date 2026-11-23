# Verification record

## Scope

Initial local verification was performed on 2026-11-23 using macOS, AppleClang 21.0.0 and CMake 4.3.3. The runtime reported `host-posix-pipes`. **No QNX SDK build, QEMU boot, native IPC test or hardware timing measurement has been completed.**

## Executed checks

| Check | Result |
| --- | --- |
| C++17 debug build, warnings treated as errors | Passed |
| Policy suite: 16 named boundary/state cases | Passed |
| Process integration: 11 scenarios plus argument/startup checks | Passed |
| Workbench HTTP: static assets, origin/path checks, real run evidence | Passed |
| AddressSanitizer + UndefinedBehaviorSanitizer build and all three CTest suites | Passed |
| JavaScript syntax and Python compilation | Passed |
| Browser: live promotion/rollback and delayed-candidate rejection | Passed |
| Source, documentation and interface language | English |
| QNX preflight | Blocked: SDK tools and environment unavailable |

The process suite checks output source identity, strictly increasing sample IDs, rejection before authority, rollback to the current baseline sample, failure with no valid fallback, post-commit failure and child reaping. Policy tests additionally cover exact deadline boundaries, malformed/late outputs, continuity across switching and rollback, invalid configuration and terminal deadline failure. Test checks remain active in release builds.

The repository configures sanitizer CI on Ubuntu 24.04 and macOS 15. Both hosts passed run [45119491688](https://github.com/SungJaeYu/qnx-shadow-deploy/actions/runs/45119491688). Current status for later commits is available in the Actions tab.

One earlier macOS run ended the HTTP scenario in `failed` rather than `rejected`. That first assertion did not preserve the decision reason, so its exact cause was not established; a diagnostic-only follow-up passed. The test now saves JSONL evidence and includes decision details in failures. The HTTP suite explicitly uses a 100 ms period, a 75 ms decision budget and 12 samples to test request/stream behavior without asserting the interactive 10 ms setting on a shared sanitizer runner. Process integration uses 30/25 ms settings; policy tests check exact deadline boundaries without wall-clock dependence. The interactive defaults and captured 20/10 ms host evidence are unchanged. This test configuration change does not resolve or establish target timing robustness.

## Captured host example

Original trace: [host-rollback.jsonl](evidence/host-rollback.jsonl).

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
| Median decision latency | 0.086 ms |
| 95th-percentile decision latency | 0.353 ms |
| Maximum observed decision latency | 1.801 ms |

Percentiles use sorted samples with zero-based index `floor((N-1)*p)`. Decision latency runs from synthetic acquisition to the broker's publication decision. It excludes JSON encoding, stdout delivery, browser rendering and actuator response. This is one short host run under an uncontrolled host workload; it is not a benchmark, WCET bound, QNX result or hardware guarantee.

The [workbench screenshot](workbench.jpg) shows a separate browser-driven host run with the same promotion/rollback sequence. Its process IDs and timing samples differ from the captured JSONL trace.

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
4. Add separate artifact staging and late candidate attachment before describing the project as a complete update workflow.
5. Validate the final target demonstration and record its precise build/configuration.

See [QNX setup](qnx-setup.md) and [the roadmap](roadmap.md). No competition submission or target verification is implied by the host CI badge or screenshot.
