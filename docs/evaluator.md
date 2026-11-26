# Evaluator evidence and release handoff

ShadowDeploy governs the application output selected for each sample. An OTA
delivery system distributes installation artifacts. QNX High Availability
Manager (HAM) supervises services and can trigger recovery actions such as a
restart. ShadowDeploy gives the baseline and candidate the same stream,
checks candidate outputs for semantic and timing validity, and switches
authority at a sequence boundary. During probation, it can fall back to the
current valid baseline sample. After commit retires the baseline, loss of
valid authority stops output. Delivery, supervision and output selection are
complementary responsibilities. [QNX High Availability Framework](https://www.qnx.com/developers/docs/8.0/com.qnx.doc.ham/topic/hamover.html)

## Archived native demo

[Play the 44-second H.264 MP4](native-qnx-demo.mp4). English captions present
individual decisions from original recordings in an edited sequence. The
source is the QNX x86-64 guest running in QEMU TCG on an ARM64 macOS host on
2026-11-23, with archived runtime revision
`af72fba664ec813e071572ac80495bf0e70c6edb`. See the
[export checksums and original capture record](evidence/qnx-validation-2026-11-23/README.md)
and [environment, configuration and reported outcomes](verification.md#native-qnx-execution-2026-11-23).

| Archive trace | Shown sequence and event | SHA-256 |
| --- | --- | --- |
| [default-reject-delay](evidence/qnx-validation-2026-11-23/shadow-validation/default-reject-delay.jsonl) | 23, output stopped after exceeding the 10 ms decision budget | `043b6eff3d53d61ea3409757bf6f285b237b4cf8f73b444e7599960fddb7bb6a` |
| [functional-reject-units](evidence/qnx-validation-2026-11-23/shadow-validation/functional-reject-units.jsonl) | 2, candidate rejected for invalid position | `8a25b1f34f75370943aac7fce9e336625cd7b8030fe45653b6cdf8184ddad444` |
| [functional-reject-delay](evidence/qnx-validation-2026-11-23/shadow-validation/functional-reject-delay.jsonl) | 2, candidate rejected after bounded IPC wait | `f0201004b6fa56befb62b2665f8c25c59fd43d1e4cc14a4515dbae91ecb9c20d` |
| [functional-rollback](evidence/qnx-validation-2026-11-23/shadow-validation/functional-rollback.jsonl) | 4 and 8, candidate promoted and then disconnected during probation | `bca3bec4066665fb58c781f7c8dfb0e98820a60018c183c99ffa5e7f6322c933` |
| [functional-live](evidence/qnx-validation-2026-11-23/shadow-validation/functional-live.jsonl) | 81, candidate committed after promotion at 42 | `2cc8318e0b8b9702c1dd7d6e5bc945bc9dab44cc50fc2f234a4db042d81c92fe` |

The three functional scenario traces use a 100 ms period, 75 ms decision
budget and 56.25 ms IPC cutoff. The separate live attachment trace uses
200/150 ms. The default trace uses 20/10 ms with a 7.5 ms IPC cutoff. It
rejected a slow candidate at sequence 2, then stopped at sequence 23 with a
30.306 ms decision. Its summary records 22 selected outputs and one missing.
Observed guest latencies describe these runs. Hardware timing remains unmeasured.

Regenerate locally with Python, Pillow and an OpenCV/NumPy installation whose
video backend supports H.264 encoding. Supply a TrueType font such as Arial
and an output path. Font and encoder choices may change the file bytes.

```sh
python3 tools/render_native_demo.py --font /path/to/font.ttf docs/native-qnx-demo.mp4
```

## Open release gates

| Issue | Evidence available | Acceptance still required |
| --- | --- | --- |
| [#1](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/1) Timing diagnosis | Five historical default scenarios stopped early. A [separate QEMU comparison](evidence/qnx-native-cancel-2026-11-26.md) completed three unloaded attempts and stopped at the first decision in each of three loaded attempts. | Root-cause diagnosis and deadline tuning remain deferred. Collect same-clock nominal release, acquisition, dispatch, transport and publication markers to distinguish the contributing intervals before fault attribution. |
| [#3](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/3) Native IPC and cancellation | Historical diagnostics observed REPLY. The gated fixture recorded 12/12 QEMU passes at 112.5 ms and 12 inconclusive attempts at 7.5 ms (exit 77). Guest cancellation at a sample boundary was observed again on 2026-11-26. [Fixture and cancellation evidence](evidence/qnx-native-cancel-2026-11-26.md) | Record exact request ordering relative to a live IPC exchange and the replay-to-qualification transition, plus the fixture attempt verdicts for the target platform. |
| [#4](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/4) Active-path isolation | The [pre-board pipeline](preboard-pipeline.md) separates browser drains from the trace relay. Synchronous broker stdout and live shadow waits can still delay broker progress. | Implement producer-side isolation and compare no-reader, slow-reader and healthy-reader runs against a baseline-only control at the default budget. |
| [#5](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/5) Repeatability | The six-run QEMU comparison is a short software observation with three retained loaded failures. | Execute the [qualification matrix](qnx-pi4-qualification.md#follow-up-issue-gates) after #1/#3/#4 acceptance. |
| [#10](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/10) Supported build host | Installed QSC 1.7.0 on macOS ARM64 fell short of the documented install prerequisites. Previous package/build-ID checks are [recorded separately](evidence/qnx-release-audit-2026-11-23/README.md). | Provide a supported x86-64 development host, QSC 2.0.4 or newer and authorized SDK access, then retain the fresh build transcript, package inventory and artifact hashes. |

For [#6](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/6),
record the provisioned Raspberry Pi 4B after the functional, IPC and load
gates. Show board identity, stage, reject, cancel/retry, promote, rollback and
cleanup. Replay that same exported board trace in the workbench. For
[#8](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/8), attach each
run to its platform, source revision, image/binary hashes and verdict.
For [#13](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/13), the local
walkthrough and reproduction command are available above. Final publication
and submission depend on separate authorization and target evidence.

## Isolation design and workload recipe

Treat the selected output as its own channel with a bounded delivery contract.
Place telemetry on a finite, preallocated queue drained by a separate writer.
A full queue drops the incoming telemetry record, increments a monotonic
counter and preserves the sample sequence. Include a run ID and the latest
drop counter in later records so a reader can detect and quantify gaps. A
writer stall, disconnect or filesystem error must remain confined to that
writer. Track selected-output misses independently from telemetry drops.
Reject an invalid or late selected result as the existing authority rules
require.

Give the baseline path its own time reservation. Calculate the shadow cutoff
from the absolute decision deadline minus a measured reserve for policy and
publication. During qualification, skip shadow work whenever that reserve
cannot be protected. During probation, select a timely, valid standby result
under the existing rollback policy if the candidate fails or misses its
cutoff. Tag asynchronous results with generation and sequence so late
completions can be discarded. Admit one candidate only after checking its
manifest and target-specific CPU, memory and IPC resource allowance, and
rate-limit background staging and replay. Test admission rejection and
resource return before admitting another generation. Priorities, affinity,
resource limits and reserve values require target measurements. This is a
design for the remaining work.

Declare five trials of at least 10,000 samples and 20 cancel/retry cycles per
condition at 20/10 ms. Run baseline idle, fixed CPU workers, fixed
memory-pressure workers, a rate-limited telemetry consumer, and combined
load. Include a disconnected consumer in the telemetry isolation check.
For each platform fix the CPU worker count and affinity, memory limit and
touch rate, consumer byte rate and queue limits before collecting results.
Start load before acquisition and verify its actual duration and rates.
Retain the exact commands, achieved load, revision, artifact hashes, clock,
scheduling configuration and all failed or inconclusive attempts. Report
release lateness, acquisition-to-decision latency, IPC time, sequence gaps,
selected-output misses, and resource cleanup. The hardware matrix requires
a provisioned Pi 4B, BSP and verified image plus serial access and the
declared load sources. [Pi qualification procedure](qnx-pi4-qualification.md)
