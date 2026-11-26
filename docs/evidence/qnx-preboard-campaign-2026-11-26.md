# QNX pre-board campaign, 2026-11-26

This campaign exercised the x86-64 native QNX backend in QEMU. It combined
stock broker lifecycle and timing runs, a gated transport fixture, and a
separate test-only replay worker for the operator cancellation witness.

## Stock broker and native transport fixture

The initial stock boot used binaries rebuilt from
`599a9cc32540d9f0b9a456f25d6304f0792a08a5`. The observation driver for
stock cancellation came from `09de7d7` (SHA-256
`9bfd06835bc53b79778560a9aa41ccb980328fb79b2e6e3203edad326a0e5ec9`).
The IFS SHA-256 was `df09660a840148d324bbbcd71761f1708295b4fb6b7ba1e8c53b57f7041f6980`
and the disk SHA-256 was
`868fbc04e976607e5175013af094bb9fb8d98148521fae3c9c66d279ae94f7ec`.
The `boot-idle-1/guest-data.tar` export SHA-256 was
`e7e448f7d348ed0f01bb74d10fb08a291d80e4ba3416bd7d57865e724879bbb9`.
Guest and host POSIX `cksum` matched (`2460799552 977408`), and QEMU
exited 0. The prior harness startup attempt launched no tests and is
retained as `boot-1/`. Stock artifacts are under the original checkout's
ignored `artifacts/qnx-preboard-campaign-20261126/` directory.

The stock `shadow-demo`, `shadow-worker` and `shadow-candidate` hashes match
the binaries listed in the gated-attempt table below. The native fixture
hashes identify the separate test executables.

| Native fixture | SHA-256 |
| --- | --- |
| `qnx-ipc-tests` | `5573e4893b58a53f56cbb5191f6b2bfc244b2b19ac75d4cbd4b4fe68da2d3f1c` |
| `qnx-ipc-receiver` | `994a08fe5a53e0f80bba38619842f70df5fd09ed152a15512afcbd7328616d34` |

The stock lifecycle driver `qnx_lifecycle_checks.sh` verified two 100/100
output runs at a 200 ms period and a 150 ms decision deadline, with three
fresh qualification samples and 15 probation samples. The driver checks
cancel/retry and replay-rejection/retry separately. The crash-and-retry
repetitions below use a different driver and workload.

The short baseline-only controlled collection ran three trials of ten
inputs per condition at 100/75 ms. The CPU condition ran two busy loops
inside the guest. The before/after binary checksums matched and all six
cleanup checks passed.

| Condition | Attempts passing continuity | Acquired / selected / requested | Decision misses | Maximum observed decision latency |
| --- | ---: | ---: | ---: | ---: |
| Idle | 3/3 | 30 / 30 / 30 | 0 | 5.932 ms |
| Two guest busy loops | 3/3 | 30 / 30 / 30 | 0 | 26.319 ms |

All six runs had zero initial or skipped nominal slots. The maximum
selection intervals across the three idle and three CPU attempts were
110.324 ms and 119.973 ms respectively. This short collection used
functional budgets. The earlier [controlled collections](../qnx-controlled-trials-2026-11-26.md)
used a 20 ms period and 10 ms deadline. They had 2/3 idle and 0/3 loaded
passes for baseline-only, and 3/3 idle and 0/3 loaded passes for
candidate-enabled rollback. The earlier images reused binaries without a
confirmed source revision. No fresh controlled default-budget runs were
part of this boot.

The separate native IPC fixture ran two attempts for each of SEND release,
peer exit and timeout, and REPLY release, peer exit and timeout with late
reply. It passed all 12 at the 112.5 ms functional budget (exit 0) and
reported all 12 inconclusive at the 7.5 ms default IPC budget (exit 77).
The [fixture protocol](../qnx-ipc-fixture.md) defines the wait, late
completion and cleanup evidence required per attempt.

Both stock cancellation cases passed behavior and cleanup. Live cancellation
was applied at sample 23 and the retry committed at 37. The before and
after REPLY views matched the broker thread and candidate PID, but sample
high-water marks `21,21,21,22` crossed a live input boundary, leaving exact
ordering inconclusive. Replay cancellation was applied at sample 18 and
the retry committed at 32. Both REPLY views matched with the same
high-water mark (17), but stock replay can move between historical
requests without publishing a live sample. That exact-request verdict
was also inconclusive. The host verifier exited 77 overall.

