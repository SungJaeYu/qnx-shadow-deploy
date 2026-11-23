"use strict";
const $ = id => document.getElementById(id);
const descriptions = {
  rollback: "A healthy candidate takes over, then exits during probation. The warm baseline supplies the current result.",
  "reject-units": "The candidate returns millimeters as meters. Its output is rejected before it gains authority.",
  "reject-delay": "The candidate stalls. A bounded IPC wait expires and the baseline remains selected.",
  promote: "Both versions receive the same samples. Qualification and probation pass before the baseline is retired.",
  "reject-nan": "A NaN cannot become a published sensor result.",
  "reject-stale": "A response for the wrong input sequence is rejected.",
  "reject-protocol": "The candidate responds with an incompatible protocol version.",
  "reject-switch": "Both streams are individually valid, but switching between them would create a 50-meter jump.",
  "no-fallback": "Both processes exit during probation. The broker stops publishing because no valid result remains.",
  "post-commit-failure": "The candidate fails after the standby has been retired. The broker stops; it cannot roll back."
};
let records = [], samples = [], running = false, count = 0, missing = 0;
let redrawPending = false;
const pretty = value => String(value || "").replaceAll("_", " ");
function requestDraw() {
  if (!redrawPending) { redrawPending = true; requestAnimationFrame(() => { redrawPending = false; draw(); }); }
}
function reset() {
  records = []; samples = []; count = 0; missing = 0;
  $("events").replaceChildren(); $("error").hidden = true; $("empty").hidden = false;
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
  sequence.className = "seq"; sequence.textContent = `#${row.sequence}`;
  text.textContent = pretty(row.event); reason.textContent = pretty(row.reason);
  text.append(reason); item.append(sequence, text); $("events").prepend(item);
}
function accept(row, replay = false) {
  if (!row || typeof row !== "object") throw new Error("Invalid recording row");
  records.push(row);
  if (row.type === "meta") {
    if (row.schema !== 1 || !["host-posix-pipes", "qnx-native-ipc"].includes(row.backend)) throw new Error("Unsupported recording schema or backend");
    $("backend").textContent = `${replay ? "Recorded" : "Live"} ${row.backend === "qnx-native-ipc" ? "QNX native IPC" : "host POSIX process run"} · baseline PID ${row.baseline_pid} · candidate PID ${row.candidate_pid}`;
    $("deadline").textContent = `${row.deadline_ms} ms`; $("qualification").textContent = `${row.qualification_samples} samples`;
    $("probation").textContent = `${row.probation_samples} samples`;
  } else if (row.type === "sample") {
    if (!Number.isFinite(row.sequence) || !Number.isFinite(row.time_s) || !Number.isFinite(row.truth)) throw new Error("Invalid sample record");
    if (samples.length && row.sequence <= samples[samples.length - 1].sequence) throw new Error("Recording sequences must increase");
    samples.push(row); $("empty").hidden = true;
    if (row.output === null) ++missing; else ++count;
    $("outputs").textContent = count; $("missing").textContent = missing;
    $("owner").textContent = pretty(row.source); $("latency").textContent = `${Number(row.decision_latency_ms).toFixed(2)} ms`;
    $("sample-count").textContent = `${samples.length} samples`;
    $("phase").textContent = pretty(row.phase);
    $("phase").className = `phase ${row.phase === "failed" ? "bad" : ["rejected", "rolled_back"].includes(row.phase) ? "warn" : row.phase === "committed" ? "good" : ""}`;
    const roles = {shadowing: ["Active", "Shadow"], probation: ["Standby", "Active"], committed: ["Retired", "Active"], rejected: ["Active", "Rejected"], rolled_back: ["Active", "Retired"], failed: ["Stopped", "Stopped"]};
    const pair = roles[row.phase] || ["Unknown", "Unknown"];
    $("baseline-role").textContent = pair[0]; $("candidate-role").textContent = pair[1];
    $("baseline-check").textContent = pretty(row.baseline_check); $("candidate-check").textContent = pretty(row.candidate_check);
    if (row.event) addEvent(row);
    requestDraw();
  } else if (row.type === "error") throw new Error(row.message);
  else if (row.type !== "summary") throw new Error("Unknown record type");
}
async function run() {
  if (running) return;
  running = true; $("run").disabled = true; $("recording").disabled = true; $("scenario").disabled = true;
  reset(); $("connection").textContent = "Demonstration running";
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
  finally { running = false; $("run").disabled = false; $("recording").disabled = false; $("scenario").disabled = false; $("download").disabled = !records.length; }
}
function draw() {
  const canvas = $("chart"), box = canvas.getBoundingClientRect(), scale = window.devicePixelRatio || 1;
  canvas.width = box.width * scale; canvas.height = box.height * scale;
  const ctx = canvas.getContext("2d"); ctx.scale(scale, scale);
  const w = box.width, h = box.height, left = 50, top = 24, right = w - 20, bottom = h - 35;
  const shown = samples.slice(-240);
  let lo = shown.length ? Math.min(...shown.map(r => r.truth)) - 0.4 : 9.5;
  let hi = shown.length ? Math.max(...shown.map(r => r.truth)) + 0.4 : 11.5;
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
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash); ctx.beginPath(); let connected = false;
    for (const row of shown) {
      const v = get(row); if (v === null || !Number.isFinite(v) || v < lo || v > hi) { connected = false; continue; }
      if (connected) ctx.lineTo(x(row.time_s), y(v)); else ctx.moveTo(x(row.time_s), y(v)); connected = true;
    }
    ctx.stroke(); ctx.setLineDash([]);
  }
  line(r => r.truth, "#91a1ad", 1.5, []);
  line(r => r.candidate?.position ?? null, "#b96913", 2, [5, 4]);
  line(r => r.output, "#176b89", 2.5, []);
  for (const row of shown.filter(r => r.event)) {
    const px = x(row.time_s); ctx.strokeStyle = "#a8bdca"; ctx.setLineDash([2, 4]); ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, bottom); ctx.stroke(); ctx.setLineDash([]);
  }
}
$("scenario").addEventListener("change", () => { $("description").textContent = descriptions[$("scenario").value]; });
$("description").textContent = descriptions[$("scenario").value];
$("run").addEventListener("click", run);
$("download").addEventListener("click", () => {
  const blob = new Blob([records.map(row => JSON.stringify(row)).join("\n") + "\n"], {type: "application/x-ndjson"});
  const url = URL.createObjectURL(blob), link = document.createElement("a"); link.href = url; link.download = `shadow-deploy-${records[0]?.scenario || "run"}.jsonl`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$("recording").addEventListener("change", async event => {
  const file = event.target.files[0]; if (!file || running) return;
  reset();
  try {
    if (file.size > 10 * 1024 * 1024) throw new Error("Recordings must be smaller than 10 MB.");
    const rows = (await file.text()).trim().split("\n").map(line => JSON.parse(line));
    if (rows[0]?.type !== "meta" || rows[rows.length - 1]?.type !== "summary") throw new Error("Recording requires metadata and a final summary.");
    for (const row of rows) accept(row, true);
    $("connection").textContent = "Recorded run loaded"; $("download").disabled = false;
  } catch (error) { failure(error.message); $("connection").textContent = "Invalid recording"; }
});
new ResizeObserver(requestDraw).observe($("chart"));
