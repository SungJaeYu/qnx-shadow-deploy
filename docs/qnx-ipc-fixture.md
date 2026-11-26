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
The registered CTest cases use the same executable and arguments.

Each configuration runs two fresh attempts per case. Each attempt has a
12-second outer watchdog that kills the attempt's process group if needed.
The raw `pidin` output, stderr, command and receiver events, snapshot
timestamps, result timestamps and cleanup records are retained under the run
directory. The receiver writes each event to stderr before sending it on the
control FIFO, so events survive observation failures. The `receiver_event`
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

The state parser requires the harness PID, a non-main thread ID, the named
kernel wait state and that attempt's receiver PID in a snapshot accepted
before the absolute exchange deadline. If the observer cannot establish the
state, the expected API timeout alone leaves the attempt inconclusive. An
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
[pidin output fields](https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.utilities/topic/p/pidin.html)
[ConnectServerInfo enumeration](https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.lib_ref/topic/c/connectserverinfo.html)
