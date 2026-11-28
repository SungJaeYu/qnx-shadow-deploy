# QNX setup and target validation

For the physical AArch64 target, see the [Raspberry Pi 4B qualification plan](qnx-pi4-qualification.md).

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

**Status: the existing image booted and native functional tests passed on 2026-11-23.** QNX Software Center installed the SDK and Linux x86-64 host tools on the development Mac. [Cross-build evidence](evidence/qnx-cross-build.json), [image evidence](evidence/qnx-image-build.json) and [native execution evidence](evidence/qnx-validation-2026-11-23/README.md) distinguish these stages. The [release audit](evidence/qnx-release-audit-2026-11-23/README.md) confirms package membership against the cached 8.0.6 definition and matches inspected guest components. Default-budget timing failures and supported-host installation reproduction remain open.

## Candidate admission profile for the QEMU guest

Use `--qnx-guest-profile` on the QNX 8.0.6 x86-64 QEMU image configured with
2 vCPUs and 1 GiB RAM. For example, from the installed artifact directory:

```sh
./shadow-demo --qnx-guest-profile --worker ./shadow-worker \
  --candidate-manifest ./candidate.manifest --stage-at 50 \
  --qualify 80 --probation 60 --samples 240 --period-ms 20 --deadline-ms 10
```

Before spawning a candidate, the broker reads a single bounded snapshot of
[QNX system VM counters](https://qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.sys_arch/topic/vmstat.html).
Both physical free pages (`pages_free`) and unreserved allocation pages
(`vmem_avail`) must cover 32 MiB at the guest's page size. The threshold budgets
16 MiB for the candidate and 16 MiB of spare capacity for the incumbent
adapter. Low headroom and invalid or unavailable counters each refuse
admission. In a startup scenario the broker exits before emitting metadata.
During live staging, the staging thread performs the check. The broker
reports `preparation_failed` and the running baseline retains authority.

The broker spawns its own executable in an internal launcher mode. The
owned child caps both `RLIMIT_AS` limits at 16 MiB, preserving any tighter
inherited limit, and denies and locks the ability to raise them across
exec in both credential domains. It then replaces itself with the
candidate executable. The child PID, worker handshake and QNX native
channel belong to the same process. The broker and baseline keep their
regular launch settings. The cap follows the candidate into promotion
and commit. The admission gate holds one candidate slot through staging,
qualification and abort cleanup. After a failed launch or handshake, the
stager reaps the owned child before releasing the slot.

The stock candidate measured 822 mapped and resident 4 KiB pages (about
3.21 MiB), and a launch control with the locked 16 MiB limit completed the
240-output stock promotion run. The allocation control used 400,000 entries with 128-character
values. It completed uncapped and failed under the locked cap. A privileged
launch control verified rejection of an attempt to raise the hard limit.
These external launcher controls preceded the broker-owned launch path.
See [the guest timing record](qnx-guest-timing-2026-11-27.md) for the
separate stock and fault-injection results.

The profile is for one trusted, non-forking candidate adapter. The ceiling
is per-process virtual address space. Physical memory remains
shared with the broker, baseline, kernel and other processes. The profile
uses a point-in-time admission decision. Other allocations can change
headroom between the snapshot and candidate execution, and the check creates
no physical memory reservation. On the supported guest the operator controls
background work and keeps it below authority-capable work. The measured
configuration used two CPU-bound round-robin workers at priority 5 and observed
the broker and authority-capable workers at priority 10 round-robin. Equal
priority 10 background load is outside this profile. The broker does not
reconfigure external processes or change the sample period or deadline.

Use the metadata field `candidate_launch_profile` to identify the enabled
policy. For a guest check, observe the candidate PID through staging and
promotion with `pidin`, check scheduling priorities under managed load, and
compare the capped stock run with the uncapped control. A fixture that cannot
allocate under the cap should exit before its worker handshake. The broker
should report a preparation failure while baseline output continues. Once the
failed process is reaped, retry with the stock artifact and check that it
commits. The CLI rejects the flag on non-QNX-x86-64 builds before launching
workers.

## Obtain and activate the SDK

Use [QNX Everywhere](https://qnx.software/en/developers/get-started/qnx-everywhere) and QNX Software Center to obtain and activate an eligible license. In myQNX License Manager, deploy the license to your own myQNX email address; a newly issued license marked **Available** is not yet deployed. Then activate it locally in QNX Software Center. For a supported 8.0.6 installation, use QNX Software Center 2.0.4 or later on a supported x86-64 host and create a new baseline with the Conservative update policy, as specified by the [release notes](https://developers.qnx.com/developers/docs/relnotes8.0/com.qnx.doc.release_notes/topic/8.0.6_prod_release_notes/8.0.6_rn.html). Existing 8.0.x installations cannot currently be upgraded to 8.0.6 using a patch set. Install the complete baseline and matching target components. Do not mix partial SDK/OS update sets. Review and accept applicable terms yourself; never paste serial numbers or passwords into repository issues, scripts or chat.

Do not commit SDK headers, binaries, activation files, account credentials or generated QNX images. The repository includes only project-owned source and build instructions.

## Apple Silicon Mac: observed container cross-build

The steps below record how the existing artifacts were produced. The archived Mac QSC client is below the 8.0.6 minimum version; this is an experimental reproduction path, not the supported installation procedure above.

1. Install and start Docker Desktop for Apple Silicon.
2. The recorded installation used the archived [QNX Software Center for macOS](https://www.qnx.com/download/feature.html?programid=58068), version 1.7.0. Use the supported installation procedure above for a new development environment.
3. The owner activated the license locally, selected the 8.0.6 set in QSC, and installed the SDK plus **QNX SDP Host Tools (Linux 64-bit)** at `~/qnx800`. The later audit verifies the resulting package inventory; it does not certify the old client's installation workflow.
4. From the repository directory in your Mac terminal:

```sh
bash tools/qnx_container.sh image
bash tools/qnx_container.sh build
```

If the SDK is elsewhere, set `SHADOW_QNX_SDP=/absolute/path/to/sdk` before the build command. If license storage is customized, set `SHADOW_QNX_LICENSE` to the actual **license directory**, not its parent. The script uses `~/.qnx/license` by default and does not read or print its contents.

The image contains only Ubuntu packages. It mounts this repository, the SDK read-only, and the license directory writable for QNX licensing state. It does not mount the entire home directory or Docker socket, enable privileged mode, publish ports, or use host networking. It runs as the invoking user's UID/GID. Review `tools/qnx_container.sh` before execution. The license and SDK never enter the Docker build context or image layers.

Output goes to `build-qnx-container/`, separate from native host builds. `bash tools/qnx_container.sh shell` opens a diagnostic shell with the same mounts. This helper does not start a QNX VM, activate a license, or run QNX binaries on Linux. Emulator timing is not hardware timing evidence.

**Setup checkpoint, 2026-11-23:** QSC installed the SDK after selecting the 8.0.6 patch set and displayed successful local license activation. The installed Linux GCC package is `12.2.0.01008T202610011452L`. The owner built the Ubuntu container and cross-compiled all five executables from the Mac terminal. Docker engine access was unavailable in the 2026-11-23 automated session. The later native run reused these artifacts. The [SDK audit](evidence/qnx-sdk-audit.json) inventories 260 packages and verifies nine selected installed assets against package size/SHA-512 metadata. The follow-up [release audit](evidence/qnx-release-audit-2026-11-23/README.md) matches all 260 IDs/versions to `com.qnx.qnx800.patchset806/8.0.6.00398T202611040724L`, with no missing, extra or mismatched package. The guest kernel package and 164 distinct guest build IDs also match. This uses cached vendor metadata and does not certify a supported installation or every filesystem byte.

**Setup checkpoint, 2026-11-26:** The [pre-board campaign](evidence/qnx-preboard-campaign-2026-11-26.md)
used the local SDK and Docker container to rebuild x86-64 guest artifacts
and validate them under QEMU. The supported x86-64 QSC installation and
the Pi 4B target remain separate gates.

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

The build produces `shadow-demo`, `shadow-worker`, `shadow-candidate`, `shadowctl`, `candidate.manifest`, `policy-tests` and `staging-tests`. Cross builds do not run these binaries on the host and do not register the host-only Python integration tests.

## Create and run a guest

For the container workflow, package the existing cross-build into a local image:

```sh
# Refresh the host image to include ssh-keygen for guest host keys.
bash tools/qnx_container.sh image
bash tools/qnx_container.sh vm-image
```

This action uses the installed SDK's `mkqnximage` with x86-64 QEMU, 2 CPUs and 1 GiB RAM. It writes to ignored `build-qnx-vm/` and adds `/system/bin/shadowdeploy/` containing the six executables, `candidate.manifest` and the local deployment hook example. It explicitly requires `libc++.so.2`, which is also present in the generated standard library list. The standard image supplies libc, libm and libgcc_s. The helper owns `local/snippets/system_files.shadowdeploy`; it preserves other local snippets. Regenerating the image rebuilds its partitions, so export guest logs first.

**Image generation and subsequent boot passed on 2026-11-23.** The outputs are `build-qnx-vm/output/ifs.bin`, a 278,589,952-byte raw `disk-qemu`, and `disk-qemu.vmdk`. The small VMDK file is a descriptor referencing the raw disk, not a standalone disk image. The [image evidence](evidence/qnx-image-build.json) records hashes and the generated partition layout. The native run reused these exact images. Generated images contain development accounts and must not be exposed on a public interface.

On the Mac, install host QEMU once and then boot with the project helper:

```sh
brew install qemu
bash tools/run_qnx_vm.sh
```

The helper uses TCG software emulation directly on the Mac, without Docker, KVM, guest networking or host directory sharing. It uses temporary disk writes (`-snapshot`), preserving the generated disk. Save guest results over the serial console before exiting: `/data` changes disappear when QEMU exits. `bash tools/run_qnx_vm.sh --print-command` shows the exact launch command. Press Ctrl-A then X to quit. The default guest startup prints network errors while waiting for its absent `vtnet0` adapter; wait for `Startup complete`. QEMU 11.1.3 booted this configuration successfully. The owner installed QEMU in their terminal after the automated Homebrew attempt failed DNS resolution.

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

See [the local deployment hook](deployment-hook.md) for `shadowctl` setup and the guest functional example.

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

For the recorded functional suite, each scenario used `--samples 22 --qualify 3 --probation 8 --period-ms 100 --deadline-ms 75`. Live functional runs used `--samples 100 --period-ms 200 --deadline-ms 150`. These explicit settings already existed in the host functional tests; they do not replace the interactive 20/10 ms defaults. Run the defaults first and preserve failures separately.

The repository also provides `tools/qnx_lifecycle_checks.sh` and `tools/qnx_timeout_checks.sh`. Transfer these text scripts to the guest and invoke them with `sh`; image regeneration is unnecessary. They write to `/data/shadow-validation`, refuse existing trace names, and capture native connection/thread snapshots. The timeout probe uses a 1000 ms period, 150 ms decision budget and 112.5 ms IPC cutoff to observe a stopped receiver and a delayed reply. Do not infer SEND blocking merely from stopping a receiver: it may already be waiting in `MsgReceive`.

The single-broker crash and retry check uses the same driver on the host and on QNX. On the guest, transfer `tools/lifecycle_checks.sh` alongside the three matching project executables and generated manifest. Use a new absolute output directory:

```sh
sh /data/shadowdeploy/lifecycle_checks.sh /data/shadowdeploy /data/lifecycle-new
```

Export that directory and run `python3 tools/verify_lifecycle.py PATH_TO_EXPORTED_DIRECTORY` on the host. It checks the live qualification counts, current-sample rollback, worker IDs, broker connections and process retirement. Its standalone timing gate checks the declared 200/150 ms run for skipped slots and decision misses. Retain the run directory and stderr even if the driver or verifier exits unsuccessfully. The host `live-deployment` CTest uses the same driver and reports nominal-grid timing alongside its functional verdict.

For a timed run under load, transfer `tools/lifecycle_loaded_checks.sh` alongside the same binaries and manifest. Start the configured workload before invoking the driver and retain its workload evidence with the exported run. Use a fresh directory for each attempt.

```sh
sh /data/shadowdeploy/lifecycle_loaded_checks.sh /data/shadowdeploy /data/lifecycle-loaded
```

```sh
python3 tools/verify_lifecycle.py --loaded PATH_TO_EXPORTED_DIRECTORY
```

The broker starts the first stage after sample 20 and writes its telemetry directly to a regular file. The first candidate uses the stock crash mode at sample 38. A separate observer scans the file and requests a healthy second stage after the first worker disconnects and baseline authority resumes. The verifier requires the crash to occur in probation, fresh qualification for each generation, 90 selected outputs at the original 200/150 ms configuration, and distinct worker identities that have retired by broker exit. It separately gates skipped nominal slots and deadline misses. Use `lifecycle_checks.sh` in a separate run to audit native process and descriptor snapshots. The loaded run performs no `pidin` snapshots during acquisition.

For protocol v2 kernel conformance, build and transfer the QNX-only `qnx-reply-conformance` executable and run it on the guest. It uses an owned loopback channel to check the status, copied bytes, untouched suffix and production decoder verdict for each test reply.

Capture the serial session on the Mac, for example with `script -q serial-original.log bash tools/run_qnx_vm.sh`. Export only the project log directory before quitting. The recorded run created a tar archive, printed its `cksum`, sent it as base64 over serial, and verified the decoded host archive's checksum and length. Preserve JSONL bytes and stderr separately; do not scrape only successful summaries. After extraction, run `python3 tools/verify_qnx_logs.py PATH_TO_EXPORTED_LOG_DIRECTORY`. The [evidence README](evidence/qnx-validation-2026-11-23/README.md) records the exact export checksums and limitations.

## Required evidence before declaring QNX support verified

- [x] Record package inventory, existing compiler metadata, guest OS version and exact QEMU configuration.
- [x] Match all installed package versions to the cached 8.0.6 release definition and inspect guest provenance.
- [ ] Reproduce installation with QSC 2.0.4+ on a supported x86-64 host and capture a fresh compiler transcript.
- [x] Compile every target with warnings treated as errors.
- [x] Run target policy/staging tests, all eleven functional scenarios and late artifact attachment.
- [x] Exercise cancel/retry, replay failure and backlog overflow; record baseline PID continuity in live runs.
- [x] Verify observed native timeout return, child exit, connection cleanup and rejection of stale observations across retries.
- [x] Observe SEND- and REPLY-blocked timeout behavior in the gated QEMU fixture at 112.5 ms.
- [ ] Observe the complete 7.5 ms default-budget fixture. The QEMU attempts at that budget were inconclusive.
- [x] Verify sample continuity across promotion and rollback; preserve default-budget missing outputs.
- [ ] Measure decision and release timing under declared CPU/memory load with chosen priorities.
- [x] Retain QEMU guest logs and check in the recorded English lifecycle recap.
- [ ] Save original Pi 4B logs and compare them with workbench evidence.

See the [pre-board campaign record](evidence/qnx-preboard-campaign-2026-11-26.md)
for fresh runs, source and image hashes, and timing limits.

## QNX features used by this project

| QNX mechanism | Project use | Verification state |
| --- | --- | --- |
| `ChannelCreate`, `ConnectAttach`, `MsgSend`, `MsgReceive`, `MsgReply` | Broker-to-worker request/reply with matching input sequence and a single output owner | Native functional scenarios, connection snapshots and repeated deployment passed |
| `TimerTimeout` with monotonic absolute SEND/REPLY limits | Bound IPC blocking when a worker stalls or fails to reply | Gated SEND and REPLY timeouts observed in QEMU at 112.5 ms. Default-budget and board gates remain. |
| Separate process address spaces and `posix_spawn` | Keep candidate filter state separate; terminate/reap an owned failed candidate | Host and QNX lifecycle checks passed; no owned child remained after exit |
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
