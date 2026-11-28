# QNX guest timing and admission, 2026-11-27 to 2026-11-28 (KST)

The stock comparisons use a 20 ms period, a 10 ms acquisition-to-decision
budget and a 7.5 ms acquisition-to-IPC cutoff. These are observations in an
x86-64 QNX guest under QEMU TCG. Pi 4B timing, worst-case execution time and
continuous external service on target hardware are separate gates.

| Issue | Evidence and remaining gate |
| --- | --- |
| [#1 Timing diagnosis](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/1) | Guest diagnosis scope met. A QNX kernel trace identified READY waits in one trial. A separate host trace bounded vCPU off-core time inside two marked misses. Stock native IPC handoffs and cadence target selection were corrected at the original budgets. The equal-priority miss and the later managed guest miss remain in the record. General default-budget robustness and hardware timing remain open. |
| [#4 Active-path isolation](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/4) | Approved guest design scope met. The profiled guest preserved the baseline across admission refusal and delivered all authority records on time in the bounded receiver probes. The longer stall case missed a decision after the stall, with authority withheld. Production reaping still runs synchronously after the write. The reviewed bounded retirement design remains future implementation. Hardware resource budgets and repeatability remain open. |
| [#5 Load and repeatability](https://github.com/SungJaeYu/qnx-shadow-deploy/issues/5) | One of the five original fixed-grid idle runs passed strict timing. The six-arm profiled series added independent output delivery evidence and found timing and lifecycle verification failures. Qualification requires the complete [matrix](qnx-pi4-qualification.md#follow-up-issue-gates) after #1/#3/#4, including five runs of at least 10,000 samples and at least 20 cancel/retry cycles per condition. |

## Stock source revisions

| Revision | Behavior |
| --- | --- |
| `a2fe0ea` | Original stock broker after live range input merge |
| `3289bd4` | Single-worker native direct exchange plus test include correction |
| `8d8f455` | Adds the fixed release grid for synthetic and file sources |
| `16c3a16` | Added the opt-in candidate address-space limit and memory admission snapshot, with a native counter-format failure |
| `c0e8deb` | Accepts the native counter size annotation while validating the hexadecimal page count |
| `af9f1f9` | Allows a guarded in-slot release and retains the occupied-slot accounting |
| `53cf1fa` | Caps and locks address space in the broker-owned child before replacing it with the candidate |

The direct path executes the existing native exchange on the broker thread
when one worker participates, including baseline service while staging and
candidate-only service after standby retirement. Dual-worker samples still
submit both exchanges before collection. The optional phase record marks a
direct start and leaves dispatch and future timestamps null. SEND/REPLY
flags, the absolute IPC cutoff, connection ownership, protocol validation
and fail-closed selection are shared across the two paths. The macOS and
Linux builds retain dispatched exchanges.

For broker-owned sources, each acquired input belongs to an observed
nominal slot on the fixed monotonic grid. The current scheduler uses the
latest of the next slot boundary after the actual occupied slot, completion
of the preceding work, and half a period after the actual acquisition
(rounded up to a whole nanosecond). This allows an eligible acquisition
inside the slot and resumes at a boundary when possible. Live range input
retains its producer timestamps and separate read timeout.

## Diagnostic host scheduler state

In two instrumented first-sample failures, both QEMU vCPU threads spent almost the entire acquisition-to-selection interval running on macOS CPUs. The upper bound on the time when either vCPU was off-core was 0.129 ms in trial 1 and 0.110 ms in trial 3. Each host interval exceeded 16 ms against a 10 ms guest decision deadline. Host vCPU descheduling occupied too little of either observed interval to account directly for that excess.

### Conditions and results

Three baseline-only broker launches ran sequentially in one QNX 8 x86-64 guest under QEMU TCG with two vCPUs and 1 GiB RAM. Two ordinary shell arithmetic loops ran during each trial. Each broker requested 100 samples at a 20 ms period, 10 ms decision deadline and 7.5 ms IPC cutoff. The diagnostic broker contains first-sample x86 NOP markers added to commit `86334371f987988b219bd7a943ff6227ef01dacc`. A host plugin recorded their execution. Intermediate dispatch and IPC markers were disabled in this run. The first-sample acquisition, collection-return and selection markers remained active. The first and third brokers stopped on the first sample. The second selected one output and stopped on the second sample.

| Trial | First decision on guest (ms) | First output | First decision, host inner–outer bounds (ms) | Host non-running time, vCPU 0 / 1 (ms) | Host runnable wait, vCPU 0 / 1 (ms) |
| --- | ---: | --- | ---: | ---: | ---: |
| 1 | 16.262278 | Withheld | 16.209084–16.227750 | 0.065458 / 0.063377 | 0.046334 / 0.036877 |
| 2 | 9.294162 | Selected | 9.266125–9.274875 | 0.014457 / 0.013835 | 0.001792 / 0.003459 |
| 3 | 16.577096 | Withheld | 16.530708–16.537250 | 0.044416 / 0.065126 | 0.026125 / 0.045501 |

Non-running time includes all Runnable, Blocked and Interrupted intervals in the outer marker window. Summing the two vCPU totals gives a conservative upper bound on the time when at least one vCPU was off-core, since the intervals can overlap. The unmarked second decision in trial 2 missed at 15.353916 ms. This capture cannot assign host scheduler intervals to that decision.

### Matching clocks and reproducing the totals

1. Attach Instruments System Trace to the marked QEMU process before releasing the guest workload. Record the host plugin event stream through clean VM shutdown. Retain the broker outcomes and exact binaries. In this capture the signpost export contained zero rows.
2. Export the `syscall` and `thread-state` tables. In the former, select `thread_selfusage` events whose recorded stack includes `clock_gettime` and the plugin's `at_marker`. The plugin reads the host monotonic raw clock immediately before and after the thread-CPU clock read that issues this syscall. Match each syscall to its callback using host thread ID and per-thread order. Fifteen callback records matched fifteen syscalls, split six and nine across the vCPUs, with zero plugin-buffer drops.
3. Cross-check the match independently. For each event with raw readings B and A around a traced syscall starting at S and ending at E, the compatible raw-minus-trace offset lies in [B − S, A − E]. All fifteen intervals intersected in a 624 ns window, and the widest raw callback bracket was 1.667 µs. The scheduler calculation uses the Instruments time domain directly. Guest and host durations can differ and do not require a common clock rate.
4. For each first decision, measure the inner window from the end of the acquisition-after syscall to the start of the selection-before syscall. Bound it from outside using the start of the acquisition-before syscall and the end of the selection-after syscall. Clip each vCPU's scheduler intervals to these windows. The exported states accounted for the full duration of both windows on both vCPUs for all three trials.

The diagnostic broker rebuild patch (SHA-256 `856faf07bfcb92c321f9de59a0a27397e0223b5db0cb331f2a8e4a0bee8627bc`) and harness sources are preserved locally under `artifacts/qnx145-reproduction/`. The private evidence bundle under `artifacts/qnx15-host-scheduler-68680/` retains the trace and exports, plugin source and header, binary, marker CSV, guest archive, and analysis scripts. Extract `host-system-trace.tgz` beside `analyze.py`, `inspect_frames.py` and `events.csv`. Run `python3 analyze.py guest-data.tar` to regenerate the timing summary. The host trace can contain unrelated host metadata and remains in the local evidence bundle.

| Artifact | SHA-256 |
| --- | --- |
| Instruments trace and XML export archive | `92afcd14faf748bb729aa47b090d44009a544f009da35bb01a2c677fab49146c` |
| Host marker CSV | `2a0083e15cd7bb862b673d563c954fbd9cf7be43e017bd41ad183849550e2ed1` |
| Guest evidence archive | `ad17136ac091f1d2d56f4168a95142e9ba5d7d901358b6078f81a05483a76bdf` |
| Diagnostic broker | `013180bb442db4741ba4f924305413735ca075429222a879015101303a642293` |
| Worker | `78ab48c1136a29b4faa620f504f9f5cb026c6ab80ea9a37143062b9ede695f54` |
| Host plugin | `71d98c4d9c1755dae2326e15e29c3410b32d927fe15a448b949f9caf331cb65c` |
| Guest IFS | `ff076348efa6e13bc4283fbb69836e663b686cb04ebeb1fb0ebb11753d754772` |

The host Running state identifies CPU residency of a QEMU thread. It includes emulation, guest load, guest broker/worker execution and host kernel work on that thread. This trace provides no guest thread-level attribution within the two missed intervals. An earlier [QNX kernel trace](evidence/qnx-qualification-follow-up.md#guest-scheduler-state-during-one-missed-decision) attributed 9.536 ms of one 11.992 ms guest miss to three nonoverlapping READY waits under two-loop contention. The waits occurred before native exchange, after reply and before the main thread returned from collection. That observation applies to its own trial. The instrumented host findings likewise apply to these two marked misses. Uninstrumented source revisions, other timing failures, and physical board behavior require separate evidence.

## Paired stock Release trials

Each revision requested 300 baseline-only acquisitions per condition.
The two-loop conditions used identical arithmetic work. The equal-priority
loops ran at the shell's default priority and the managed loops used
`on -p 5`. No candidate or external output receiver participated.
All rows include the terminating sample, if one was recorded.

| Condition | Revision | Selected / requested | Skipped nominal slots | Decision misses | Decision p50 / p95 / max (ms) | Max selected interval (ms) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Idle | Original | 300 / 300 | 48 | 0 | 1.536 / 1.932 / 3.926 | 26.938 |
| Idle | Direct | 300 / 300 | 49 | 0 | 0.990 / 1.205 / 1.540 | 26.166 |
| Equal priority | Original | 0 / 300 | 0 | 1 | 12.107 / 12.107 / 12.107 | — |
| Equal priority | Direct | 213 / 300 | 26 | 1 | 3.934 / 6.356 / 10.813 | 30.531 |
| Managed priority | Original | 300 / 300 | 23 | 0 | 0.314 / 0.433 / 2.416 | 33.431 |
| Managed priority | Direct | 300 / 300 | 26 | 0 | 0.216 / 0.486 / 2.214 | 24.895 |

The original equal-priority run exited 3 on its first decision. The
direct run reached sample 214 and logged a withheld decision. The
serial wrapper timed out after 60 seconds and shut down QEMU. A recovery
boot of the same overlay exported the complete one-miss summary,
a truncated final phase-timing row, and no exit sidecar. The managed
pair ran in that recovery boot. The idle
and managed pairs completed their requested acquisitions, with empty
nominal slots under the old acquisition-relative schedule. One pair
per condition provides a mechanism check and limited outcome comparison.
The equal-priority distributions contain different numbers of observations.

The direct revision also returned 0 from the native `staging-tests` executable, covering successful dispatch-to-direct use of one connection, an expired cutoff, malformed reply, delayed IPC timeout and failed-connection reuse across modes.

The separately launched 16 MiB stock control completed 240/240 selections
and committed with a 7.351 ms maximum decision, 35 skipped slots and a
40.674 ms maximum selection interval. It used the installed guest image's
binaries under an external launcher cap, before broker-owned candidate
admission was exercised.

## Reproduce the stock comparison

Cross-build each source revision in a separate checkout using the same
[QNX SDK and CMake toolchain](qnx-setup.md#cross-build) with
`-DCMAKE_BUILD_TYPE=Release`. The recorded container used CMake 3.28.3,
Ninja and QCC 12.2.0 for `gcc_ntox86_64`. Copy the broker, worker,
candidate and manifest
from each build into separate guest directories on the same QNX SDP
8.0.6 image. The guest kernel identifies itself as `8.0.0`, build
`2026/10/30-09:10:05EDT`, for `x86pc x86_64`. The recorded guest used QEMU 11.1.3 TCG on Darwin 25.6.0 arm64, two guest
vCPUs, 1 GiB RAM, 4 KiB pages, an isolated writable overlay, and no guest
network. Capture guest identity, `pidin info`, scheduling priorities,
binary hashes, exits and cleanup snapshots. Export the traces before
shutdown.

Run once per directory and condition. Choose a fresh output prefix. For
managed load use `on -p 5` before each `sh` command. Omit both loops for idle.

```sh
sh -c 'n=0; while :; do n=$((n+1)); done' & load1=$!
sh -c 'n=0; while :; do n=$((n+1)); done' & load2=$!
sleep 1
kill -0 "$load1" && kill -0 "$load2"
./shadow-demo --worker ./shadow-worker --control-stdin --phase-timing \
  --samples 300 --period-ms 20 --deadline-ms 10 \
  </dev/null >"$prefix.jsonl" 2>"$prefix.err"
echo $? >"$prefix.rc"
kill "$load1" "$load2"
wait "$load1"
wait "$load2"
pidin ar >"$prefix.after"
```

The archive's seven paired and control traces passed the
`validate_rows` schema and continuity checks in `tools/verify_qnx_logs.py`.
Run `python3 artifacts/qnx145-reproduction/guest/qnx145-summary.py`
with the extracted trace paths to regenerate the aggregates.
For new matrix collections with the standard provenance and cleanup
sidecars, use `tools/qnx_controlled_trials.sh` and
`tools/summarize_qnx_trials.py`. Count unavailable outputs against
the full request, use the nearest-rank percentiles over observed decisions,
and compute intervals only between successful selections. A stopped run
with zero skipped slots can have stopped before the next nominal slot
entered the observed horizon. Broker selection timestamps do not measure
receiver arrival.

| Artifact | SHA-256 |
| --- | --- |
| Guest IFS | `ff076348efa6e13bc4283fbb69836e663b686cb04ebeb1fb0ebb11753d754772` |
| Guest base disk | `047b8f96827728cc9e4bfdcb52fa2be928c1d66bebf29fd03350f7fb5b6443f9` |
| Original Release broker | `0ad9db7f2da9a72deab12ae81dba7a7e199082433728cdb8287aada2dd4c6c44` |
| Direct Release broker | `1c3f93ca762f4521456ccd6ab6a1d4b00579e7f04887b096efdfb0acafb7d5c0` |
| Grid Release broker | `8d63c9ee390f4926813a95f24341244c8919d2bf9d8b4758cd8fcd5e1377cb6f` |
| Initial admission Release broker (`16c3a16`) | `47964640827cd1d1b12dc892a5ef86d9842366bb92e53b748122b530e32a5e2e` |
| Same-PID Release broker (`53cf1fa`) | `2d9af47c8dcc9d50167f9398233a73fc21712d60d661af3c5ca519da08cf55dd` |
| Shared Release worker | `b52ea116831dcdd701d20577dab92b01dbd84d9ffaf83a28b221ff374c637718` |
| Shared Release candidate | `a0a36a7d9b642c57d8f5d691aa13edd2823941593cc485bb56f6f28efac56473` |
| Shared candidate manifest | `a1adf726aecf04a24de1bebe9a778f9d06f014a7d2a5a30b29a854af23f6bdee` |
| Paired guest archive | `88fa761f166bd0403add72eb4b75b8d2dca728dab677b4a1aec2d23766ba775d` |

## Fixed-grid stock idle lifecycle

Five fresh Release brokers at `8d8f455` ran in one idle guest boot with
the fixed release grid and without the later admission profile. Each
requested 10,000 inputs. Each reached its 20th cancel/retry and final
commit, including trial 5, which failed after commit. The prior
[acquisition-relative idle campaign](evidence/qnx-qualification-follow-up.md#final-source-qnx-sustained-idle-controls)
completed five of five 10,000-input runs with 1,434–1,457 skipped
nominal slots per run. It used an earlier runtime and a separate guest
boot. The new campaigns therefore show the observed outcomes with
both sets of changes and across separate runs.

| Run | Selected / requested | Commit sample | Skipped slots | Deadline misses | Decision p50 / p95 / p99 / max (ms) | Max selected interval (ms) | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 10,000 / 10,000 | 480 | 2 | 0 | 0.125 / 0.268 / 0.419 / 2.764 | 31.100 | Functional pass, cadence fail |
| 2 | 10,000 / 10,000 | 440 | 1 | 0 | 0.133 / 0.292 / 0.471 / 8.582 | 35.320 | Functional pass, cadence fail |
| 3 | 10,000 / 10,000 | 522 | 1 | 0 | 0.135 / 0.308 / 0.506 / 5.708 | 31.017 | Functional pass, cadence fail |
| 4 | 10,000 / 10,000 | 460 | 0 | 0 | 0.130 / 0.269 / 0.406 / 3.571 | 28.638 | Strict pass |
| 5 | 2,555 / 10,000 | 478 | 0 | 1 | 0.132 / 0.374 / 0.601 / 10.432 | 28.100 | Decision miss and exit 3 at sample 2,556 |

All 100 captured post-cancel side-channel sets matched the respective
initial two-entry set. At final commit the set still held two entries,
with the final candidate in place of the baseline. Four runs completed
the verifier's full retirement sequence. The failing fifth run ended
before the final retirement entry. A subsequent process snapshot for
each run contained none of its recorded broker or worker PIDs, including
the final candidate identified from the fifth run's commit event.

Use the [extended cycle procedure](cancel-retry-cycles.md#extended-qualification)
to repeat the 10,000-input workload. These trials used an archived
driver with native `pidin` ownership checks and descriptor snapshots.
Copy each compressed trace into a working directory and decompress
it to `cycles.jsonl` beside its `exit` and `retirement` files before
running the verifier. This campaign's archive is
`artifacts/qnx145-cycles-idle-1795786522/guest-data.tar`, SHA-256
`c9481131505b6ea3103596207213c4e676d13f26a0b87d4a780403ce12191120`.

### Why the fixed-grid idle trials still skipped slots

The grid-only QNX run used a 20 ms period, a 10 ms decision deadline and a 7.5 ms IPC cutoff. All five incidents occurred after commit with the candidate as sole authority. Each row was derived by parsing the guest archive for the incident and its preceding samples.

| Run and sequence | Scheduled target versus acquisition | Effect |
| --- | --- | --- |
| 1 / 2674 | Previous acquisition 10.324 ms late. The half-period guard expired 0.324 ms into the following slot. | Scheduler selected the boundary one slot later. Previous decision finished 9.316 ms before the skipped boundary. |
| 1 / 2681 | Previous acquisition 12.474 ms late. Guard expired 2.474 ms into the following slot. | Scheduler selected the boundary one slot later. Previous decision finished 7.302 ms before the skipped boundary. |
| 2 / 5353 | Scheduler targeted the immediately following boundary. Acquisition occurred 21.686 ms after that target. | Actual wakeup or pre-acquisition work crossed the end of the slot. |
| 3 / 4465 | Previous acquisition 11.857 ms late. Guard expired 1.857 ms into the following slot. | Scheduler selected the boundary one slot later. Previous decision finished 7.854 ms before the skipped boundary. |
| 5 / 2556 | Acquisition occurred 13.329 ms after the target and remained inside its nominal slot. | Candidate completion took 10.101120 ms. Selection followed at 10.431517 ms and withheld output. |

Candidate completion on each of the four rows reporting a skipped slot was below 0.25 ms. Selection took at most 0.303 ms. Runs 1, 2, 3 and 5 committed at sequences 480, 440, 522 and 478 respectively. No candidate retirement or staging transition occurred near the incidents. The available sample records do not timestamp the end of all post-decision work or distinguish sleep overshoot from work between wakeup and acquisition.

The half-period guard prevented requesting the immediate boundary in three cases. Its subsequent full-period rounding also discarded the remainder of that slot. The `af9f1f9` in-slot correction uses the latest of the next slot boundary after the actual last occupied slot, half-period guard expiry, and work completion. This preserves one input per occupied slot and minimum spacing while allowing eligible acquisition inside the slot. The records establish that three target choices excluded an entire slot. They cannot guarantee how soon a changed runtime would actually execute. Actual late acquisitions and deadline misses still need independent gates.

## Candidate admission and output isolation

The opt-in profile checks 32 MiB of headroom in both physical free pages
and unreserved allocation pages immediately before spawning a candidate.
The staging path reads the counters off the periodic broker thread.
The current broker resolves its own executable with `_cmdname` and
spawns it as the owned child. The child caps its `RLIMIT_AS` using
`setrlimit`, denies and locks the ability to raise limits through
`procmgr_ability`, and replaces itself with the candidate through
`execv`. The same PID owns the native channel and keeps the cap
through promotion and commit. The existing
single candidate slot covers preparation and cleanup. This snapshot
creates no reservation, and the CPU workload depends on external
configuration. Follow the [guest profile setup](qnx-setup.md#candidate-admission-profile-for-the-qemu-guest) for the
exact launch command and the managed priority conditions.

### Reproduce the profiled lifecycle

After copying the current Release binaries, manifest and `tools/lifecycle_checks.sh`
to the guest, run the stock rollback and retry in a fresh directory:

```sh
run=/data/shadow-profile-rollback-$(date +%Y%m%d-%H%M%S)
sh /data/lifecycle_checks.sh --qnx-guest-profile /system/bin/shadowdeploy "$run"
```

Export the directory and run on the host:

```sh
python3 tools/verify_lifecycle.py "$exported_run" --qnx-guest-profile
```

This driver requests 240 acquisitions at 20/10/7.5 ms with 3 qualification
samples and 40 probation samples. The verifier checks the profile marker,
worker-loss rollback, promotion of the healthy retry,
baseline continuity, native side-channel snapshots and cleanup. The
profiled [extended cycle driver](cancel-retry-cycles.md#profiled-qnx-guest)
adds 20 cancel/retry generations and the 10,000-input cadence gate. It
uses broker selection telemetry. Separate receiver tests must timestamp
each authority record on the guest clock and check sequence, source and
arrival against its absolute deadline.

### Native admission failure before the counter-format fix

The first integrated Release run on `16c3a16` uncovered an actual
QNX counter format that its parser did not accept. The guest annotates
the hexadecimal page counters with a parenthesized size. The broker
reported invalid VM counters at ordinary headroom (about 869 MiB)
as well as under pressure (about 30.215 MiB). The memory fixture never
reached its worker launch, and the stock retry was rejected by the
same gate. The rollback controller stopped waiting for promotion.
The baseline still provided all 650 selections in each refusal test
and 240 in the rollback-control trace, with zero skipped slots and
zero decision misses in those three runs. The stock profile therefore
failed functional admission even though its native parser unit test
and native staging test returned zero.

Separate receiver probes in this same failed build observed the
following baseline-only service. Receiver time was compared against
each record's absolute guest deadline on the same guest monotonic clock.

| Condition | Receiver records | Late receipts | Skipped slots | Decision misses | Dropped telemetry |
| --- | ---: | ---: | ---: | ---: | ---: |
| Attempted CPU-candidate stage | 360 / 360 | 0 | 2 | 0 | 0 |
| Five-second telemetry reader stall | 360 / 360 | 0 | 2 | 0 | 169 |
| Combined CPU, memory and telemetry stall | 360 / 360 | 0 | 0 | 0 | 169 |

The combined smoke used two priority-5 loops and a bounded memory
holder with 384 MiB touched. The snapshot before broker launch
showed about 483 MiB of free and unreserved memory. The private
archive is `artifacts/qnx145-managed-1795787453/guest-data.tar`,
SHA-256 `7134208e950c0d73268c72e1f5f423e4d376d82c5a91edf24ad59ac152c5ae92`.

### Counter-format correction and native connection failure

On `c0e8deb` the new native-format parser test and the native staging
suite returned zero. The admission gate accepted the annotated VM
counters. With both counters at about 30.36 MiB during the low-memory
trial, the broker reported `insufficient memory headroom`. It kept the
same baseline PID and side-channel set and selected all 650 outputs.
After restoring headroom, the stock retry reached native IPC launch
and failed at `ConnectAttach`. The stock rollback controller also
stopped waiting for promotion because the first candidate's connection
failed. The managed probes left six orphan candidate workers visible
in the post-combined process snapshot. This run establishes admission
refusal and baseline isolation under pressure and records failed retry,
promotion and candidate cleanup.

The over-cap fixture observed a 16,777,216-byte address-space cap and
`ENOMEM` after 14 MiB of allocations. It exited before the worker
handshake and the broker selected all 650 baseline outputs. Its stock
retry likewise failed at native connection setup. The CPU fixture
reported soft and hard limits of 16,777,216 bytes before its connection
failed. The address-space probe and IPC admission outcomes are separate.
The first endurance controller for the rejected admission path was
stopped. Its overlay and controller status remain in
`artifacts/qnx145-final-cycles-1795787620/`. It produced no exported
qualification result.

A four-way launcher probe compared the shell-visible launcher PID with
the executable PID. Bare execution and `on -L` reported the same PID.
Adding the `on -A` ability-lock option produced a different executable
PID both alone and with `-L`. The broker attached IPC using the PID it
owned, so this launcher behavior explains the failed connection and
worker ownership loss. The diagnostic archive is
`artifacts/qnx145-launchdiag-1795788067/guest-data.tar`, SHA-256
`9b1a591a46b83d865358f9a95de695cba038c06f49d0799ceb2a9744c8835ff3`.

| Condition | Receiver records | Late receipts | Skipped slots | Decision misses | Dropped telemetry |
| --- | ---: | ---: | ---: | ---: | ---: |
| Attempted CPU-candidate stage | 360 / 360 | 0 | 2 | 0 | 0 |
| Five-second telemetry reader stall | 360 / 360 | 0 | 1 | 0 | 170 |
| Combined CPU, memory and telemetry stall | 360 / 360 | 0 | 0 | 0 | 169 |

All the received authority records in these probes came from the
baseline. The archive is `artifacts/qnx145-managed-1795787859/guest-data.tar`,
SHA-256 `43806566b41ca68628ef1c7067830a2812a0d6eb5d0eb74f06ea5afdf026069a`.
The Release broker SHA-256 is
`f10c9193e71237c5755c714dc24656133c16935ef5f4900efbb8d37837961f12`.

### Same-PID launcher validation, 2026-11-28

The `53cf1fa` Release broker passed the native parser and staging tests.
The public profiled lifecycle driver and verifier also passed. The
stock candidate promoted at sample 31, the broker rolled back to the
same baseline at sample 40 after that candidate was killed, and a healthy
second candidate committed at sample 90. All 240 inputs had selections,
with zero skipped slots and zero deadline misses. Maximum decision time
was 1.612 ms. The side-channel checks observed one baseline peer, two
peers in probation, one baseline peer after rollback, and one candidate
peer after commit. The retired child and connection observations passed.

The sub-threshold test held physical and unreserved memory at about
30.17 MiB when the broker refused admission. It then released the
holder, staged the stock worker again and committed at sample 134.
The over-cap fixture measured 16,777,216 bytes of address space and
`ENOMEM` after 14 MiB of allocations. Its stock retry committed at
sample 70. The same baseline served through each refusal and until
its intended retirement. Both runs selected 650/650 outputs with
zero skipped slots and zero deadline misses. The after-run process
snapshots for all five managed probes showed no newly remaining broker,
worker, load holder or receiver process.

The fixed 360-input receiver probes used an independent QNX monotonic
receiver. Each row had 360 contiguous records and zero skipped slots,
decision misses, or records arriving after the respective deadlines.

| Condition | Baseline / candidate receipts | Latest receipt relative to deadline | Dropped telemetry | Final phase |
| --- | ---: | ---: | ---: | --- |
| Candidate with 2 ms of thread CPU work per sample | 360 / 0 | 4.649 ms early | 0 | Shadowing |
| Stock candidate, telemetry reader stalled for 5 s | 64 / 296 | 6.018 ms early | 171 | Committed |
| CPU candidate, two background loops, 384 MiB held, and 5 s stall | 109 / 251 | 6.521 ms early | 170 | Committed |

The CPU fixture observed the 16 MiB soft and hard cap. During the
combined probe `pidin` observed the two busy loops and memory holder at
priority 5 round-robin and the broker, baseline and active candidate at
priority 10 round-robin. Free and unreserved memory were about 476 MiB
in the active snapshot. Telemetry losses and the authority receipt
counts describe separate streams. The CPU-only fixture showed baseline
service under shadow load and remained in shadowing at the end of its
finite run.

The run archive is `artifacts/qnx145-managed-1795829295/guest-data.tar`,
SHA-256 `0235cee74dbc649f3b5ed2d51d23e41c30e7480a175c6a8404be57703633e529`.
The archived `qnx145-managed.sh` configures FIFO receivers and bounded
fixtures. See `artifacts/qnx145-reproduction/` for their source and
harness. These smoke runs characterize the selected guest conditions.


## Managed long-run checks, 2026-11-28

The exploratory workload matrix used the `53cf1fa` Release binaries
and one fresh profiled broker per arm. Each arm requested 10,000 inputs
and 20 cancel/retry generations followed by a stock commit. A dedicated
FIFO delivered authority records to a receiver that timestamped each
record with the guest monotonic clock. The telemetry reader kept consuming records through all 20
cancellation generations and the final commit. Each arm began with a
stock stage-and-commit preflight at the same timing settings. For the
stall and combined arms it stopped reading for five seconds just after
it observed commit. Both streams were exported independently. Guest CPU
load used two priority-5 round-robin loops. The memory arm used a bounded
384 MiB holder. The combined arm ran both loads and the reader stall.

The public driver and verifier are documented in the [extended cycle
procedure](cancel-retry-cycles.md#profiled-qnx-guest). The archived
controller adds the authority and telemetry FIFOs and host-side collection
for the receiver experiment. Receiver checks compare actual sequences,
source counts and monotonic arrival to the per-record absolute deadline.
Keep output-read evidence separate from the broker's pipe write count.
If deliberate telemetry loss removes sample rows, the strict lifecycle
verifier can reject the incomplete telemetry independently of the
receiver result.

| Arm | Acquired / requested | On-time receipts | Skipped slots | Decision misses | Telemetry drops | Strict verifier result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Idle | 10,000 / 10,000 | 10,000 | 0 | 0 | 0 | Generation 18 cancelled in probation |
| Managed CPU | 10,000 / 10,000 | 10,000 | 1 | 0 | 0 | Generation 17 cancelled in probation |
| Memory | 10,000 / 10,000 | 10,000 | 11 | 0 | 0 | Cadence failure |
| Stalled telemetry | 3,668 / 10,000 | 3,667 | 1 | 1 | 169 | Broker failed closed |
| Combined | 10,000 / 10,000 | 10,000 | 0 | 0 | 169 | Incomplete telemetry |
| Idle repeat | 10,000 / 10,000 | 10,000 | 0 | 0 | 0 | Generation 19 cancelled in probation |

Each arm reached the generation 21 commit. The driver sent cancel
commands after observing a live shadow sample. In the three ordering
failures, a candidate entered probation before the command took effect,
so the broker rolled back to the baseline. The verifier requires a
shadow-phase rejection for those 20 generations and correctly rejected
these traces for that intended coverage. The original driver and verifier
outcomes remain in the archive. The 120 captured post-cancel descriptor
maps matched their initial maps, and the after-run process lists showed
no surviving recorded broker or retired worker PIDs.

Every received record was on time and each arm's receipts were contiguous
through its last delivered input. The stalled arm withheld the decision
on input 3,668 after 16.018 ms of decision time. The remaining 6,332 inputs
in that arm were unprocessed. Across all six arms, the independent
receivers observed 53,667 on-time records out of 53,668 acquisitions.
This preserves the distinction between one acquired input withheld for a
miss and the unprocessed tail.

Decision percentiles use only the captured decision samples. The stalled
and combined traces omit 169 telemetry records each. Receipt percentiles
use all independent receiver records.

| Arm | Observed decision p50 / p95 / p99 / max (ms) | Acquisition-to-receipt p50 / p95 / p99 / max (ms) | Max receiver interval (ms) |
| --- | ---: | ---: | ---: |
| Idle | 0.127 / 0.318 / 0.539 / 1.766 | 0.447 / 0.733 / 1.123 / 5.777 | 29.721 |
| Managed CPU | 0.110 / 0.291 / 0.587 / 3.541 | 0.388 / 0.709 / 1.282 / 5.870 | 45.670 |
| Memory | 0.121 / 0.271 / 0.497 / 6.391 | 0.427 / 0.681 / 1.017 / 7.011 | 236.347 |
| Stalled telemetry | 0.141 / 0.392 / 0.671 / 16.018 | 0.486 / 0.812 / 1.192 / 2.529 | 29.106 |
| Combined | 0.135 / 0.357 / 0.668 / 3.474 | 0.459 / 0.827 / 1.336 / 4.175 | 32.853 |
| Idle repeat | 0.125 / 0.308 / 0.486 / 2.474 | 0.437 / 0.713 / 1.050 / 3.640 | 29.196 |

The 13 empty slots occurred after commit. The CPU case targeted the next
slot and acquired 25.692 ms after that target. The memory case had a
239.205 ms acquisition gap. Its next target was 219.152 ms after the
immediately following slot boundary, with another 4.476 ms from target
to acquisition. The stalled case targeted the next slot, acquired
22.803 ms after the target, and then missed its independent decision
deadline. The evidence narrows the timing intervals. It leaves the
underlying causes of post-selection delay and late acquisitions open.

The archive is `artifacts/qnx145-final-cycles-1795829427/guest-data.tar`,
SHA-256 `4459429fb2d51de7dc3a9d738072a36d3bf5e8fb0d3bc459c66f4aca9ffd9a17`.
Its `checked-summary.txt` and `checked-slots.txt` siblings give the parsed
results. Recompute them with the archived analysis scripts under
`artifacts/qnx145-reproduction/guest/` after extracting the archive. Keep
this six-arm diagnostic series separate from the five-per-condition
qualification matrix.

### Targeted repeats and cancellation trigger

A repeat with the two managed CPU loops delivered 10,000 consecutive
receipts with zero late arrivals, missed decisions or skipped slots.
Decision p50/p95/p99/max was 0.114/0.312/0.661/3.581 ms and the latest
receipt arrived 4.057 ms after acquisition. Archive SHA-256
`86b475d8609f4cc58610389d7bde43bd275645a4712b6f5ea0f4d44528855cec`.
A diagnostic-only 384 MiB memory repeat selected 10,000/10,000 outputs
with no skips or deadline misses and a 28.040 ms maximum selected
interval. It did not reproduce the earlier 239.205 ms acquisition gap.
Archive SHA-256
`72b1e9b3928985a9bce925b96d6e7b6953707b994e7298264da69b37240eb7f4`.
The two passes preserve the original CPU and memory failure records.

The updated cancellation driver looks for the stage, replay completion
and first valid live shadow sample in one bounded scan. It writes cancel
before PID parsing, FD snapshots or retirement observations. The original
20 cancellations, 20-sample qualification and 40-sample probation still
apply. The verifier still requires every cancellation to take effect before
promotion. External scheduling can still delay the command.

An early trigger prototype completed 20 verified prepromotion
cancellations and committed generation 21 at sample 357. All 20
post-cancel descriptor maps matched the initial map and all 23 recorded
broker and worker PIDs were absent afterward. The independent receiver
observed 10,000/10,000 consecutive on-time outputs, with receipt
p50/p95/p99/max of 0.371/0.601/0.847/5.599 ms and a 126.022 ms maximum
interval. Two post-commit gaps left seven empty slots, so the unchanged
strict cadence gate failed. The prototype source SHA-256 was
`202ff1a67164fb98f30c88ca6d5d03fdf4c733d94f44ab2d2a836b01f153140a`.
The tracked `fbd2874` revision removes the preliminary stage scan before
cancellation and reads the same replay boundary by name. The prototype
archive is `artifacts/qnx145-early-cancel-1795832129/guest-data.tar`,
SHA-256 `c4edc264ba27273e32e3b838cb6323936030f89918ae95f95da41679fcfbb9ab`.

The exact tracked driver also ran the public 640-input guest command
with regular-file stdout. The native functional verifier passed all 20
prepromotion cancellations, generation 21 committed at input 394, and
all workers retired. All 640 inputs had a selected output and no decision
miss. The maximum decision took 1.577 ms and the maximum selected interval
was 40.465 ms. One skipped slot failed the strict cadence gate. This
regular-file run did not measure independent receiver arrival. Driver
SHA-256 `df47309a280a70dddb41a7f5515d724c782b08cca9294524b6d33f1a0df83b1a`.
Archive `artifacts/qnx145-public640-1795834382/guest-data.tar`, SHA-256
`e82472cb1c3e87e4dcd49b8128910b53e75d396a6986926a167f4b03b70b41dc`.

## Reviewed deferred retirement design

The authority pipe enforces a bounded write and supports baseline-first
publication while a candidate is shadowing. The selected guest probes
observed on-time authority receipts even when the independent telemetry
reader stalled. Worker disconnect, rollback and commit still trigger
synchronous retirement on the periodic broker thread after publication
has been handled. Join, teardown and reaping can delay its next acquisition.

The reviewed design would transfer retired workers to a preallocated
reaper queue following authority revocation. The two total charged slots
include queued workers, the worker being reaped and slots reserved for
still-live workers. Admission must preserve capacity for simultaneous stop
of the baseline and candidate. Each charge persists until dispatch, the
native connection and child are released. Saturation or overdue reaping
would refuse candidate admission without waiting on the authority thread. A
watchdog would report the overdue worker and keep its owner responsible.
Shutdown must drain the queue or report an unresolved owned worker.
Hardware budgets, watchdog thresholds and recovery behavior require
confirmation on the provisioned target. This design review carries no
implementation or repeatability verdict.

## Host and native check boundaries

Host Release build and all 13 CTest suites passed for `16c3a16` with
loopback and Unix sockets available. The focused parser test also passed
after `c0e8deb` using sanitized native counter-format cases. Five focused
host Debug tests passed on `53cf1fa` (policy, protocol, guest-headroom,
staging and process integration). A separate sanitizer build with
`SHADOW_SANITIZERS=ON` passed all 13 host suites on the same revision.
Its log is `artifacts/issues-1-4-5/host-asan-53cf1fa.log`. The host tests cover deterministic
grid boundaries and admission-counter parsing and thresholds, among other
functional checks. Native staging and guest runs provide separate
target-OS evidence with the observed emulator configuration.
