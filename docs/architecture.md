# Architecture and behavior contract

## Runtime boundary

The MVP starts one baseline and one candidate process. Each has private filter state. A broker owns the input sequence, the deployment state machine, and the only selected output stream. Workers return estimates and have no output consumer connection in this example.

```mermaid
flowchart TD
    I["Synthetic sensor stream"] --> B["Broker and supervisor"]
    B --> V1["Baseline process"]
    B --> V2["Candidate process"]
    V1 --> B
    V2 --> B
    B --> O["Selected output and JSONL evidence"]
    O --> U["Host workbench"]
```

This is logical authority separation among cooperative programs. It is not a security sandbox: the current implementation does not configure filesystem/device/network permissions or resource quotas for untrusted workers.

## Sample and output contracts

All local IPC peers must use the same ABI, architecture, and protocol version. The binary structs are not a network serialization format. Compile-time size checks and runtime header checks reject known layout/version mismatches; heterogeneous targets need explicit serialization in a future protocol.

| Message | Size | Fields |
| --- | --- | --- |
| Header | 8 bytes | Signature, protocol version, message kind |
| Hello | 16 bytes | Header, QNX channel ID or host sentinel, reserved word |
| Sample | 32 bytes | Header, sequence, monotonic acquisition timestamp, position in meters |
| Estimate | 40 bytes | Header, echoed sequence/timestamp, position in meters, velocity in meters/second |

The broker supplies sequence and acquisition time. Workers cannot establish freshness by stamping old data with a new completion time. The parent records receipt completion using `CLOCK_MONOTONIC` on the same machine. Transport completion includes dispatch-thread scheduling and IPC, not just algorithm execution.

The default contract is defined in `Contract`:

- Absolute position at most 1,000 m; absolute velocity at most 100 m/s.
- Finite numeric values and no more than a 5 m position change per sample.
- Matching sequence and acquisition timestamp.
- Completion within the decision budget; IPC gets an earlier cutoff at 75% of that budget.
- Consecutive qualifying candidate samples before promotion, followed by a bounded probation window.

These example thresholds are not a general correctness oracle. Each real application must define units, tolerances, freshness and transition constraints. Candidate-baseline difference is observable, but equality is not a pass condition.

## State and authority

| Phase | Selected output | Transition |
| --- | --- | --- |
| `shadowing` | Baseline | Reject on the first candidate violation; qualify after N valid samples |
| `probation` | Candidate | Enter at the next sequence boundary; keep baseline warm |
| `committed` | Candidate | Enter after M valid probation samples; retire baseline |
| `rejected` | Baseline | Retire candidate; continue baseline if valid |
| `rolled_back` | Baseline | Use this sample's valid baseline result and retire candidate |
| `failed` | None | Stop output; no automatic restart in this MVP |

With qualification N=20, samples 1–20 use the baseline and sample 21 can use the candidate. The promotion sample is the first probation sample. With M=40, a healthy candidate commits on sample 60.

On candidate failure during probation, rollback requires a valid baseline estimate for the **current** input. The selected stream's continuity check also applies to rollback. If neither output is valid, standby protection is lost during probation, or the switch would violate continuity, output stops. An unqualified candidate never replaces a failed baseline.

Candidate errors after commit stop output because the baseline has been retired. Rejection and rollback are terminal deployment outcomes for one run; attaching another candidate is a future feature.

## Process and transport ownership

`WorkerProcess` owns its child PID, IPC resources and one persistent dispatch thread. The input slot allows at most one outstanding sample. Both workers receive a sample before the broker waits for either result. A timed-out or malformed transport is marked unusable, preventing late or partial replies from being reused.

Children are created with `posix_spawn`. Pipe descriptors are close-on-exec. A startup handshake has a three-second bound. Retiring a child closes its descriptors, terminates that owned child, and reaps it. The sample worker must not spawn descendants; production process-group isolation is not implemented in the C++ runtime.

| Build | Data path | Timeout |
| --- | --- | --- |
| QNX | Worker `ChannelCreate(0)`, parent `ConnectAttach`, `MsgSend`, `MsgReceive`, `MsgReply` | `TimerTimeout(CLOCK_MONOTONIC, SEND | REPLY | TIMER_ABSTIME, ...)` immediately before `MsgSend` |
| Linux/macOS | Nonblocking pipes and complete fixed-size transfers | `poll` plus a shared absolute monotonic deadline |

QNX uses a side-channel connection to avoid descriptor collisions. The worker intentionally does **not** enable `_NTO_CHF_UNBLOCK`, because that flag would require the server to handle unblock pulses before a timed-out sender can return. Startup still uses a pipe to exchange the channel identifier.

Native QNX code has been checked against official API documentation but has not been compiled against proprietary headers or exercised on a QNX kernel. Review/build is not runtime evidence.

## Timing and state preparation

The synthetic source follows a wall-clock schedule and skips missed releases instead of bursting delayed samples. Acquisition, IPC receipt, and decision latency are separate concepts. If selection itself misses the decision budget, the broker stops output. JSON rendering, pipe consumption and browser display happen afterward and are **not** included in that decision latency. A slow stdout consumer can delay future releases; this telemetry path is not suitable for a production hard real-time output channel.

The example filters start together and retain private state across samples. Qualification therefore includes initial state preparation. There is no arbitrary memory transfer or process checkpointing. Starting a candidate later needs a bounded replay window or an explicit application snapshot contract before it can be added safely.

CPU scheduling and memory contention remain shared. Two processes do not automatically provide deadline isolation. Target priorities, admission control, load testing, resource policy and an independent telemetry path are explicit remaining gates.

## Technical references

- [QNX MsgSend](https://qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.lib_ref/topic/m/msgsend.html)
- [QNX MsgReceive](https://qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.lib_ref/topic/m/msgreceive.html)
- [QNX message metadata](https://qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.lib_ref/topic/m/_msg_info.html)
- [QNX TimerTimeout, including unblock behavior](https://qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.lib_ref/topic/t/timertimeout.html)
- [QNX process creation](https://qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.sys_arch/topic/proc_posix_spawn.html)