A separate stock retry established live ordering. The REPLY snapshots
around the cancel write matched the same broker thread and candidate peer
for one unpublished live input. All four high-water marks were 21.
The broker applied cancellation at sample 23 and committed the retry at 37. The replay case again passed behavior and cleanup, with
cancellation at 18 and commit at 32. Exact stock replay ordering remained
inconclusive for the same request-identity gap, so the overall verifier
exit remained 77.

Run the matching project tools from the packaged guest directory.
The controlled-trial environment file records target, source revision,
IFS hash and disk hash. Write each invocation to a fresh output path.
The fixture commands run from separate evidence directories per budget.

```sh
bin=/system/bin/shadowdeploy
run=/data/qnx-preboard
mkdir -p "$run/lifecycle-idle-1" "$run/ipc-7500000" "$run/ipc-112500000"
SHADOW_QNX_TARGET_DIR="$bin" SHADOW_QNX_TRACE_DIR="$run/lifecycle-idle-1/checks" \
  sh "$bin/qnx_lifecycle_checks.sh"
"$bin/qnx_controlled_trials.sh" "$bin" "$run/controlled" 3 10 100 75 2 \
  "$run/environment.txt"
(cd "$run/ipc-7500000" && "$bin/qnx-ipc-tests" "$bin/qnx-ipc-receiver" 7500000)
(cd "$run/ipc-112500000" && "$bin/qnx-ipc-tests" "$bin/qnx-ipc-receiver" 112500000)
SHADOW_QNX_TARGET_DIR="$bin" SHADOW_QNX_TRACE_DIR="$run/cancellation-ordering" \
  sh "$bin/qnx_cancel_ordering_checks.sh" stock
```

After export, run `python3 tools/summarize_qnx_trials.py EXPORTED_CONTROLLED_DIR`
and `python3 tools/verify_qnx_cancel_ordering.py EXPORTED_CANCEL_DIR`.
Preserve the drivers' stdout, stderr and exit status separately.

## Crash, rollback and healthy retry in one broker

The repeated crash lifecycle used `lifecycle_checks.sh`, with 90
requested inputs per attempt, a 200 ms period, a 150 ms decision budget,
a 112.5 ms IPC cutoff, three fresh qualification inputs and 24 probation
inputs. The driver kills the first candidate after it obtains authority.
A healthy retry repeats preparation and fresh qualification before
the second promotion. The verifier checks output identity for every
input, the baseline PID through rollback and retry, and native
connection restoration and owned-process cleanup.

The rebuilt image used the same stock binaries and added the lifecycle
driver and the cancellation driver from
`33faead6364311ea8284e366c60dec534e975d0d`. A serial harness attempt at
`boot-crash-retry-idle-2/` launched no tests because the command file
included literal newline escapes. The corrected harness used a fresh
guest directory.

| Later stock image artifact | SHA-256 |
| --- | --- |
| IFS | `e883b18c21ecebaa970275a8a538930c41f2ee5a436291d9ff5ef9c06069b11c` |
| Disk | `6641e40b7f4f0a36ff4e3f36675339a8d30b5b870a86ceea86ce93315e14c0f4` |
| `lifecycle_checks.sh` | `d214a8c6203b96280aef11aa0f6dd93191af55e6707bd4d3662649577d82b707` |
| `qnx_cancel_ordering_checks.sh` | `215f0101a540b51ff302a2816e3d48edff4930a979f6007505064c0d2a6a2bc3` |

From the packaged QNX shell and then on the host after export:

```sh
sh /system/bin/shadowdeploy/lifecycle_checks.sh \
  /system/bin/shadowdeploy /data/qnx-preboard/lifecycle
python3 tools/verify_lifecycle.py EXPORTED_LIFECYCLE_DIR
```

The loaded attempt uses two guest shell busy loops during the same
driver invocation. The wrapper records both load PIDs, stops and reaps
them, then checks whether either survives. The strict continuity verdict
requires complete selection, no decision misses and no unoccupied
nominal slots in the observed horizon.

| Attempt | Outputs / requested | Promotion, rollback, re-promotion, commit | Decision misses | Skipped slots | Maximum selection interval | Strict verdict |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| Idle 1 | 90/90 | 25, 26, 32, 55 | 0 | 2 | 233.379 ms | Fail |
| Idle 2 | 90/90 | 25, 26, 32, 55 | 0 | 2 | 240.172 ms | Fail |
| Idle 3 | 90/90 | 25, 27, 33, 56 | 0 | 1 | 261.166 ms | Fail |

