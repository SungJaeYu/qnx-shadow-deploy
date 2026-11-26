"use strict";
const $ = id => document.getElementById(id);
const descriptions = {
  "live-deployment": "The baseline starts alone. After 20 inputs, a separate candidate artifact replays recent history and qualifies on new live inputs before taking over.",
  rollback: "A healthy candidate takes over, then exits during probation. The warm baseline supplies the current result.",
  "reject-units": "The candidate returns millimeters as meters. Its output is rejected before it gains authority.",
  "reject-delay": "The candidate stalls. A bounded IPC wait expires and the baseline remains selected.",
  promote: "Both versions receive the same samples. Qualification and probation pass before the baseline is retired.",
  "reject-nan": "A NaN cannot become a published sensor result.",
  "reject-stale": "A response for the wrong input sequence is rejected.",
  "reject-protocol": "The candidate responds with an incompatible protocol version.",
  "reject-switch": "Both streams produce valid estimates. The broker keeps the baseline because the switch would exceed the 5-meter selected-output continuity limit.",
  "no-fallback": "Both processes exit during probation. The broker stops publishing because no valid result remains.",
  "post-commit-failure": "The candidate fails after the standby has been retired. The broker stops; it cannot roll back."
};
let records = [], samples = [], running = false, count = 0, missing = 0;
let redrawPending = false, watchingBoard = false;
const pretty = value => String(value || "").replaceAll("_", " ");
function requestDraw() {
  if (!redrawPending) { redrawPending = true; requestAnimationFrame(() => { redrawPending = false; draw(); }); }
}
const formatMetric = (value, unit) => Number.isFinite(value) ? `${value.toFixed(2)} ${unit}` : "—";
function showContinuity(latest, summary) {
  const hasSummary = Number.isFinite(summary?.requested_inputs);
  $("continuity-counts").textContent = hasSummary
    ? `${summary.acquired_inputs} / ${summary.requested_inputs} acquired · ${summary.selected_outputs} selected · ${summary.missing_selected_outputs} missing · ${summary.unprocessed_inputs} unprocessed`
    : Number.isFinite(latest?.nominal_slot) ? "Run totals pending summary" : "Unavailable";
  const interval = (last, max) => Number.isFinite(last) || Number.isFinite(max)
    ? `${formatMetric(last, "ms")} / ${formatMetric(max, "ms")}` : "—";
  $("continuity-acquisition").textContent = interval(latest?.acquisition_interval_ms, summary?.max_acquisition_interval_ms);
  $("continuity-scheduling").textContent = interval(
    Number.isFinite(latest?.nominal_slot) ? latest.release_lateness_ms : null, summary?.max_scheduling_lateness_ms);
  $("continuity-nominal").textContent = interval(latest?.nominal_grid_lateness_ms, summary?.max_nominal_grid_lateness_ms);
  $("continuity-gap").textContent = Number.isFinite(summary?.max_selected_interval_ms)
    ? `${formatMetric(summary.max_selected_interval_ms, "ms")} / ${formatMetric(summary.effective_selected_rate_hz, "Hz")}` : "—";
  $("continuity-slots").textContent = hasSummary
    ? `${summary.skipped_nominal_slots} / ${summary.initial_unoccupied_slots}` : "—";
  $("continuity-misses").textContent = hasSummary ? summary.decision_deadline_misses : "—";
}
function labelOutputs(scope = "") {
  $("outputs-label").textContent = `Selected outputs${scope}`;
  $("missing-label").textContent = `Missing outputs${scope}`;
}
function reset() {
  records = []; samples = []; count = 0; missing = 0;
  showContinuity(); labelOutputs();
  $("events").replaceChildren(); $("error").hidden = true; $("empty").hidden = false;
  $("reference-label").textContent = "Reference";
  $("backend").textContent = "Waiting for run metadata";
  $("phase").textContent = "Starting"; $("phase").className = "phase";
  $("owner").textContent = "—"; $("outputs").textContent = "0"; $("missing").textContent = "0";
  $("latency").textContent = "—"; $("sample-count").textContent = "0 samples";
  $("baseline-role").textContent = "Starting"; $("candidate-role").textContent = "Starting";
  $("baseline-check").textContent = "Waiting for a response"; $("candidate-check").textContent = "Waiting for a response";
  $("download").disabled = true; requestDraw();
}
function failure(message) { $("error").hidden = false; $("error").textContent = message; }
function addEvent(row) {
  const item = document.createElement("li"), sequence = document.createElement("span"), text = document.createElement("div"), reason = document.createElement("small");
  sequence.className = "seq"; sequence.textContent = row.type === "deployment" ? `after #${row.after_sequence}` : `#${row.sequence}`;
  text.textContent = pretty(row.event); reason.textContent = pretty(row.reason);
  text.append(reason); item.append(sequence, text); $("events").prepend(item);
}
function accept(row, replay = false, board = null) {
  if (!row || typeof row !== "object") throw new Error("Invalid recording row");
  records.push(row);
  if (row.type === "meta") {
    if (![1, 2].includes(row.schema) || !["host-posix-pipes", "qnx-native-ipc"].includes(row.backend)) throw new Error("Unsupported recording schema or backend");
    $("backend").textContent = `${board ? board.source : replay ? "Recorded" : "Live"} ${row.backend === "qnx-native-ipc" ? "QNX native IPC" : "host POSIX process run"} · baseline PID ${row.baseline_pid} · ${row.candidate_pid > 0 ? `candidate PID ${row.candidate_pid}` : "candidate not attached"}`;
    if (row.input_source === "sensor-trace-mm") {
      $("reference-label").textContent = "Sensor input";
      $("backend").textContent += " · sensor trace";
    }
    if (board) labelOutputs(" (relay window)");
    $("deadline").textContent = `${row.deadline_ms} ms`; $("qualification").textContent = `${row.qualification_samples} samples`;
    $("probation").textContent = `${row.probation_samples} samples`;
  } else if (row.type === "sample") {
    if (!Number.isFinite(row.sequence) || !Number.isFinite(row.time_s) || (row.truth !== null && !Number.isFinite(row.truth))) throw new Error("Invalid sample record");
    if (samples.length && row.sequence <= samples[samples.length - 1].sequence) throw new Error("Recording sequences must increase");
    samples.push(row); $("empty").hidden = true;
    showContinuity(row);
    if (row.output === null) ++missing; else ++count;
    $("outputs").textContent = count; $("missing").textContent = missing;
    $("owner").textContent = pretty(row.source); $("latency").textContent = `${Number(row.decision_latency_ms).toFixed(2)} ms`;
    $("sample-count").textContent = `${samples.length} samples`;
    $("phase").textContent = pretty(row.phase);
    $("phase").className = `phase ${row.phase === "failed" ? "bad" : ["rejected", "rolled_back"].includes(row.phase) ? "warn" : row.phase === "committed" ? "good" : ""}`;
    const roles = {idle: ["Active", "Not attached"], warming: ["Active", "Preparing state"], shadowing: ["Active", "Shadow"], probation: ["Standby", "Active"], committed: ["Retired", "Active"], rejected: ["Active", "Rejected"], rolled_back: ["Active", "Retired"], failed: ["Stopped", "Stopped"]};
    const pair = roles[row.phase] || ["Unknown", "Unknown"];
    $("baseline-role").textContent = pair[0]; $("candidate-role").textContent = pair[1];
    $("baseline-check").textContent = pretty(row.baseline_check); $("candidate-check").textContent = pretty(row.candidate_check);
    if (row.event) addEvent(row);
    requestDraw();
  } else if (row.type === "deployment") {
    addEvent(row);
    if (row.candidate_pid > 0) {
      const meta = records[0];
      $("backend").textContent = `${board ? board.source : replay ? "Recorded" : "Live"} ${meta.backend === "qnx-native-ipc" ? "QNX native IPC" : "host POSIX process run"} · baseline PID ${meta.baseline_pid} · candidate PID ${row.candidate_pid} · generation ${row.generation}`;
    }
  } else if (row.type === "summary") {
    const latest = samples[samples.length - 1];
    showContinuity(board && latest?.sequence !== row.samples ? null : latest, row);
    const totalSelected = row.selected_outputs ?? row.outputs;
    const totalMissing = row.missing_selected_outputs ?? row.missing_outputs;
    if (Number.isFinite(totalSelected) && Number.isFinite(totalMissing)) {
      $("outputs").textContent = totalSelected;
      $("missing").textContent = totalMissing;
      if (board) labelOutputs(" (broker total)");
    }
  } else if (row.type === "error") throw new Error(row.message);
  else throw new Error("Unknown record type");
}
async function run() {
  if (running || watchingBoard) return;
  running = true; $("watch-board").disabled = true; $("run").disabled = true; $("recording").disabled = true; $("scenario").disabled = true;
  reset(); $("board-status").hidden = true; $("connection").textContent = "Demonstration running";
  try {
    const response = await fetch("/api/run", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({scenario: $("scenario").value})});
    if (!response.ok) throw new Error(`Run could not start (HTTP ${response.status}). Check the server and build output.`);
    const reader = response.body.getReader(), decoder = new TextDecoder(); let pending = "";
    for (;;) {
      const {value, done} = await reader.read();
      pending += decoder.decode(value, {stream: !done});
      const lines = pending.split("\n"); pending = lines.pop();
      for (const line of lines) if (line.trim()) accept(JSON.parse(line));
      if (done) break;
    }
    if (pending.trim()) accept(JSON.parse(pending));
    if (records[records.length - 1]?.type !== "summary") throw new Error("The run ended without a summary. Evidence is incomplete.");
    $("connection").textContent = "Run complete";
  } catch (error) { failure(error.message); $("connection").textContent = "Run interrupted"; }
  finally { running = false; $("watch-board").disabled = false; $("run").disabled = false; $("recording").disabled = false; $("scenario").disabled = false; $("download").disabled = !records.length; }
}
async function watchBoard() {
  const button = $("watch-board"), status = $("board-status");
  if (watchingBoard) {
    watchingBoard = false;
    button.disabled = true;
    return;
  }
  if (running) return;
  watchingBoard = true;
  button.textContent = "Stop watching board";
  $("run").disabled = true; $("recording").disabled = true;
  reset();
  status.hidden = false;
  status.textContent = "Waiting for the board relay";
  try {
    let previous = "";
    while (watchingBoard) {
      const response = await fetch("/api/board", {cache: "no-store"});
      if (!watchingBoard) break;
      if (!response.ok) {
        if (response.status === 503) {
          await new Promise(resolve => setTimeout(resolve, 1000));
          continue;
        }
        throw new Error("Start the workbench with --board-stdin to view board telemetry.");
      }
      const board = await response.json();
      if (!watchingBoard) break;
      const signature = JSON.stringify(board);
      if (signature !== previous) {
        previous = signature;
        status.textContent = `${board.source} · upstream ${board.upstream} · ${board.sequence_gaps} missing relay sample frames · ${board.evicted} older rows evicted · ${board.malformed} malformed · ${board.oversize} oversized`;
        $("connection").textContent = board.complete ? "Board run complete" : board.upstream === "connected" ? "Board stream connected" : "Board stream disconnected";
        reset();
        if (board.meta) {
          accept(board.meta, false, board);
          for (const row of board.rows) accept(row, false, board);
          const id = board.meta.producer_run_id || board.run_id;
          $("backend").textContent += ` · run ${id}`;
          $("download").disabled = !board.complete || board.evicted > 0 || board.sequence_gaps > 0;
        }
      }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  } catch (error) {
    failure(error.message);
    $("connection").textContent = "Board view unavailable";
  } finally {
    watchingBoard = false;
    button.textContent = "Watch board stream";
    button.disabled = false;
    $("run").disabled = false; $("recording").disabled = false;
  }
}
function draw() {
  const canvas = $("chart"), box = canvas.getBoundingClientRect(), scale = window.devicePixelRatio || 1;
  canvas.width = box.width * scale; canvas.height = box.height * scale;
  const ctx = canvas.getContext("2d"); ctx.scale(scale, scale);
  const w = box.width, h = box.height, left = 50, top = 24, right = w - 20, bottom = h - 35;
  const shown = samples.slice(-240);
  let lo = shown.length ? Math.min(...shown.map(r => r.truth ?? r.input)) - 0.4 : 9.5;
  let hi = shown.length ? Math.max(...shown.map(r => r.truth ?? r.input)) + 0.4 : 11.5;
  if (hi - lo < 1) { lo -= .3; hi += .3; }
  const t0 = shown[0]?.time_s || 0, t1 = Math.max(t0 + .5, shown[shown.length - 1]?.time_s || 4);
  const x = t => left + (t - t0) / (t1 - t0) * (right - left), y = v => bottom - (v - lo) / (hi - lo) * (bottom - top);
  ctx.font = "11px Arial"; ctx.fillStyle = "#526b7e"; ctx.strokeStyle = "#e2ebf1"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; ++i) {
    const v = lo + (hi - lo) * i / 4, py = y(v); ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(right, py); ctx.stroke();
    ctx.fillText(v.toFixed(1), 12, py + 4);
    const t = t0 + (t1 - t0) * i / 4; ctx.fillText(`${t.toFixed(1)}s`, x(t) - 10, h - 12);
  }
  ctx.fillText("m", 14, 15);
  function line(get, color, width, dash) {
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash); ctx.beginPath(); let connected = false, previousSequence = null;
    for (const row of shown) {
      if (previousSequence !== null && row.sequence !== previousSequence + 1) connected = false;
      previousSequence = row.sequence;
      const v = get(row); if (v === null || !Number.isFinite(v) || v < lo || v > hi) { connected = false; continue; }
      if (connected) ctx.lineTo(x(row.time_s), y(v)); else ctx.moveTo(x(row.time_s), y(v)); connected = true;
    }
    ctx.stroke(); ctx.setLineDash([]);
  }
  line(r => r.truth ?? r.input, "#91a1ad", 1.5, []);
  line(r => r.candidate?.position ?? null, "#b96913", 2, [5, 4]);
  line(r => r.output, "#176b89", 2.5, []);
  for (const row of shown.filter(r => r.event)) {
    const px = x(row.time_s); ctx.strokeStyle = "#a8bdca"; ctx.setLineDash([2, 4]); ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, bottom); ctx.stroke(); ctx.setLineDash([]);
  }
}
$("scenario").addEventListener("change", () => { $("description").textContent = descriptions[$("scenario").value]; });
$("description").textContent = descriptions[$("scenario").value];
$("run").addEventListener("click", run);
$("watch-board").addEventListener("click", watchBoard);
$("download").addEventListener("click", () => {
  const blob = new Blob([records.map(row => JSON.stringify(row)).join("\n") + "\n"], {type: "application/x-ndjson"});
  const url = URL.createObjectURL(blob), link = document.createElement("a"); link.href = url; link.download = `shadow-deploy-${records[0]?.scenario || "run"}.jsonl`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$("recording").addEventListener("change", async event => {
  const file = event.target.files[0]; if (!file || running || watchingBoard) return;
  reset(); $("board-status").hidden = true;
  try {
    if (file.size > 10 * 1024 * 1024) throw new Error("Recordings must be smaller than 10 MB.");
    const rows = (await file.text()).trim().split("\n").map(line => JSON.parse(line));
    if (rows[0]?.type !== "meta" || rows[rows.length - 1]?.type !== "summary") throw new Error("Recording requires metadata and a final summary.");
    for (const row of rows) accept(row, true);
    $("connection").textContent = "Recorded run loaded"; $("download").disabled = false;
  } catch (error) { failure(error.message); $("connection").textContent = "Invalid recording"; }
});
new ResizeObserver(requestDraw).observe($("chart"));
