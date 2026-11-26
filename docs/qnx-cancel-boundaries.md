# Native operator cancellation at IPC and handoff boundaries

## QNX x86-64 guest result

The `668a70c` fixture passed both cases with the stock broker. The first
attempt on an earlier revision expired its worker gate before the helper
recorded a command write. A second attempt stopped during FIFO identity
validation. Both failed captures were retained. The passing helper writes
through the shell's already-open control FIFO on standard output and records
its timestamps in a separate file.

| Gated case | FIFO write | Cancellation accepted | Generation 2 committed | Selected |
| --- | --- | ---: | ---: | ---: |
| Live request 22 | 36.830 to 36.522 ms before the 337.5 ms cutoff | 23 | 37 | 44/44 |
| Replay handoff on request 17 | 0.103 ms duration | 18 | 32 | 44/44 |

The verifier checked zero missing outputs, current baseline authority at
cancellation, restored connection descriptors and absence of the owned
processes after exit. In the handoff case it also checked that the observed
sample preceded transfer and that transfer preceded acceptance. The gated
worker identifies one request unambiguously. The passing trace archive's
SHA-256 is `287c770d126d75b19b2578f6e8ef4a7ef468c363672239cc04bbe1084a22046c`.

## Reproduce

Run the boundary cases on QNX with `shadow-demo`, `shadow-worker`,
`shadow-candidate`, `candidate.manifest`, `qnx-cancel-replay-worker` and
`qnx_cancel_ordering_checks.sh` from the same build revision. The fixture
uses the existing 44-input configuration, 500 ms period, 450 ms decision
deadline and 337.5 ms IPC budget. The broker records native phase timing
on standard error after the run.

```sh
SHADOW_QNX_TARGET_DIR=/system/bin/shadowdeploy \
SHADOW_QNX_TRACE_DIR=/data/shadow-cancel-boundaries \
sh /system/bin/shadowdeploy/qnx_cancel_ordering_checks.sh boundary
```

Export the entire fresh trace directory and verify it on the host.

```sh
python3 tools/verify_qnx_cancel_ordering.py FRESH_TRACE_DIR --boundary
```

- `live-near` gates the candidate reply for live input 22. The native
  helper targets the FIFO write 300 ms after acquisition. A passing
  bracket places the whole write within the final 40 ms of the IPC
  budget, shows the matching native exchange completing within that
  budget, and shows the command accepted at the subsequent input
  boundary. The preceding input still uses the probation candidate.
- `handoff-gated` gates the 17th replay request, the first acquired input
  appended to the standard 16-sample history. The helper writes
  `cancel` before releasing that reply. A passing bracket requires
  successful replay transfer and operator cancellation acceptance at the
  next input boundary, in that order. The baseline remains
  authoritative throughout generation 1.

The driver starts the FIFO writer before staging and connects its
standard output to the already-open control FIFO. The helper validates
that descriptor and makes a single atomic command write. It records
the surrounding monotonic timestamps separately in `.write`. The
driver records the writer status in `.write.exit` and captures
diagnostics in `.write.stderr`. The worker matches its receipt against
the release token before replying. The broker's `cancel_requested` event records
acceptance and `candidate_cancelled` records the policy decision.

Both cases also require complete output, baseline authority at
cancellation, a fresh generation 2 replay, qualification, probation and
commit, and retirement of owned processes and connections. Verifier
exit 77 means the requested ordering could not be established. A
captured deadline, behavior, integrity or cleanup failure takes
precedence and exits 1. Ordinary early writes and cancellation while
still warming can produce an inconclusive ordering verdict alongside
passing behavior and cleanup. Guest results establish functional
ordering for the observed execution.

The host controls create all fixtures in temporary directories.

```sh
python3 tests/qnx_cancel_ordering_verifier.py
```

For an instrumented guest retry, the driver can run from a fresh writable
directory selected by `SHADOW_QNX_TARGET_DIR`. Copy the four existing
broker, worker, candidate and manifest files there from the original
image, then transfer the updated `qnx-cancel-replay-worker` and driver
to that directory. Keep the original image and first attempt intact.
Record the source revision and host/guest checksums of the reused and
replaced files in the retry inventory.