The idle attempts each retained one baseline PID through rollback and
retry (434199, 438294 and 438294 in separate boots), then reached
functional commit and passed the native connection and cleanup checks.
All three strict verifiers exited 1 because nominal slots were skipped
despite complete selection and zero decision misses. The first boot also
included the separate stock cancellation retry.

The first loaded boot, `boot-crash-retry-loaded-clean/`, completed guest
identity collection. Its workload command exceeded the host serial
runner's 180 s bound before it returned an exit marker. QEMU exited 0
after the runner stopped it. No guest archive was exported, so output,
cleanup and strict continuity could not be assessed. The reason the
command exceeded the host bound remains unverified.

The bounded loaded retry exported its evidence. Two guest busy loops
ran for 40 s and neither remained after cleanup. The wrapper armed a
180 s driver watchdog and canceled it on driver exit. Generation 1
promoted at sample 58 and committed at 81. The driver could not send
the intended termination signal because the candidate PID no longer
existed when it issued the command. The driver exited 1 before
observing crash, rollback or healthy retry. The independent trace
summary counted 90/90 selected, zero decision misses and four skipped
nominal slots. The strict lifecycle verifier exited 1 at the missing
driver-success sidecar. The result records a failed fault injection
and an observed continuity shortfall under load. Rollback behavior
under that load remains unverified. QEMU exited 0 after export. The
archive's POSIX `cksum` was `605072429 105472`.

The retry packaged two independently bounded load workers with a 210 s
self-stop, a 180 s driver watchdog, and a wrapper that starts the
workers, runs the stock lifecycle driver, then terminates and reaps load
workers and reports whether they survived. The observed run ended by
parent cleanup before either bound expired.

| Loaded retry artifact | SHA-256 |
| --- | --- |
| IFS | `a8226c8cb9b30495def39297bf7bd591c45d3bd9f16ee11f55f6778d09e71282` |
| Disk | `96fbf25670327e243f338ec41409ecb8261571827c90d5ec9df90ac719e5afa8` |
| Load worker | `2536c342202d97532c6a9c173fca49ee97eff272dd2f9e7baaf505e52a5a6da4` |
| Driver watchdog | `5e91b3f0f21be863b86a320d281449bcf0a17cbe333c589cdd6db9f021c17cdb` |
| Load wrapper | `6268aa81184c1823150217b3e828bfac0f0395701b40a661bc8d8330a8f43262` |

| Local archive directory and file | SHA-256 |
| --- | --- |
| `boot-crash-retry-idle-1/guest-data.tar` | `ebb0b06878bcaa5aee6ae3fd8312e65448fabbfe283b6b4c26f892bd594c6a99` |
| `boot-crash-retry-idle-2-clean/guest-data.tar` | `e64aa19f5d17d6a4101d77f1376909784a19133f108c7b93ee91f6447560098a` |
| `boot-crash-retry-idle-3-clean/guest-data.tar` | `7cc31eb13a983ad4dcfaad75be6b7683df9f84dc0d7ed9f7f0d2992efecd3ecc` |
| `boot-crash-retry-loaded-watchdog/guest-data.tar` | `4e96b28a694d0405a34ffc4e9724bc224bfe8b5926623f1ad49b302e462018c1` |

## Operator cancellation with a test-only replay worker

Both attempts used 44 inputs, a 500 ms period, a 450 ms decision deadline,
a 337.5 ms IPC budget, one fresh qualification sample and ten probation
samples. The fixture bounded the first replay reply with a 300 ms gate.
An isolated copy of the image builder packaged the replay worker and
`qnx_cancel_ordering_checks.sh` alongside the stock files. The operator
command was the public `cancel` control line.

The first gated guest attempt used commit
`e247884a74dd9be731ebd2e114a522ca9155d186`. The broker selected all 44
requested inputs. Generation 1 failed preparation with
`replay_worker_disconnected` after sequence 17 and was rejected at sequence
18. The witness recorded receipt of replay sequence 1 by PID 3059734
under native receive ID 196609. Release and reply markers were absent.
The driver timed out waiting for `candidate_cancelled` and exited 1. The
host verifier exited 1 at the behavior prerequisite. Ordering and cleanup verdicts remained
unavailable. The reason for the disconnect was unverified.

The original export's guest and host POSIX checksum and length matched
(`3459158449 48128`). Its SHA-256 was
`73d0b04d7fc80c0baf5adeea0815e69de9f82ed90376d64a2d3b5d569be196b1`.

