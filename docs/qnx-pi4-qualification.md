# Raspberry Pi 4B 2 GB qualification

The official QNX SDP 8.0 BSP covers the Raspberry Pi 4 Model B with 2 GB RAM
and uses 64-bit AArch64. The artifacts below are project executables and a
candidate manifest. Build and provision the boot image separately with the
vendor's BSP instructions. Hardware qualification is pending.
[QNX Pi 4 BSP guide](https://www.qnx.com/developers/docs/BSP8.0/com.qnx.doc.bsp_raspberrypi.bcm2711.rpi4_8.0/topic/about.html),
[installation notes](https://www.qnx.com/developers/docs/BSP8.0/com.qnx.doc.bsp_raspberrypi.bcm2711.rpi4_8.0/topic/install_notes.html),
[BSP build](https://www.qnx.com/developers/docs/BSP8.0/com.qnx.doc.bsp_raspberrypi.bcm2711.rpi4_8.0/topic/common/bsp_build.html).

## Build and preflight on the host

Use a licensed SDP 8.0.6 installation with the AArch64 target components.
Record the installed SDP and BSP package IDs and versions, CMake/compiler
transcripts, Git revision, and source cleanliness. If the checkout is dirty,
retain the patch and its hash with the build record. Build with either the
existing x86-64 Linux tool container or a supported Linux development host.

```bash
# Existing container workflow. Save this transcript and require exit status 0.
set -o pipefail
bash tools/qnx_container.sh board-build 2>&1 | tee pi4-cross-build.log
```

```bash
# Supported Linux host, from the repository root.
source /path/to/qnx806/qnxsdp-env.sh
q++ -V
cmake -S . -B build-qnx-pi4 -G Ninja -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_TOOLCHAIN_FILE=cmake/qnx8-aarch64.cmake -DBUILD_TESTING=ON
cmake --build build-qnx-pi4 --parallel 2
```

Require `gcc_ntoaarch64le` in the compiler transcript. The verifier requires
each of the seven executables to have a little-endian ELF64 AArch64 header and
the QNX dynamic interpreter. It also checks candidate manifest identity.
Prepare a fresh project bundle only after this check succeeds:

```bash
(
  set -e
  mkdir -p artifacts
  mkdir artifacts/pi4
  python3 tools/verify_qnx_pi4_artifacts.py build-qnx-pi4 > artifacts/pi4/SHA256SUMS
  while read -r hash file; do
    cp -p "build-qnx-pi4/$file" artifacts/pi4/
  done < artifacts/pi4/SHA256SUMS
  cp tools/qnx_lifecycle_checks.sh tools/qnx_timeout_checks.sh \
    tools/qnx_controlled_trials.sh artifacts/pi4/
  (
    cd artifacts/pi4
    shasum -a 256 -c SHA256SUMS
    shasum -a 256 qnx_lifecycle_checks.sh qnx_timeout_checks.sh \
      qnx_controlled_trials.sh >> SHA256SUMS
  )
  tar -C artifacts -cf artifacts/pi4-project.tar pi4
  cksum artifacts/pi4-project.tar
)
```

Retain the archive checksum and byte length. Inspect the executable dependencies with the SDK
`ntoaarch64-readelf -l -d` or equivalent ELF utility. Provision the board
image with the matching loader at `/usr/lib/ldqnx-64.so.2` and every
`NEEDED` library, including the SDK's matching `libc++.so.2`. Confirm those
components against the actual BSP image. Retain vendor binaries and images
locally under their license.

## Provision and identify the board

On the physical Pi, follow the linked BSP guide to build or select the
matching image, prepare boot media and boot onto an isolated development
network or serial console. Record the Pi 4B model and revision, physical
2 GB configuration, usable RAM, storage, power supply, cooling, BSP release,
image SHA-256/build IDs, boot log and boot ID/time. Check the kernel/runtime
release using component build IDs and package provenance in addition to the
startup banner and `uname`.

Use the image's documented serial or SSH transfer route to copy
`pi4-project.tar` to `/data` or another writable executable filesystem.
Verify the board archive checksum and byte length before extraction. Set
`expected_archive` to the first two fields printed by the host's `cksum`
for this exact bundle. Use a filesystem supporting execution and FIFOs.
The policy/staging suites and IPC fixture need a writable temporary area.
Provide `pidin` and the shell utilities used below.

```sh
[ "$(uname -s)" = QNX ] || exit 2
case "$(uname -p)" in aarch64|aarch64le|AARCH64) ;; *) uname -a; exit 2 ;; esac
cd /data || exit 2
[ ! -e pi4 ] || exit 2
[ -n "${expected_archive:-}" ] || exit 2
actual_archive=$(cksum < pi4-project.tar) || exit 2
[ "$actual_archive" = "$expected_archive" ] || exit 2
tar xf pi4-project.tar || exit 2
```

The run setup verifies the extracted files when `sha256sum` is available.
If the board lacks it, copy the exact extracted files to a fresh directory
on the host and run `shasum -a 256 -c /absolute/path/to/host/SHA256SUMS`
from that returned copy. Use the original host bundle's checksums.
Set `verified_offline=yes` in the board shell only after every file in the manifest passes.

```sh
cd /data/pi4 || exit 2
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c SHA256SUMS || exit 2
else
    [ "${verified_offline:-}" = yes ] || exit 2
fi
bin=$(pwd -P) || exit 2
out="/data/shadow-pi4-$(date +%Y%m%d-%H%M%S)" || exit 2
mkdir "$out" || exit 2
uname -a > "$out/uname.txt" || exit 2
pidin info > "$out/pidin-info.txt" || exit 2
pidin ar > "$out/initial.processes" || exit 2
```

## Collect native functional and IPC evidence

Use the following shell function to capture each command's original stdout,
stderr and exit status. Keep the shell session log for the command sequence.
Keep each invocation in a fresh output directory. Record baseline PID
continuity from metadata and sample rows and use native process/connection
snapshots to verify cleanup.

```sh
capture() {
    stem=$1
    suffix=$2
    shift 2
    code=0
    "$@" > "$out/$stem.$suffix" 2> "$out/$stem.stderr" || code=$?
    printf '%s\n' "$code" > "$out/$stem.exit"
}
capture policy stdout "$bin/policy-tests"
capture staging stdout "$bin/staging-tests"

scenarios='promote reject-units reject-delay reject-nan reject-stale reject-protocol reject-switch rollback baseline-failure no-fallback post-commit-failure'

# Keep the built-in 180 samples, 20 ms period, 10 ms decision budget,
# 7.5 ms IPC cutoff, and built-in qualification/probation settings.
for scenario in $scenarios; do
    capture "default-$scenario" jsonl "$bin/shadow-demo" --worker "$bin/shadow-worker" \
        --scenario "$scenario"
done
capture default-live jsonl "$bin/shadow-demo" --worker "$bin/shadow-worker" \
    --candidate-manifest "$bin/candidate.manifest" --stage-at 20

# Functional exercise with explicit budgets and qualification boundaries.
for scenario in $scenarios; do
    capture "functional-$scenario" jsonl "$bin/shadow-demo" --worker "$bin/shadow-worker" \
        --scenario "$scenario" --samples 22 --qualify 3 --probation 8 \
        --period-ms 100 --deadline-ms 75
done
capture functional-live jsonl "$bin/shadow-demo" --worker "$bin/shadow-worker" \
    --candidate-manifest "$bin/candidate.manifest" --stage-at 20 \
    --samples 100 --period-ms 200 --deadline-ms 150

export SHADOW_QNX_TARGET_DIR="$bin" SHADOW_QNX_TRACE_DIR="$out/lifecycle"
capture lifecycle stdout sh "$bin/qnx_lifecycle_checks.sh"
capture timeout stdout sh "$bin/qnx_timeout_checks.sh"
pidin ar > "$out/functional-final.processes"

mkdir "$out/ipc"
(
    cd "$out/ipc" || exit 2
    capture fixture-7500000 stdout "$bin/qnx-ipc-tests" "$bin/qnx-ipc-receiver" 7500000
    capture fixture-112500000 stdout "$bin/qnx-ipc-tests" "$bin/qnx-ipc-receiver" 112500000
)
pidin ar > "$out/final.processes"
```

Run the verifier on the exported directory from the host:

```bash
python3 tools/verify_qnx_logs.py --pi4-functional /path/to/exported/shadow-pi4-run
```

Acceptance for the explicit functional runs:

- Policy and staging suites exit 0. Each trace identifies
  `qnx-native-ipc` and has consecutive sequences and an intact summary.
  Each non-failing scenario must accept all 22 samples and produce 22
  authoritative on-time outputs. Require exit 3 and output stopped at
  sequence 2 for baseline disconnection, 8 for no valid fallback, and
  13 for committed candidate disconnection, with output at every earlier
  sequence. The verifier checks promotion and rollback boundaries and
  each rejection reason against the existing scenario assertions.
- Require all 100 on-time outputs for each of the three live runs.
  Verify stage request after sequence 20 in the late-attachment run,
  replay of at least 16 samples, live qualification and complete
  probation before commit. Require the original baseline PID through the
  commit sample or until the run stops, with retirement after commit.
  Compare broker descriptors after cancellation and rejection to the
  baseline snapshot, and confirm no owned child survives broker exit.
- Both legacy timeout probes must reject the candidate at sequence 2 for
  `deadline_exceeded`, with candidate latency in [112.5, 250) ms.
  Require four samples, four on-time baseline outputs and no candidate
  PID or observation after rejection. Require a delayed-reply snapshot
  showing a broker worker in REPLY against the candidate PID. The verifier
  exits 77 when the behavioral assertions pass and that state capture is
  missing. This leaves delayed REPLY observation inconclusive. A stopped
  receiver alone cannot establish SEND.
- Use the [fixture guide](qnx-ipc-fixture.md) to judge each of the six
  SEND/REPLY cases at both IPC cutoffs. Both attempts for a case must pass
  before claiming coverage for that cutoff; retain every failed and
  inconclusive attempt. Check observation, cleanup and recovery evidence.
  On x86-64 QEMU at source `b351e88`, the fixture recorded 12/12 passes at
  112.5 ms and 12/12 inconclusive attempts at 7.5 ms (exit 77). Exported
  archive `cksum` was 652301128, length 25041 bytes. The Pi requires its own
  observations at each cutoff.
- Retain the default runs independently and report all deadline misses and
  publication gaps even if the relaxed suite passes. A successful process
  exit is insufficient to establish deadline compliance.

## Collect baseline-only timing trials

The collector runs the broker with `--control-stdin` and closed input, with
no candidate manifest or staging commands. Each repetition contains one
idle trial (no added load) and one CPU trial (a fixed number of target shell
busy loops). The broker writes straight to the evidence filesystem. Load
processes run across each CPU attempt and are terminated and reaped before
the next attempt. Store all attempts, including nonzero exits, as a single
collection.

Prepare a nonempty `environment.txt` before the run. Include the target
and image identity, source revision or unknown mapping, SDK/BSP version,
binary SHA-256 values from the bundle, scheduler policy, priorities and
affinity, and the evidence filesystem. Record observed CPU and memory
conditions and, on the Pi, cooling, temperature and throttling. For QEMU,
include host architecture/OS, QEMU version, accelerator, allocated RAM
and vCPU count. Mark unmeasured conditions explicitly. The collector adds
target `uname`, `pidin` identity and process snapshots, binary checksums
and UTC attempt timestamps. The busy-loop count identifies the load
generator configuration. Measure utilization separately when required.

For a short QEMU trial, use the existing guest binary directory and a fresh
writable archive destination. These arguments request three attempts per
condition, 100 samples per attempt, a 20 ms period, a 10 ms decision budget
and two busy loops.

```sh
sh /data/qnx_controlled_trials.sh /system/bin/shadowdeploy \
    /data/shadow-controlled-smoke 3 100 20 10 2 /data/environment.txt
```

On the Pi, the idle and CPU baseline-only control runs can use the same
collector with the verified project bundle. Supply the predeclared CPU load
count in `busy_loops`. Five attempts of 10,000 samples run for approximately
33 minutes when both conditions reach their requested sample counts.

```sh
sh "$bin/qnx_controlled_trials.sh" "$bin" "$out/controlled-trials" \
    5 10000 20 10 "$busy_loops" /data/environment.txt
```

Analyze the exported collection on the host:

```bash
python3 tools/summarize_qnx_trials.py /path/to/exported/controlled-trials > timing-summary.json
```

The analyzer exits 0 only after every planned attempt passes and the
collection metadata is complete. Inspect the report on exit 1. It tracks
delivered samples, produced outputs, deadline failures and missing outputs
against the planned count, and gives nearest-rank p50/p95/p99/max over
observed samples. Baseline IPC time is acquisition to reported completion,
including timeout handling when a timeout observation is present. The
report names every planned attempt even if its trace is missing,
incomplete, invalid, interrupted or stopped unexpectedly. A gap from a
completed fail-closed trace includes both the terminal sample without
output and any sample slots after the stop. Unknown counts stay null when
the evidence lacks a validated trace. Stop-on-failure injections belong to
the functional matrix above, where the verifier checks reason and stop
sequence independently. The controlled workload has no planned stops.

The baseline controls cover only the idle and CPU cells of the timing
matrix. Memory, slow reader, combined load, and lifecycle cycles retain
their separate acceptance criteria. QEMU measurements characterize the
observed virtualized environment. Run the hardware matrix on the physical
Pi before claiming board timing behavior.

The historical 2026-11-26 QEMU comparison can be reanalyzed using
`--legacy-plan 3 100 20 10 2` after pointing the analyzer at its exported
`timing/` directory. Those traces ran the candidate-enabled `rollback`
scenario with and without two busy loops. Keep that result separate from
the baseline-only controls. The legacy trace bundle does not include the
per-attempt cleanup records required by the new collector.

## Export native evidence

Export a tar archive of the entire run directory:

```sh
run=${out##*/}
tar -C /data -cf "/data/$run.tar" "$run"
cksum "/data/$run.tar"
```

Compare checksum and byte length on board and host, record the host archive
SHA-256 and keep the
original archive, artifact manifest, metadata, serial log and raw traces.
For a replay or demo consume copies of the exported traces. Store working
copies and generated artifacts outside the source history unless selected
evidence is explicitly approved for publication.

## Follow-up issue gates

| Issue | Future acceptance |
| --- | --- |
| [#1](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/1) timing diagnosis | After the functional pass, collect per-sequence same-clock monotonic acquisition, dispatch, IPC start/completion/timeout and decision/publication timestamps. Capture nominal release and actual release to separate release lateness from decision latency. Correlate wait state, scheduling, timer resolution and output backpressure with misses. Keep unclassified and inconclusive traces. Reproduce the same instrumentation on QEMU to attribute historical QEMU misses. Use Pi traces to characterize the physical board. |
| [#3](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/3) state and cleanup | Reproduce the fixture matrix at 7.5 ms and 112.5 ms on the Pi with evidence of actual SEND/REPLY, peer exit behavior, no stale second completion, descriptor/connection return, child cleanup and recovery. Separately exercise live cancellation during active replay and at the transition to live qualification. Retain a race attempt as inconclusive if the required interval cannot be observed. |
| [#4](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/4) active-path isolation | Use #1 evidence, then specify and verify a bounded candidate wait relative to the baseline decision deadline and a bounded telemetry queue with observable drop counters. Preserve baseline authority and fallback and measure complete behavior at idle, CPU/memory contention and a slow reader, including cleanup and output loss. |
| [#5](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/5) load and repeatability | After #1/#3/#4 acceptance, predeclare five runs of at least 10,000 samples per condition and at least 20 cancel/retry cycles per condition. Measure idle, CPU load, memory load, slow telemetry reader and combined load at the unchanged default budget. Require stable baseline PID until intended retirement, no leaked processes or connections, no output loss in non-failure scenarios and zero observed unexpected misses. Keep and report every run, including interrupted runs. |
| [#6](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/6) demo | After the above gates, record a target session showing Pi identity, baseline PID, stage/reject/cancel/retry/promote/rollback and cleanup. Use the workbench to replay the exact exported board trace; link revision, hashes, run parameters and complete outcome. |
| [#8](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/8) tracker | Link versioned evidence for each gate and record separate dispositions for host, QEMU, Pi functional, Pi IPC and Pi timing/load. Close only the gate supported by the recorded runs. |
| [#10](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/10) supported development host | Follow [the supported x86-64 installation](qnx-setup.md#obtain-and-activate-the-sdk). Record QSC version, installed package inventory and a fresh compiler transcript. Build and retain hashes and architecture evidence for the five x86 project targets (`shadow-demo`, `shadow-worker`, `shadow-candidate`, `policy-tests`, `staging-tests`) plus manifest. Record any x86 fixture outputs alongside the x86 archive and keep all AArch64 artifacts in their own archive. |

For #1/#4/#5 record clock source/resolution, kernel options, core count,
affinity, scheduler policy and broker/worker/load priorities, memory pressure,
CPU utilization, load-generator command/rate/seed, reader delay and buffer
limits, effective period and budget, temperature and throttle/frequency
state. Specify exact workload parameters before collecting the repeat matrix.
For each condition retain per-run counts (samples, outputs, all miss categories,
telemetry drops), p50/p95/p99 and observed maximum for release lateness,
decision latency and IPC time, plus the affected sequence IDs and timestamps.
Report the number of attempts and evidence of PID/descriptor/connection
return. Observed maxima describe the stated workload and run conditions.
