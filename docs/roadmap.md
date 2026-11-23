# Implementation roadmap

## Milestone 1: Executable policy and process runtime

- [x] Define an explicit local protocol and output contract.
- [x] Run baseline and candidate in separate processes.
- [x] Bound each transport exchange and reject late or mismatched responses.
- [x] Promote at an input boundary and retain a warm probation fallback.
- [x] Fail closed when no valid output or continuous transition exists.
- [x] Provide real-process failure scenarios, a workbench and exportable evidence.
- [x] Add host tests and sanitizer configuration.
- [x] Cross-compile all five executables with the installed QNX SDK.
- [x] Boot the existing image and validate native policy/staging, eleven functional scenarios and live deployment.
- [x] Inventory 260 SDK packages and match five guest runtime build IDs to installed assets.
- [ ] Confirm the complete 8.0.6 release package set; component matching alone is insufficient.

## Milestone 2: Deploy into an already-running service

- [x] Keep the baseline running while attaching a separately built local candidate artifact.
- [x] Validate an artifact manifest for OS/architecture, protocol, entrypoint and contract.
- [x] Add bounded input replay for the example filter; reject gaps, overflow and invalid replay.
- [x] Add local stage/cancel controls, deployment generations and retry after rejection/rollback.
- [x] Keep historical replay separate from fresh qualification and automatic promotion.
- [ ] Add an optional operator approval gate before promotion if required by the final workflow.
- [ ] Make the selected output channel independent of stdout telemetry and shadow waits.

Acceptance: attach a candidate after the baseline has processed live input, show rejection or promotion without restarting the baseline, and preserve original evidence for each decision.

## Milestone 3: Target isolation and competition evidence

- Measure QNX timing with declared thread priorities, resource assumptions and controlled load.
- Add resource admission limits; abort shadow execution if it harms the active path.
- [x] Exercise native timeout/worker exit, cancellation/retry, six replay rejections and connection/process cleanup in QEMU.
- [ ] Repeat under controlled target load, isolate SEND-blocked timeout behavior, and address default 10 ms budget failures without weakening acceptance rules.
- Produce the short English target demonstration and final reproducible setup guide.

Acceptance: the repository's target support claims match captured QNX execution, and the public video demonstrates the final behavior.

The [2026-11-23 native execution](verification.md#native-qnx-execution-2026-11-23) used the existing image and unchanged runtime code. Functional scenarios passed at the repository's existing 100/75 ms settings; live functional runs used 200/150 ms. Five default-budget scenario runs stopped early on deadline misses. A successful default-budget live run does not establish timing robustness. Original failures, native traces and connection snapshots are retained.

## Explicitly outside the first competition implementation

Full OS/firmware OTA, fleet management, hardware actuator integration, arbitrary state migration, malicious-code containment, distributed consensus, certification, and rollback of external side effects.