The revised capture at `267827c7af674321d5ca68f9c503ce414310cb8b`
wrote the receipt-matched release token immediately after the cancel
write. The worker recorded native receipt, release and successful
`MsgReply` for replay sequence 1 and receive ID 196609. The worker-side
receipt-to-release and receipt-to-reply-record intervals were 66.659 ms
and 67.247 ms respectively. The sender's total IPC latency was unmeasured.

The broker processed cancellation at sample 18 with the baseline still
authoritative. The canceled PID never regained authority. Generation 2
completed replay and fresh qualification, promoted at sample 23, and
committed at sample 32. All 44 inputs selected an output, 22 from the
baseline and 22 from generation 2. Descriptor restoration and retirement
of known owned children passed. Driver, verifier and QEMU each exited 0.
The revised export matched POSIX `cksum` `960954267 51712`.

Use the isolated source snapshot and package the matching test driver and
worker in the guest image. The [QNX setup](../qnx-setup.md) covers SDK and
container prerequisites. The two attempts used image ID
`sha256:cce4a67d31f14f7ae613cec68c3cc8aaca230106dc91e049f278544e5566e2d0`.

```sh
export SHADOW_QNX_IMAGE=sha256:cce4a67d31f14f7ae613cec68c3cc8aaca230106dc91e049f278544e5566e2d0
export SHADOW_DOCKER=/Applications/Docker.app/Contents/Resources/bin/docker
bash tools/qnx_container.sh build
bash tools/qnx_container.sh vm-image
```

In the guest and then on the host after export:

```sh
SHADOW_QNX_TARGET_DIR=/system/bin/shadowdeploy \
SHADOW_QNX_TRACE_DIR=/data/shadow-cancel-gated \
sh /system/bin/shadowdeploy/qnx_cancel_ordering_checks.sh gated
python3 tools/verify_qnx_cancel_ordering.py FRESH_TRACE_DIR --gated
```

The exports, build and capture logs, invocation, and full file inventories
are retained locally under
`artifacts/qnx-cancel-gated-guest-2026-11-26-attempt-1/` and
`artifacts/qnx-cancel-gated-guest-2026-11-26-attempt-2/`. Both used
QEMU 11.1.3 TCG with two vCPUs, 1 GiB RAM, snapshot disk and no network.

| Cross-built file | SHA-256 shared by both gated attempts |
| --- | --- |
| `shadow-demo` | `8ff1f5e2efc52bba16572e7d070976b64e801f016fd4cddec15795f39bded526` |
| `shadow-worker` | `9b56845a7204abb9e7f15c68a1165d457b488898ca3d13de0c344c5bfe6b4d12` |
| `shadow-candidate` | `dac99a945346cadd2a7a76910f8ba329dd39a7d4e8a7dc79acc2cb65bf561cfc` |
| `candidate.manifest` | `a1adf726aecf04a24de1bebe9a778f9d06f014a7d2a5a30b29a854af23f6bdee` |

| Artifact | Attempt 1 SHA-256 | Attempt 2 SHA-256 |
| --- | --- | --- |
| IFS | `143d8b88050828e00f7762747790b14657d0ddfbf856f52db0f00bf95bada5a1` | `e8281ce9e4185267dcffe328eb84b39a424755875186872d3e8d0fb946ccd7bf` |
| Disk | `1cb05c1e25399ba0b3aa1853b4770d1b7c72356cd383b04ed48168c297a37a8c` | `907b1f9f34c533b88b63a68fba944fdf6c21b90997783025a252bee7c232c343` |
| Replay worker | `58744a9a1895769c1ba3d31ea73de6a130722224ba468a5641e5c3edfac9ea94` | `d954179a4732c1452d770907e5258515d00cbf446b5dc9876d9998a193edd476` |
| Guest driver | `24f35b50da562fccc0fdeb36d8153015d83d486626585477c14e0ba23fb745e6` | `29378901cdc2584aff70cc27a0cc88962e3d9747dc6627879e9e8498b19579b7` |
| Guest export | `73d0b04d7fc80c0baf5adeea0815e69de9f82ed90376d64a2d3b5d569be196b1` | `696f834ee3893f273ef262ee99b08a0c866675501e21de2bdb358b136874429c` |

## Host checks

