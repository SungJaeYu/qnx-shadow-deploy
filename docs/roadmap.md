# Implementation roadmap

## Milestone 1: Executable policy and process runtime

- [x] Define an explicit local protocol and output contract.
- [x] Run baseline and candidate in separate processes.
- [x] Bound each transport exchange and reject late or mismatched responses.
- [x] Promote at an input boundary and retain a warm probation fallback.
- [x] Fail closed when no valid output or continuous transition exists.
- [x] Provide real-process failure scenarios, a workbench and exportable evidence.
- [x] Add host tests and sanitizer configuration.
- [ ] Compile and validate the native QNX path with SDP 8.0.6.

## Milestone 2: Deploy into an already-running service

- Keep the baseline running while attaching a separately built candidate artifact.
- Define an artifact manifest for architecture, protocol version, entrypoint and contract.
- Add bounded replay or an application-defined snapshot for candidate state preparation.
- Separate candidate staging, qualification, promotion and cancellation commands.
- Make the selected output channel independent of stdout telemetry.

Acceptance: attach a candidate after the baseline has processed live input, show rejection or promotion without restarting the baseline, and preserve original evidence for each decision.

## Milestone 3: Target isolation and competition evidence

- Measure QNX timing with declared thread priorities, resource assumptions and controlled load.
- Add resource admission limits; abort shadow execution if it harms the active path.
- Exercise connection failure, repeated deployment cycles and process cleanup on the target.
- Produce the short English target demonstration and final reproducible setup guide.

Acceptance: the repository's target support claims match captured QNX execution, and the public video demonstrates the final behavior.

## Explicitly outside the first competition implementation

Full OS/firmware OTA, fleet management, hardware actuator integration, arbitrary state migration, malicious-code containment, distributed consensus, certification, and rollback of external side effects.
