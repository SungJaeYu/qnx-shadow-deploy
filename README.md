# QNX ShadowDeploy

ShadowDeploy gives a new sensor-processing application the same inputs as the running baseline. A QNX broker checks the candidate's outputs and response times before switching output authority at an input sequence boundary. During probation, the broker keeps the baseline available for fallback. The example uses a synthetic position stream.

## Evaluator overview

- **Host.** The local workbench runs the compiled broker and separate worker processes over POSIX pipes. The recorded crash-and-retry run selected all 90 outputs and skipped one nominal slot. [Host lifecycle record](docs/evidence/live-lifecycle-2026-11-26.md#host-functional-observation)
- **QNX x86-64 in QEMU.** Native IPC validation passed the policy and staging checks, live deployment, and all 11 functional scenarios. The 11 scenarios used a 100 ms period and a 75 ms decision budget. At the default 20 ms period, 10 ms budget, and 7.5 ms IPC cutoff, five scenarios stopped early on deadline failures. [Original verification record](docs/verification.md#native-qnx-execution-2026-11-23)
- **Raspberry Pi 4B (2 GB).** QNX AArch64 application artifacts were cross-built. BSP image provisioning, execution on the physical board, and hardware timing measurements are pending. [Pi qualification procedure](docs/qnx-pi4-qualification.md)

On November 26, the QNX guest crash-and-retry run selected 90 of 90 outputs with zero decision deadline misses and one skipped nominal slot. [Lifecycle evidence](docs/evidence/live-lifecycle-2026-11-26.md)

[Play the 44-second English demonstration](docs/native-qnx-demo.mp4) or see [trace provenance and release gates](docs/evaluator.md). The video presents selected decisions from archived native QNX guest runs.

![ShadowDeploy workbench during a host run](docs/workbench.jpg)

<details>
<summary>November 26 QNX guest results</summary>

- **Crash, rollback, and retry.** The same broker rolled back to its baseline after the promoted candidate crashed, then staged and committed a fresh candidate. The run used a 200 ms period and a 150 ms decision budget. The strict timing check failed on one skipped nominal slot. A separate protocol v2 check accepted all 4 canonical replies and rejected all 73 noncanonical replies in its test matrix. [Lifecycle and protocol evidence](docs/evidence/live-lifecycle-2026-11-26.md)
- **Cancellation and timing.** The broker accepted operator cancellation at a sample boundary and selected the baseline on that sample. With the candidate enabled, at the default 20 ms period and 10 ms decision budget, three runs without deliberate load completed, and three runs with two guest busy loops stopped at the first decision. The separate IPC fixture passed 12 of 12 attempts with a 112.5 ms cutoff. All 12 attempts at 7.5 ms were inconclusive. [Cancellation and timing evidence](docs/evidence/qnx-native-cancel-2026-11-26.md)

</details>

## Run the host demo

On Linux or macOS, use CMake 3.20+, a C++17 compiler, and Python 3.9+.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build --parallel 2
python3 tools/serve.py
```

Open <http://127.0.0.1:8765>. Select **Attach a candidate to a running baseline** and click **Run demonstration**. The broker attaches the candidate after 20 inputs. Watch **Deployment decisions** for replay completion, promotion, and commit. **Output owner** switches to the candidate during probation, and the baseline retires at commit. Inspect **Missing outputs** and save the trace with **Download JSONL**.

<details>
<summary>Operator-controlled staging and cancellation</summary>

```sh
./build/shadow-demo --worker ./build/shadow-worker --control-stdin \
  --samples 1000000 > deployment.jsonl
```

After 16 inputs, enter `stage ./build/candidate.manifest` on stdin. Enter `cancel` during replay, qualification, or probation. After cleanup, enter `stage` with the manifest again.

</details>

[Architecture](docs/architecture.md) · [QNX setup](docs/qnx-setup.md) · [Sensor replay and telemetry](docs/preboard-pipeline.md) · [Demo script](docs/demo.md) · [Roadmap](docs/roadmap.md)

Development was assisted by OpenAI Codex. The project makes no certification claim and is independent of QNX. QNX is a trademark of its respective owner. SDK use is subject to QNX's terms. [MIT license](LICENSE)
