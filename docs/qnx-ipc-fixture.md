# Native QNX IPC fixture

The fixture runs the production `WorkerProcess` against a test-only receiver
with separate FIFO gates before `MsgReceive` and `MsgReply`. The receiver uses
`ChannelCreate(0)` and the production binary Hello, Sample, and Estimate
layouts. It is built only for QNX with `BUILD_TESTING` enabled.

## Run

Build `qnx-ipc-tests` and `qnx-ipc-receiver` for the same architecture with
the QNX SDK. For a guest with `pidin` in PATH, copy both executables into a
writable working directory on a filesystem that supports FIFOs. Run each
configuration there and preserve both run directories:

```sh
./qnx-ipc-tests ./qnx-ipc-receiver 7500000 > fixture-7500000.stdout 2> fixture-7500000.stderr
printf '%s\n' "$?" > fixture-7500000.exit
./qnx-ipc-tests ./qnx-ipc-receiver 112500000 > fixture-112500000.stdout 2> fixture-112500000.stderr
printf '%s\n' "$?" > fixture-112500000.exit
```

Record the exact source revision, dirty patch hash if applicable, and both
binary SHA-256 hashes on the build host before transfer. Preserve this manifest
with the original output and verify the transferred and exported artifacts.
The harness records the guest's `uname` identity and the effective budget.
The registered CTest cases use the same executable and arguments. The default
state observer uses QNX procfs. Append `pidin` to either command to run the
process-spawn observer as a comparison. Preserve the observer name with the
results.

Each configuration runs two fresh attempts per case. Each attempt has a
12-second outer watchdog that kills the attempt's process group if needed.
The run directory retains command and receiver events, snapshot and result
timestamps, cleanup records, and `pidin` descriptor snapshots. With the
`pidin` observer it also retains the raw thread snapshots and their stderr. The receiver writes each
event to stderr before sending it on the control FIFO, so events survive observation failures. The `receiver_event`
kind values are 0 for ready, 1 for sample, 2 for reply, 3 for receive timeout,
4 for receive error and 5 for probe ready. Gate commands are `R` for receive,
`Y` for reply and `P` for the bounded receive probe. A per-attempt setup
failure is recorded with its directory and verdict before continuing.

## Interpret

| Case | Before the trigger | Required result |
| --- | --- | --- |
| SEND release | Sender observed in SEND with receive gate closed | Matching estimate before the deadline |
| SEND exit | Sender observed in SEND with receive gate closed | Disconnected by the deadline after receiver termination |
| SEND timeout | Sender observed in SEND with receive gate closed | Timeout and no sample when receive is subsequently probed |
| REPLY release | Sender observed in REPLY with reply gate closed | Matching estimate before the deadline |
| REPLY exit | Sender observed in REPLY with reply gate closed and receiver recorded the matching sample and positive receive ID | Disconnected by the deadline after receiver termination |
| REPLY timeout and late reply | Sender observed in REPLY with reply gate closed | Timeout, followed by a recorded late reply attempt |

Before submitting the sample, the harness opens its own `/proc/<pid>/ctl`
read-only and identifies the worker's dispatch thread as the attempt's sole
non-main thread. The procfs observer queries that exact thread with
`DCMD_PROC_TIDSTATUS`. It requires the harness PID, dispatch TID, expected
kernel wait state, and receiver PID, channel, and connection ID to match
before the absolute exchange deadline. The comparison observer uses the
dispatch TID, receiver PID, and wait state reported by `pidin`. If the observer
cannot establish the state, the expected API timeout alone leaves the attempt inconclusive. An
exit attempt that returns timeout cannot demonstrate disconnection within the
budget. A release started within the budget that fails to produce its valid
result is a failure. The late-reply result and errno are evidence, and the
failed transport must reject the later sample independently of that result.

For a failed exchange the harness expects a different sequence to return
`unavailable` by a fresh deadline. Any first receive on a surviving receiver
is accounted for even when REPLY observation was inconclusive. The
subsequent receive probe must time out, with its recorded receive interval
covering the full submission call and asynchronous completion of the
second sequence. The harness checks
descriptor counts, the original side-channel ID, child reaping and child
absence after each worker is destroyed. A separate worker runs a recovery
exchange with a distinct sequence and a one-second fixture budget.

Exit code 0 means every attempt passed. Exit code 77 means at least one
attempt was inconclusive and none failed. Exit code 1 means at least one
attempt or the watchdog failed. Preserve every attempt and use its verdict
when reporting coverage. The fixture measures transport waits and receiver
termination. Live deployment cancellation, hardware deadlines and worst-case
latency require separate evidence.

The state and connection fields follow the QNX documentation.
[DCMD_PROC_TIDSTATUS](https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.prog/topic/process_DCMD_PROC_TIDSTATUS.html)
[Thread information](https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.prog/topic/process_Thread_info.html)
[pidin output fields](https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.utilities/topic/p/pidin.html)
[ConnectServerInfo enumeration](https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.lib_ref/topic/c/connectserverinfo.html)