The pre-gated host Debug checks passed 9/9 CTest suites in the normal
and ASan/UBSan configurations. The C++17 build used `-Wall -Wextra
-Wpedantic -Werror`. The subsequent focused cancellation verifier suite
passed all 15 generated controls after the gated worker was added. The
host controls exercise verifier acceptance and rejection. The native
guest results establish the observed QNX behavior.

## Measurement limits

The host was Darwin 25.6.0 ARM64. QEMU 11.1.3 used x86-64 TCG,
two vCPUs, 1024 MiB RAM, snapshot mode and disabled guest networking.
The stock guest reported
`QNX shadowdeploy-qnx 8.0.0 2026/10/30-09:10:05EDT x86pc x86_64`.
The gated cross-build compiler identified itself as QCC 12.2.0. The
[release audit](qnx-release-audit-2026-11-23/README.md) records the
installed package membership against the cached 8.0.6 release definition.
The controlled runs include host scheduling and guest evidence writes.
The native measurements cover broker acquisition, selection, authority and cleanup.
External delivery, physical-board timing and worst-case execution time
remain unmeasured. The local QSC 1.7.0 environment remains separate
from the supported x86-64 installation requirement.

Worker retirement and JSONL writes occur after the measured decision.
Both can delay a later release, and live selection still waits for the
bounded candidate exchange. This campaign did not isolate the effect of
each path. The [architecture notes](../architecture.md)
describe what the post-decision retirement counter measures and the
separate #4 proposal.

The [older cancellation probe](qnx-native-cancel-2026-11-26.md) observed
baseline restoration at a sample boundary and cleanup. The relation
between its cancel write and the native wait was unobserved. This
campaign tested operator cancellation at a larger observation budget.
The separate native transport fixture supplies the SEND/REPLY timeout
and unblock evidence.

The [English lifecycle recap](../qnx-lifecycle-demo.mp4) draws from the
separate [recorded same-broker run](live-lifecycle-2026-11-26.md).
Its 90/90 outputs had zero decision misses and one skipped nominal slot.
The strict verifier exited 1. Its runtime revision was
`ebd2c9c7cedd33d9d43b41fd56efe213e502b78d`. The video can be regenerated
from the recorded summary with
`python3 tools/render_lifecycle_demo.py docs/qnx-lifecycle-demo.mp4`.
The checked-in MP4 SHA-256 is
`2d1948db9fb94845225da4e1416f524c10542b2908871a5f9963ad2f1b0b4858`.

## Remaining acceptance

| Issue | Remaining evidence |
| --- | --- |
| [#1](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/1) | Timing diagnosis remains deferred. The measurements do not isolate scheduling, native IPC, emulation or filesystem contribution. |
| [#3](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/3) | Observe the default 7.5 ms SEND/REPLY matrix with peer exit, timeout, stale completion and cleanup. Extend cancellation observations to near-deadline ordering and the replay-to-live transition. The stock replay wait was observed but its exact request identity remains unverified. The test-only witness verified a single held replay request at the longer budget. |
| [#4](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/4) | Review the bounded retirement and telemetry design after #1. Changing the production schedule requires design approval and evidence for waits, queue losses and cleanup under contention. |
| [#5](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/5) | After #1/#3/#4 acceptance, run five trials of at least 10,000 samples and at least 20 cancel/retry cycles per condition at the unchanged default budget. Cover idle, CPU, memory, slow-reader and combined conditions. Preserve all outcomes and require stable baseline identity, cleanup, no unexpected misses and no output loss in non-failure scenarios. |
| [#6](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/6) | The [English recap](../qnx-lifecycle-demo.mp4) and its linked source evidence cover the identified QNX guest's same-broker lifecycle with reproducible video generation. Publish the recap with the source revision, run parameters and its strict continuity limit. |
| [#10](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/10) | Repeat installation and build with QSC 2.0.4 or later on a supported x86-64 development host and retain inventory, compiler output and architecture-specific hashes. |
| [#11](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/11) | Provision the Pi 4B BSP and verify the VL53L0X I²C input and three-state GPIO LED through the matching target drivers. Use a recorded sensor trace for timed comparisons. |
| [#12](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/12) | Run live Ethernet telemetry from the Pi to the laptop. Verify reconnect, producer identity, gap and drop accounting, board/dashboard count reconciliation, and behavior with a stalled reader. |

The [Pi 4B qualification guide](../qnx-pi4-qualification.md) and the
[pre-board pipeline](../preboard-pipeline.md) define the target procedure
and existing replay/relay paths.
