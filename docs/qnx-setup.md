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

**Status: all five QNX executables cross-built and a guest image generated; guest execution pending.** QNX Software Center installed the SDK and Linux x86-64 host tools on the development Mac. [Cross-build evidence](evidence/qnx-cross-build.json) and [image evidence](evidence/qnx-image-build.json) record the generated artifacts. Boot and target tests remain acceptance gates.

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

**Setup checkpoint, 2026-11-23:** QSC installed the SDK after selecting the 8.0.6 patch set and displayed successful local license activation. The installed Linux GCC package is `12.2.0.01008T202610011452L`. The owner then built the Ubuntu container and cross-compiled all five executables from the Mac terminal. Their QNX ELF identity, native imports and CMake/Ninja metadata were inspected. Docker engine access remains blocked by the automated session sandbox. Complete SDK/guest release matching and native execution remain unverified; see the [verification record](verification.md#qnx-cross-build-checkpoint).

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

For the container workflow, package the existing cross-build into a local image:

```sh
# Refresh the host image to include ssh-keygen for guest host keys.
bash tools/qnx_container.sh image
bash tools/qnx_container.sh vm-image
```

This action uses the installed SDK's `mkqnximage` with x86-64 QEMU, 2 CPUs and 1 GiB RAM. It writes to ignored `build-qnx-vm/` and adds `/system/bin/shadowdeploy/` containing all five executables and `candidate.manifest`. It explicitly requires `libc++.so.2`, which is also present in the generated standard library list. The standard image supplies libc, libm and libgcc_s. The helper owns `local/snippets/system_files.shadowdeploy`; it preserves other local snippets. Regenerating the image rebuilds its partitions, so export guest logs first.

**Image generation passed on 2026-11-23.** The outputs are `build-qnx-vm/output/ifs.bin`, a 278,589,952-byte raw `disk-qemu`, and `disk-qemu.vmdk`. The small VMDK file is a descriptor referencing the raw disk, not a standalone disk image. The [image evidence](evidence/qnx-image-build.json) records hashes and the generated partition layout. Boot remains unverified. Generated images contain development accounts and must not be exposed on a public interface.

On the Mac, install host QEMU once and then boot with the project helper:

```sh
brew install qemu
bash tools/run_qnx_vm.sh
```

The helper uses TCG software emulation directly on the Mac, without Docker, KVM, guest networking or host directory sharing. It uses temporary disk writes (`-snapshot`), preserving the generated disk. Save guest results over the serial console before exiting: `/data` changes disappear when QEMU exits. `bash tools/run_qnx_vm.sh --print-command` shows the exact launch command. Press Ctrl-A then X to quit. The default guest startup may print network errors or pause while waiting for its absent network adapter; wait for startup completion before running tests. The launch command has been inspected, but has not yet been executed because host QEMU is not installed.

On a supported Linux host with QEMU configured according to the official instructions, an alternative manual image workflow is:

```sh
mkdir -p build-qnx-vm-manual
cd build-qnx-vm-manual
mkqnximage --type=qemu --arch=x86_64
mkqnximage --run
```

The image generator operates on its current directory. Consult `mkqnximage --help=brief` for SDK-specific networking and image options. Alternatively, use the QNX Everywhere QEMU quickstart image; confirm its OS release and runtime libraries match the selected SDP before testing.

For that manual image, provide the project's target binaries, generated manifest and required C++ runtime libraries using the image's documented transfer mechanism. Guest address, login and transfer configuration are environment-specific; no credentials are assumed by this repository.

## Run on the target

Place the binaries and generated manifest in one executable directory. For the container-generated image, use `cd /system/bin/shadowdeploy`. The staging tests require a writable `/tmp`. The system partition may be read-only; save logs in `/data` or another writable location. Then run:

```sh
./policy-tests
./staging-tests
./shadow-demo --worker ./shadow-worker --candidate-manifest ./candidate.manifest --stage-at 20 > /data/deploy-qnx.jsonl
./shadow-demo --worker ./shadow-worker --scenario promote > /data/promote-qnx.jsonl
./shadow-demo --worker ./shadow-worker --scenario reject-units > /data/reject-units-qnx.jsonl
./shadow-demo --worker ./shadow-worker --scenario reject-delay > /data/reject-delay-qnx.jsonl
./shadow-demo --worker ./shadow-worker --scenario rollback > /data/rollback-qnx.jsonl
```

Also run `baseline-failure`, `no-fallback`, and `post-commit-failure`; those intentionally stop output and return exit code 3. Run the remaining rejection scenarios listed by `--help`.

Check that the first JSON record says `"backend":"qnx-native-ipc"`. A hardened image may require explicit process/channel abilities in its security policy. Configure the intended permissions rather than treating privileged execution as the production design.

Transfer logs back to the host and inspect them through **Open a recording**. Host plots of target logs are recorded target evidence, not live host execution.

## Required evidence before declaring QNX support verified

- [ ] Record the SDK package manifest, compiler output, guest OS version and QEMU configuration.
- [x] Compile every target with warnings treated as errors.
- [ ] Run target policy/staging tests, all eleven process scenarios and late artifact attachment.
- [ ] Exercise cancel/retry, replay failure and backlog overflow while recording the unchanged baseline PID.
- [ ] Verify timeout return, child-exit behavior, connection cleanup and no stale reply reuse.
- [ ] Verify sample continuity across promotion and rollback; report missing outputs honestly.
- [ ] Measure decision and release timing under declared CPU/memory load with chosen priorities.
- [ ] Save original JSONL logs and a video showing target execution and workbench evidence.

## QNX features used by this project

| QNX mechanism | Project use | Verification state |
| --- | --- | --- |
| `ChannelCreate`, `ConnectAttach`, `MsgSend`, `MsgReceive`, `MsgReply` | Broker-to-worker request/reply with matching input sequence and a single output owner | Cross-built; native imports inspected; target execution pending |
| `TimerTimeout` with monotonic absolute SEND/REPLY limits | Bound IPC blocking when a worker stalls or fails to reply | Cross-built; kernel timeout behavior unverified |
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
- [QEMU installation](https://www.qemu.org/download/#macos)
- [QEMU command-line reference](https://www.qemu.org/docs/master/system/invocation.html)
