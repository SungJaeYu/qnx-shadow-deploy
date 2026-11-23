# QNX setup and target validation

## Target configuration

| Item | Intended configuration |
| --- | --- |
| SDK and target OS | QNX SDP / OS 8.0.6 |
| Development host | x86-64 Ubuntu Desktop 22.04 or 24.04 |
| Target | x86-64 QEMU guest |
| Compiler | SDK `q++`, `gcc_ntox86_64` variant |
| Build | CMake 3.20+, C++17 |
| Transport | Native QNX message passing |

The SDK's release notes do not support macOS or ARM development hosts. A Mac can edit code and display the host workbench; the intended QNX cross-build and guest execution path is a supported x86-64 Linux environment. A Docker container does not supply a QNX kernel.

**Status: not yet executed.** These are target setup instructions and acceptance gates, not a transcript of successful target validation. The initial development environment had no discoverable `q++`, `mkqnximage`, `QNX_HOST`, or `QNX_TARGET`.

## Obtain and activate the SDK

Use [QNX Everywhere](https://qnx.software/en/developers/get-started/qnx-everywhere) and QNX Software Center to obtain and activate an eligible license. Install the complete SDP 8.0.6 baseline and matching target components. Do not mix partial SDK/OS update sets. SDK download, license acceptance and account activation are performed by the account owner.

Do not commit SDK headers, binaries, activation files, account credentials or generated QNX images. The repository includes only project-owned source and build instructions.

## Cross-build

From a supported Linux host, replace `/path/to/qnx806` with the actual installation directory:

```sh
source /path/to/qnx806/qnxsdp-env.sh
sh tools/check_qnx_env.sh
q++ -V
cmake -S . -B build-qnx \
  -DCMAKE_TOOLCHAIN_FILE=cmake/qnx8-x86_64.cmake \
  -DCMAKE_BUILD_TYPE=Debug
cmake --build build-qnx --parallel 2
```

Confirm that `q++ -V` lists the configured compiler variant. The environment preflight only checks tool discovery. A successful compiler invocation is still required to verify the installation and license.

The build produces `shadow-demo`, `shadow-worker`, `shadow-candidate`, `candidate.manifest`, `policy-tests` and `staging-tests`. Cross builds do not run these binaries on the host and do not register the host-only Python integration tests.

## Create and run a guest

Install and configure QEMU using the official QNX instructions for the selected host. For a generated command-line image, start in a separate directory:

```sh
mkdir -p build-qnx-vm
cd build-qnx-vm
mkqnximage --type=qemu --arch=x86_64
mkqnximage --run
```

The image generator operates on its current directory. Consult `mkqnximage --help=brief` for SDK-specific networking and image options. Alternatively, use the QNX Everywhere QEMU quickstart image; confirm its OS release and runtime libraries match the selected SDP before testing.

Provide the project's target binaries and generated manifest and required C++ runtime libraries to the guest using the image's documented transfer mechanism. Guest address, login and transfer configuration are environment-specific; no credentials are assumed by this repository.

## Run on the target

Place the binaries and generated manifest in one executable directory. The staging tests require a writable `/tmp`. Then run:

```sh
./policy-tests
./staging-tests
./shadow-demo --worker ./shadow-worker --candidate-manifest ./candidate.manifest --stage-at 20 > deploy-qnx.jsonl
./shadow-demo --worker ./shadow-worker --scenario promote > promote-qnx.jsonl
./shadow-demo --worker ./shadow-worker --scenario reject-units > reject-units-qnx.jsonl
./shadow-demo --worker ./shadow-worker --scenario reject-delay > reject-delay-qnx.jsonl
./shadow-demo --worker ./shadow-worker --scenario rollback > rollback-qnx.jsonl
```

Also run `baseline-failure`, `no-fallback`, and `post-commit-failure`; those intentionally stop output and return exit code 3. Run the remaining rejection scenarios listed by `--help`.

Check that the first JSON record says `"backend":"qnx-native-ipc"`. A hardened image may require explicit process/channel abilities in its security policy. Configure the intended permissions rather than treating privileged execution as the production design.

Transfer logs back to the host and inspect them through **Open a recording**. Host plots of target logs are recorded target evidence, not live host execution.

## Required evidence before declaring QNX support verified

- [ ] Record the SDK package manifest, compiler output, guest OS version and QEMU configuration.
- [ ] Compile every target with warnings treated as errors.
- [ ] Run target policy/staging tests, all eleven process scenarios and late artifact attachment.
- [ ] Exercise cancel/retry, replay failure and backlog overflow while recording the unchanged baseline PID.
- [ ] Verify timeout return, child-exit behavior, connection cleanup and no stale reply reuse.
- [ ] Verify sample continuity across promotion and rollback; report missing outputs honestly.
- [ ] Measure decision and release timing under declared CPU/memory load with chosen priorities.
- [ ] Save original JSONL logs and a video showing target execution and workbench evidence.

## QNX features used by this project

| QNX mechanism | Project use | Verification state |
| --- | --- | --- |
| `ChannelCreate`, `ConnectAttach`, `MsgSend`, `MsgReceive`, `MsgReply` | Broker-to-worker request/reply with matching input sequence and a single output owner | Native source implemented; SDK build and target execution pending |
| `TimerTimeout` with monotonic absolute SEND/REPLY limits | Bound IPC blocking when a worker stalls or fails to reply | Native source implemented; kernel timeout behavior unverified |
| Separate process address spaces and `posix_spawn` | Keep candidate filter state separate; terminate/reap an owned failed candidate | Host behavior tested; QNX behavior pending |
| Message priority inheritance and real-time thread scheduling | Intended basis for chosen broker/worker priorities under load | No explicit priority configuration or target evidence yet |

The current implementation does not use a resource manager, health-monitoring pulses, HAM, adaptive partitioning or QNX Hypervisor. Those are not claimed as implemented features. Process separation is not CPU isolation; timeout return can still be delayed by scheduling and timer granularity. QNX priorities, active-path separation and controlled-load measurements remain necessary before making a timing guarantee.

## Official references

- [QNX SDP 8.0.6 release notes and host requirements](https://qnx.com/developers/docs/relnotes8.0/com.qnx.doc.release_notes/topic/8.0.6_prod_release_notes/8.0.6_rn.html)
- [CMake cross-compilation for QNX](https://cmake.org/cmake/help/latest/manual/cmake-toolchains.7.html#cross-compiling-for-qnx)
- [mkqnximage](https://qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.utilities/topic/m/mkqnximage.html)
- [QNX Everywhere QEMU quickstart](https://qnx.com/developers/docs/qnxeverywhere/com.qnx.doc.target_images/topic/qsti_qemu/getting_started.html)
