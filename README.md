# QNX ShadowDeploy

**Validate an application candidate on the same input stream before giving it output authority.**

ShadowDeploy is an application-level deployment runtime for QNX 8.0. It runs baseline and candidate sensor-processing applications in separate processes, checks output contracts and response timing, switches at an input sequence boundary, and retains a warm fallback during a bounded probation window.

![ShadowDeploy workbench showing a measured host promotion and rollback](docs/workbench.jpg)

## Current status

This repository contains a working **host-verifiable MVP**, a QNX native IPC implementation, and a QNX cross-build configuration. **The QNX path has not yet been compiled or executed with an installed QNX SDP.** Target validation is the next release gate, not a completed result.

| Capability | Status |
| --- | --- |
| Separate baseline and candidate processes | Implemented and tested on the host |
| Same-input comparison and contract validation | Implemented and tested |
| Sequence-boundary promotion and warm fallback | Implemented and tested |
| Unit, delay, NaN, stale-response, protocol and crash injection | Implemented and tested |
| English workbench and JSONL evidence | Implemented and tested |
| QNX `MsgSend` / `MsgReceive` / `MsgReply` and `TimerTimeout` | Implemented; target build and execution pending |
| Remote artifact upload, arbitrary app adapters, live candidate attachment | Planned |
| Target scheduling, resource limits and hardware timing evidence | Pending |

## The problem

An application can start successfully and still return a wrong unit, stale observation, discontinuous position, or late result. A deployment decision needs evidence about its behavior, not just process liveness.

The example application estimates a moving object's position and velocity from a synthetic sensor stream. Both versions receive the same sequence and acquisition timestamp. Only the broker selects output. The candidate's output remains shadow-only until qualification passes.

The baseline and candidate are example algorithm variants in the same worker executable, launched as **different OS processes**. This first version starts both together; it does not yet upload a package to an already-running service. It is a deployment policy and process-runtime prototype, not a complete OTA platform.

## Run on Linux or macOS

Requirements: a C++17 compiler, CMake 3.20+, Python 3.9+, and POSIX process APIs. No Python packages or JavaScript dependencies are required.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
python3 tools/serve.py
```

Open <http://127.0.0.1:8765>, select a scenario, and click **Run demonstration**. Each run launches the compiled C++ broker and two real child processes. The dashboard plots their results; it does not invent a successful deployment in JavaScript.

The server also accepts `--samples`, `--period-ms`, and `--deadline-ms` for explicitly configured host experiments. The chosen values are included in every recording and displayed in the workbench; changing them does not establish target performance.

For command-line execution:

```sh
./build/shadow-demo --worker ./build/shadow-worker --scenario rollback
./build/shadow-demo --worker ./build/shadow-worker --scenario reject-units
./build/shadow-demo --worker ./build/shadow-worker --scenario reject-delay
./build/shadow-demo --worker ./build/shadow-worker --scenario promote
```

JSON Lines go to stdout and diagnostics go to stderr. Save stdout to a `.jsonl` file and use **Open a recording** in the workbench to inspect it. The backend field distinguishes `host-posix-pipes` from `qnx-native-ipc`; an imported recording remains labeled **Recorded**.

Run with `--help` for configuration and all eleven scenarios. Exit codes: `0` for a completed run, `2` for setup/argument errors, `3` for stopped output, and `130` for an interrupted run. Candidate rejection is a successful policy outcome and returns `0`.

## What the runtime checks

- Protocol signature, version, kind, sequence and acquisition timestamp.
- Finite position and velocity; configured range and step limits.
- Bounded IPC exchange and acquisition-to-decision timing.
- Output continuity when switching to the candidate **or back to the baseline**.
- Current valid fallback output during probation; otherwise output is stopped.

Output equality is not required: an improved algorithm can produce different valid results. The example contract is deliberately simple and does not prove estimation accuracy or application safety.

Default demonstration settings are a 20 ms nominal period, a 10 ms decision budget, 20 qualification samples, and 40 probation samples. IPC receives 75% of the decision budget to leave time for validation. These are configurable experiment settings, **not measured worst-case guarantees**.

## QNX target

The intended target is **QNX SDP / OS 8.0.6, x86-64, QEMU**. The QNX build selects native message passing at compile time. A host build selects bounded POSIX pipes while using the same policy and example workloads.

Follow [QNX setup and validation](docs/qnx-setup.md). A licensed SDK and a supported development host are required. No QNX binaries, headers, license files or VM images are distributed in this repository.

## Project map

| Location | Responsibility |
| --- | --- |
| `include/shadow/protocol.hpp` | Local worker protocol and fixed layouts |
| `src/policy.cpp` | Qualification, promotion, probation, rejection and fallback |
| `src/process.cpp` | Process ownership, bounded transport and persistent dispatch threads |
| `src/worker.cpp` | Stateful example algorithms and failure injection |
| `src/main.cpp` | Synthetic input, single output owner, scheduling and evidence |
| `web/`, `tools/serve.py` | Local workbench and recording inspection |
| `tests/` | Policy boundaries, real-process integration and HTTP behavior |
| `cmake/qnx8-x86_64.cmake` | QNX cross-compilation configuration |

## Documentation

- [Architecture and behavior contract](docs/architecture.md)
- [QNX setup and validation](docs/qnx-setup.md)
- [Verification and remaining gates](docs/verification.md)
- [Demonstration script](docs/demo.md)
- [Implementation roadmap](docs/roadmap.md)

## Scope and limitations

The prototype supports cooperative workers using its protocol and a read-only sensor-processing example. It does not isolate hostile software, migrate arbitrary state, update an OS image, control an actuator, survive broker failure, or reverse external side effects. The baseline is retired after commit, so later candidate failure stops output rather than claiming an unavailable rollback. Target scheduling and CPU/memory isolation remain to be validated.

Development was assisted by OpenAI Codex. Design decisions, source, tests, and unverified assumptions are available for review. This project makes no certification claim and is not affiliated with or endorsed by QNX.

## License

[MIT](LICENSE). QNX is a trademark of its respective owner; SDK use remains subject to QNX's terms.
