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

The SDK's release notes do not support native macOS or ARM development hosts. QNX also publishes a macOS workflow that runs the Linux x86-64 host tools inside an emulated Docker container on Apple Silicon. That is the first cross-build route for this project on an M1 Mac; a supported x86-64 Ubuntu host remains the fallback. The blog workflow does not establish SDP 8.0.6 compatibility or successful target execution. A Docker container does not supply a QNX kernel.

**Status: SDK installed; QNX compilation and execution pending.** QNX Software Center has installed the SDK and Linux x86-64 host tools on the development Mac. These are target setup instructions and acceptance gates, not a transcript of successful target validation.

## Obtain and activate the SDK

Use [QNX Everywhere](https://qnx.software/en/developers/get-started/qnx-everywhere) and QNX Software Center to obtain and activate an eligible license. In myQNX License Manager, deploy the license to your own myQNX email address; a newly issued license marked **Available** is not yet deployed. Then activate it locally in QNX Software Center. Install the complete SDP 8.0.6 baseline and matching target components. Do not mix partial SDK/OS update sets. Review and accept applicable terms yourself; never paste serial numbers or passwords into repository issues, scripts or chat.

Do not commit SDK headers, binaries, activation files, account credentials or generated QNX images. The repository includes only project-owned source and build instructions.

## Apple Silicon Mac: container cross-build

1. Install and start Docker Desktop for Apple Silicon.
2. Install [QNX Software Center for macOS](https://www.qnx.com/download/feature.html?programid=58068). The QNX macOS guide links the archived QSC 1.7 installer; its version is separate from the desired SDP 8.0.6 version. If the old client cannot install the required packages, use the current Linux QSC on a supported Ubuntu host instead of installing an older SDP silently.
3. Sign in to QSC, activate the deployed license, and add an SDP 8.0 installation, normally at `~/qnx800`. Select the complete 8.0.6 update set. Under **Manage Installation > Available**, install **QNX SDP Host Tools (Linux 64-bit)**.
4. From the repository directory in your Mac terminal:

```sh
bash tools/qnx_container.sh image
bash tools/qnx_container.sh build
```

If the SDK is elsewhere, set `SHADOW_QNX_SDP=/absolute/path/to/sdk` before the build command. If license storage is customized, set `SHADOW_QNX_LICENSE` to the actual **license directory**, not its parent. The script uses `~/.qnx/license` by default and does not read or print its contents.

The image contains only Ubuntu packages. It mounts this repository, the SDK read-only, and the license directory writable for QNX licensing state. It does not mount the entire home directory or Docker socket, enable privileged mode, publish ports, or use host networking. It runs as the invoking user's UID/GID. Review `tools/qnx_container.sh` before execution. The license and SDK never enter the Docker build context or image layers.

Output goes to `build-qnx-container/`, separate from native host builds. `bash tools/qnx_container.sh shell` opens a diagnostic shell with the same mounts. This helper does not start a QNX VM, activate a license, or run QNX binaries on Linux. Emulator timing is not hardware timing evidence.

**Setup checkpoint, 2026-11-23:** QSC was installed and the SDK installation was completed after selecting the 8.0.6 patch set. The SDK exists at `~/qnx800`; `qnxsdp-env.sh`, the target tree, and Linux x86-64 `qcc`/`q++` are present. The installed Linux GCC package is `12.2.0.01008T202610011452L`. QSC displayed a successful local license activation confirmation after an application restart. The macOS QSC command-line status query aborted, so it did not establish license status. Docker engine access remains blocked by the automated session sandbox: run the two container commands above from the owner's terminal. The container image, compiler license checkout, complete release matching and guest execution remain unverified. Presence of SDK files and QSC activation do not establish a successful cross-build.

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

- [QNX macOS development guide](https://qnx.software/en/blog/2026/how-to-use-macos-to-develop-for-qnx)
- [QNX Docker macOS reference project](https://gitlab.com/qnx/projects/docker-mac)
- [QSC license storage](https://www.qnx.com/developers/docs/qsc/com.qnx.doc.qsc.user_guide/topic/configure_default_location.html)
- [QNX SDP 8.0.6 release notes and host requirements](https://qnx.com/developers/docs/relnotes8.0/com.qnx.doc.release_notes/topic/8.0.6_prod_release_notes/8.0.6_rn.html)
- [CMake cross-compilation for QNX](https://cmake.org/cmake/help/latest/manual/cmake-toolchains.7.html#cross-compiling-for-qnx)
- [mkqnximage](https://qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.utilities/topic/m/mkqnximage.html)
- [QNX Everywhere QEMU quickstart](https://qnx.com/developers/docs/qnxeverywhere/com.qnx.doc.target_images/topic/qsti_qemu/getting_started.html)
