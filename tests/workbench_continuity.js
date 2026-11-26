// Exercise the presentation logic with fresh synthetic broker rows.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) {
      elements.set(id, {addEventListener() {}, replaceChildren() {}, textContent: "", value: "promote"});
    }
    return elements.get(id);
  }
};
const context = vm.createContext({
  document, requestAnimationFrame() {},
  ResizeObserver: class { observe() {} }
});
const evaluate = source => vm.runInContext(source, context);
const shown = id => document.getElementById(id).textContent;
evaluate(fs.readFileSync(require("node:path").join(__dirname, "../web/app.js"), "utf8"));
evaluate(`reset(); accept({type: "meta", schema: 1, backend: "host-posix-pipes"}, true);
accept({type: "summary", samples: 1, outputs: 1}, true);`);
assert.equal(shown("continuity-counts"), "Unavailable");
assert.equal(shown("continuity-gap"), "—");
evaluate(`reset(); accept({type: "meta", schema: 1, backend: "qnx-native-ipc", requested_inputs: 8}, false, {source: "board"});
accept({type: "sample", sequence: 4, time_s: .4, truth: 10, output: 10, decision_latency_ms: 1,
    release_lateness_ms: 2, nominal_slot: 6, nominal_grid_lateness_ms: 4, acquisition_interval_ms: 100});
accept({type: "summary", samples: 4, acquired_inputs: 4, requested_inputs: 8, selected_outputs: 3,
    missing_selected_outputs: 1, skipped_nominal_slots: 2, initial_unoccupied_slots: 0, unprocessed_inputs: 4,
    max_selected_interval_ms: 120, effective_selected_rate_hz: 10, max_acquisition_interval_ms: 130,
    max_scheduling_lateness_ms: 5, max_nominal_grid_lateness_ms: 8, decision_deadline_misses: 0}, false, {source: "board"});`);
assert.match(shown("continuity-counts"), /4 \/ 8 acquired.*3 selected.*1 missing.*4 unprocessed/);
assert.equal(shown("continuity-gap"), "120.00 ms / 10.00 Hz");
assert.equal(shown("continuity-acquisition"), "100.00 ms / 130.00 ms");
assert.equal(shown("continuity-scheduling"), "2.00 ms / 5.00 ms");
assert.equal(shown("continuity-nominal"), "4.00 ms / 8.00 ms");
assert.equal(shown("continuity-slots"), "2 / 0");
assert.equal(shown("outputs"), 3);
evaluate(`const terminal = records[records.length - 1];
reset(); accept({type: "meta", schema: 1, backend: "qnx-native-ipc", requested_inputs: 8},
    false, {source: "board"});
accept(terminal, false, {source: "board"});`);
assert.equal(shown("continuity-acquisition"), "— / 130.00 ms");
