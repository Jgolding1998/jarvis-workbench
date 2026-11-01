"use strict";
let token = "";
let exiting = false;
let statusInstance = "";
let statusSequence = 0;
let pollAbort = null;
let pollError = false;
let pointerNext = null;
let pointerAbort = null;
let pointerGeneration = 0;
/* Freshness. A snapshot is current only when it was accepted (same instance, newer sequence)
   recently. Losing the connection never means Jarvis stopped: the view becomes "last known"
   and readings become unknown, but nothing claims the guard is off or disarmed. */
const DELAYED_MS = 2500;
const LOST_MS = 6000;
let received = false;
let receivedAt = 0;
let pollFailing = false;
let connection = "connecting";
let failure = "";
let latest = null;
let lastKnown = {title: "", armed: "", hand: ""};
let diagnosticsReport = null;
/* Elapsed time uses a monotonic clock so wall-clock changes cannot extend or reset freshness.
   Browsers always provide performance.now(); the fallback only serves the Node VM test harnesses. */
const monotonic = () => (typeof performance === "object" && performance && typeof performance.now === "function") ? performance.now() : Date.now();
/* Bootstrap: the session request is bounded and retried. Until the first accepted snapshot the page
   knows nothing about mode, guard, actions or hand control, so those stay unknown and locked. */
const BOOT_MS = 8000;
const RETRY_MS = 3000;
let bootAbort = null;
let bootStartedAt = 0;
let bootFailedAt = null;
const $ = (id) => document.getElementById(id);
const isStopped = (s) => Boolean(s) && !s.active && !s.lock_armed && !s.sheet_armed && Boolean(s.hand) && s.hand.mode === "off" && !s.hand.move_cursor;
/* Controls that start, arm or change something wait for a fresh snapshot. Stop is never locked here. */
function lockControls() {
  for (const id of ["start", "arm", "practice", "hand-arm", "hand-voice", "hand-cursor", "command", "command-run"]) $(id).disabled = true;
  document.querySelectorAll('input[name="action"], [data-gesture], [data-people]').forEach((control) => { control.disabled = true; });
}
async function send(path, data, signal) {
  if (exiting) return;
  const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json", "X-Jarvis-Token": token}, body: JSON.stringify(data), signal});
  const result = await response.json();
  if (signal && signal.aborted) return;
  if (!response.ok) throw new Error(result.error || "Request failed");
  $("error").hidden = true;
  if (!exiting) render(result);
  return result;
}
function showError(error) { if (exiting) return; pollError = false; $("error").textContent = error.message; $("error").hidden = false; }
function endSession(title, note) {
  exiting = true;
  clearInterval(pollTimer);
  if (pollAbort) pollAbort.abort();
  cancelPointer();
  token = "";
  $("state").textContent = title;
  $("state-note").textContent = note;
  $("orb").classList.remove("running");
  $("pad").classList.remove("live");
  $("virtual-cursor").hidden = true;
  $("hand-label").textContent = "OFF";
  $("armed-label").textContent = "DISARMED";
  $("error").hidden = true;
  setConnection("ended", "Session ended");
  document.querySelectorAll("button, input").forEach((control) => { control.disabled = true; });
}
function setConnection(state, text) {
  connection = state;
  $("overview").dataset.connection = state;
  $("connection").dataset.state = state;
  $("connection-text").textContent = text;
}
const ago = (ms) => { const seconds = Math.max(0, Math.round(ms / 1000)); return seconds < 2 ? "just now" : seconds + " s ago"; };
function updateFreshness() {
  if (exiting) return;
  const age = received ? monotonic() - receivedAt : 0;
  const next = !received ? (pollFailing ? "lost" : "connecting") : (pollFailing || age > LOST_MS) ? "lost" : age > DELAYED_MS ? "delayed" : "live";
  $("retry-connect").hidden = received || !pollFailing;
  if (next === "connecting") { setConnection(next, "Connecting to Jarvis"); return; }
  if (next === "live") {
    setConnection(next, "Live, updated " + ago(age));
    $("readings-note").textContent = (latest && latest.demo ? "Simulated readings" : "Latest local status") + ", updated " + ago(age);
    return;
  }
  $("orb").classList.toggle("running", false);
  lockControls();
  $("start-label").textContent = "Waiting for fresh status";
  if (next === "delayed") {
    setConnection(next, "Delayed, last update " + ago(age));
    $("readings-note").textContent = "Delayed: last update " + ago(age) + ". Values may be out of date.";
    return;
  }
  setConnection(next, received ? "Connection lost, last update " + ago(age) : "Cannot reach Jarvis");
  if (!received) {
    $("state").textContent = "Not connected";
    $("start-label").textContent = "Waiting for status";
    $("state-note").textContent = "No status has arrived yet, so this page cannot tell whether Jarvis is running, observing or armed. Retrying automatically; check that Jarvis is still running.";
    $("readings-note").textContent = "Unknown: no status received yet.";
    $("armed-label").textContent = "UNKNOWN";
    $("hand-label").textContent = "UNKNOWN";
    $("demo-controls").hidden = true;
    return;
  }
  if ($("stop-status").dataset.kind === "ok") $("stop-status").hidden = true;
  $("state").textContent = "Status unknown";
  $("state-note").textContent = "Last update " + ago(age) + " showed: " + lastKnown.title + ". Jarvis may still be running with its last settings; this page cannot confirm it. Stop stays available.";
  $("readings-note").textContent = "Unknown: no fresh reading for " + Math.round(age / 1000) + " s.";
  $("people").textContent = "?";
  $("absence").textContent = "-";
  $("camera").textContent = "Unknown";
  $("armed-label").textContent = "LAST KNOWN: " + lastKnown.armed;
  $("hand-label").textContent = "LAST KNOWN: " + lastKnown.hand;
}
function render(state) {
  if (exiting) return;
  if (typeof state.instance !== "string" || !/^[0-9a-f]{16}$/.test(state.instance)
      || !Number.isSafeInteger(state.sequence) || state.sequence < 1) throw new Error("Session unavailable");
  if (statusInstance && statusInstance !== state.instance) {
    endSession("Jarvis restarted", "Reload this tab to connect to the new local session.");
    setConnection("ended", "Reload required");
    failure = "restart";
    $("armed-label").textContent = "RELOAD REQUIRED";
    $("hand-label").textContent = "UNKNOWN";
    $("people").textContent = "?";
    $("camera").textContent = "Unknown";
    return;
  }
  statusInstance = state.instance;
  if (state.sequence <= statusSequence) return;
  statusSequence = state.sequence;
  latest = state;
  received = true;
  receivedAt = monotonic();
  pollFailing = false;
  const hand = state.hand || {};
  $("mode").textContent = hand.allowed ? (hand.mode === "watching" ? "LOCAL HAND CAMERA" : "LOCAL HARDWARE READY") : state.demo ? "SAFE DEMO" : "LOCAL CAMERA";
  $("state").textContent = state.active ? (state.action_error ? "Action failed" : state.camera === "fault" ? "Camera unavailable" : "Guard active") : "Standby";
  $("state-note").textContent = state.action_error || (state.active ? (state.established ? "Presence established. Policy is evaluating local observations." : "Waiting to establish your presence in the camera view.") : "Not observing. Choose Start observing when you are ready.");
  $("action-error").textContent = state.action_error || "";
  $("action-error").hidden = !state.action_error;
  $("orb").classList.toggle("running", state.active);
  // A Stop confirmation describes one moment; retire it once Jarvis reports anything not fully off,
  // including hand practice.
  if (!isStopped(state) && $("stop-status").dataset.kind === "ok") $("stop-status").hidden = true;
  $("arm").disabled = false;
  $("practice").disabled = false;
  $("command").disabled = $("command-run").disabled = false;
  $("retry-connect").hidden = true;
  document.querySelectorAll('input[name="action"], [data-gesture], [data-people]').forEach((control) => { control.disabled = false; });
  $("start").disabled = Boolean(state.active) || hand.mode === "watching";
  $("start-label").textContent = state.active ? "Observing" : state.demo ? "Start observing" : "Start observing with camera";
  $("start-hint").textContent = state.demo
    ? "Simulated: no camera, microphone, or desktop action is used. Starting never arms an action."
    : hand.mode === "watching" ? "Hand control owns the camera. Stop it before starting the presence guard."
    : "Uses the camera you enabled when launching Jarvis. Starting never arms an action, moves the cursor, or changes a permission.";
  $("people").textContent = state.people === null ? "?" : state.people;
  $("absence").textContent = Number(state.absent_seconds).toFixed(1);
  $("camera").textContent = {fault:"Fault",receiving:"Receiving",waiting:"Waiting"}[state.camera];
  $("voice").textContent = "Voice: " + state.voice;
  $("uptime").textContent = state.uptime + "s uptime";
  $("armed-label").textContent = state.lock_armed ? "PRIVACY LOCK ARMED" : state.sheet_armed ? "SHEET DEMO ARMED" : "DISARMED";
  $("lock-option").disabled = !state.allow_lock;
  $("sheet-option").disabled = !state.allow_sheet;
  $("demo-controls").hidden = !state.demo || hand.mode === "watching";
  renderHand(hand);
  lastKnown = {title: $("state").textContent, armed: $("armed-label").textContent, hand: $("hand-label").textContent};
  const selected = state.lock_armed ? "lock" : state.sheet_armed ? "sheet" : "none";
  if (!document.querySelector('input[name="action"]:focus')) {
    document.querySelectorAll('input[name="action"]').forEach((input) => { if (!input.dataset.dirty) input.checked = input.value === selected; });
  }
  $("events").replaceChildren();
  for (const event of state.events) {
    const li = document.createElement("li");
    const time = document.createElement("time");
    time.textContent = "+" + event.at.toFixed(1) + "s";
    li.append(time, document.createTextNode(event.message));
    $("events").append(li);
  }
  if (!state.events.length) { const li = document.createElement("li"); li.textContent = "No actions yet. Activity stays in memory."; $("events").append(li); }
  updateFreshness();
}
$("command-form").addEventListener("submit", (event) => { event.preventDefault(); send("/api/command", {command: $("command").value}).catch(showError); });
$("start").addEventListener("click", () => send("/api/command", {command: "active"}).catch(showError));
function stopStatus(kind, message) {
  $("stop-status").dataset.kind = kind;
  $("stop-status").textContent = message;
  $("stop-status").hidden = false;
}
$("stop").addEventListener("click", () => {
  document.querySelectorAll('input[name="action"]').forEach((input) => delete input.dataset.dirty);
  stopStatus("pending", "Sending Stop to Jarvis.");
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 8000);
  send("/api/command", {command: "stop"}, controller.signal).then((response) => {
    if (exiting) return;
    // Confirm from Jarvis's own answer to this Stop, from the current instance only.
    if (timedOut || !response || response.instance !== statusInstance || !Number.isSafeInteger(response.sequence)) throw new Error("No confirmation");
    if (!isStopped(response)) {
      stopStatus("bad", "Jarvis answered but still reports something running. Press Stop again.");
    } else if (latest && latest.sequence > response.sequence && !isStopped(latest)) {
      stopStatus("pending", "Jarvis confirmed Stop, but a newer update shows something running again.");
    } else {
      stopStatus("ok", "Stop confirmed by Jarvis: the guard is off, actions are disarmed, and hand control is stopped.");
    }
  }).catch((error) => {
    if (exiting) return;
    failure = timedOut ? "stop timeout" : "stop not confirmed";
    stopStatus("bad", (timedOut ? "Stop timed out. " : "Stop was not confirmed. ") +
      "Jarvis may still be running. Try again, or close Jarvis from the window that started it. Keep your operating system's auto-lock enabled as your primary protection.");
    if (!timedOut && error.message !== "No confirmation" && !(error instanceof TypeError)) showError(error);
  }).finally(() => clearTimeout(timer));
});
document.querySelectorAll('input[name="action"]').forEach((input) => input.addEventListener("change", () => { document.querySelectorAll('input[name="action"]').forEach((radio) => radio.dataset.dirty = "true"); }));
$("arm").addEventListener("click", () => {
  const action = document.querySelector('input[name="action"]:checked').value;
  document.querySelectorAll('input[name="action"]').forEach((input) => delete input.dataset.dirty);
  send("/api/arm", {lock:action === "lock",sheet:action === "sheet"}).catch(showError);
});
document.querySelectorAll("[data-people]").forEach((button) => button.addEventListener("click", () => {
  send("/api/demo", {people: button.dataset.people === "fault" ? null : Number(button.dataset.people)}).then(() => {
    document.querySelectorAll("[data-people]").forEach((choice) => choice.classList.toggle("selected", choice === button));
  }).catch(showError);
}));
function renderHand(hand) {
  if (hand.mode !== "practice") cancelPointer();
  $("hand-label").textContent = {off: "OFF", practice: "PRACTICE", watching: "WATCHING"}[hand.mode] + (hand.move_cursor ? " / CURSOR ARMED" : "");
  $("practice").textContent = hand.mode === "practice" ? "Stop practice" : "Start practice";
  $("pad").classList.toggle("live", hand.mode === "practice");
  $("pad-hint").hidden = hand.mode === "practice";
  $("virtual-cursor").hidden = !hand.cursor;
  if (hand.cursor) { $("virtual-cursor").style.left = (hand.cursor.x * 100) + "%"; $("virtual-cursor").style.top = (hand.cursor.y * 100) + "%"; }
  $("hand-voice").disabled = $("hand-cursor").disabled = $("hand-arm").disabled = !hand.allowed;
  if (!document.activeElement || !document.activeElement.closest("#hand")) { $("hand-voice").checked = hand.voice_armed; $("hand-cursor").checked = hand.move_cursor; }
  $("hand-tracking").textContent = (hand.tracking_available ? "Live gesture tracking is available." : "Install the hand packages and verified model, then restart. Practice works now.") +
    (hand.allowed ? "" : " Real hand control needs --allow-hand-control.") + (hand.last_stop ? " Last stop: " + hand.last_stop + "." : "") + " Gesture clicks: " + hand.clicks + ".";
}
function cancelPointer() {
  pointerGeneration += 1;
  pointerNext = null;
  if (pointerAbort) pointerAbort.abort();
}
async function sendPointer(point) {
  if (exiting || !$("pad").classList.contains("live")) return;
  pointerNext = point;
  if (pointerAbort) return;
  const generation = pointerGeneration;
  while (pointerNext && !exiting && generation === pointerGeneration && $("pad").classList.contains("live")) {
    const next = pointerNext;
    pointerNext = null;
    const controller = new AbortController();
    pointerAbort = controller;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 8000);
    try {
      await send("/api/hand/practice", {type: "pointer", x: next.x, y: next.y}, controller.signal);
    } catch (error) {
      pointerNext = null;
      if (!exiting && generation === pointerGeneration) showError(timedOut ? new Error("Practice pointer request timed out. Move again to retry.") : error);
      break;
    } finally {
      clearTimeout(timeout);
      if (pointerAbort === controller) pointerAbort = null;
    }
  }
}
let lastMove = 0;
$("pad").addEventListener("pointermove", (event) => {
  if (!$("pad").classList.contains("live") || Date.now() - lastMove < 80) return;
  lastMove = Date.now();
  const box = $("pad").getBoundingClientRect();
  const clamp = (value) => Math.min(1, Math.max(0, Math.round(value * 1000) / 1000));
  sendPointer({x: clamp((event.clientX - box.left) / box.width), y: clamp((event.clientY - box.top) / box.height)});
});
$("practice").addEventListener("click", () => send("/api/hand/practice", {type: $("practice").textContent === "Stop practice" ? "stop" : "start"}).catch(showError));
document.querySelectorAll("[data-gesture]").forEach((button) => button.addEventListener("click", () => {
  const kind = button.dataset.gesture;
  send("/api/hand/practice", kind === "clap" ? {type: "clap"} : {type: "click", button: kind}).catch(showError);
}));
$("hand-stop").addEventListener("click", () => send("/api/command", {command: "stop mouse"}).catch(showError));
$("hand-arm").addEventListener("click", () => send("/api/hand/arm", {voice: $("hand-voice").checked, move_cursor: $("hand-cursor").checked}).catch(showError));
$("exit").addEventListener("click", async () => {
  if (exiting) return;
  $("exit").disabled = true;
  try {
    const response = await fetch("/api/setup/exit", {method: "POST", headers: {"Content-Type": "application/json", "X-Jarvis-Token": token}, body: "{}"});
    if (!response.ok || (await response.json()).stopped !== true) throw new Error("Could not exit Jarvis. Try again.");
    if (exiting) return;
    endSession("Jarvis stopped", "This local app has closed. You can close this tab.");
  } catch (error) { if (!exiting) $("exit").disabled = false; showError(new Error("Could not exit Jarvis. Try again.")); }
});
/* Diagnostics contain only fixed labels, booleans and a validated version number:
   never the session token, keys, chat text, file paths, host names or camera data. */
function diagnosticsText() {
  const report = diagnosticsReport || {};
  const yes = (value) => value === true ? "yes" : value === false ? "no" : "unknown";
  const pick = (value, allowed) => allowed.includes(value) ? value : "unknown";
  const packages = report.packages && typeof report.packages === "object" ? report.packages : {};
  const python = typeof report.python === "string" && /^\d{1,2}\.\d{1,3}(\.\d{1,3})?$/.test(report.python) ? report.python : "unknown";
  return ["Jarvis dashboard diagnostics (no keys, chat text, paths, host names or images)",
    "Mode: " + pick(report.mode, ["demo", "camera"]),
    "Operating system: " + pick(report.os, ["windows", "macos", "linux", "other"]),
    "Python: " + python + " (supported: " + yes(report.python_ok) + ")",
    "Optional packages: " + ["cv2", "vosk", "sounddevice", "mediapipe"].map((name) => name + " " + yes(packages[name])).join(", "),
    "Hand model verified: " + yes(report.hand_model) + "; voice model present: " + yes(report.voice_model),
    "Hand control permitted at launch: " + yes(report.hand_allowed),
    "Connection: " + connection + "; last failure: " + (failure || "none"),
    "Guard at last update: " + (latest ? (latest.active ? "active" : "standby") + ", camera " + pick(latest.camera, ["fault", "receiving", "waiting"]) : "unknown")].join("\n");
}
$("copy-diagnostics").addEventListener("click", async () => {
  const value = diagnosticsText();
  $("diagnostics-text").value = value;
  try {
    if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(value);
    $("diagnostics-text").hidden = true;
    $("diagnostics-status").textContent = "Diagnostics copied. Review them before sharing.";
  } catch (error) {
    $("diagnostics-text").hidden = false;
    $("diagnostics-text").focus();
    $("diagnostics-text").select();
    $("diagnostics-status").textContent = "Clipboard access is unavailable. The diagnostics are selected below; copy them with your keyboard.";
  }
});
async function refresh() {
  if (exiting || !token || pollAbort) return;
  const controller = new AbortController();
  pollAbort = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch("/api/status", {signal: controller.signal});
    if (!response.ok) { failure = "status rejected"; throw new Error("Session unavailable"); }
    const state = await response.json();
    if (!exiting && !controller.signal.aborted) {
      render(state); // only an accepted, newer same-instance snapshot clears pollFailing
      if (pollError) { $("error").hidden = true; pollError = false; }
    }
  } catch (error) {
    if (!exiting) {
      if (controller.signal.aborted) failure = "status timeout";
      else if (error instanceof TypeError) failure = "unreachable";
      showError(controller.signal.aborted ? new Error("Status request timed out. Retrying automatically.")
        : error instanceof TypeError ? new Error("Jarvis is not answering. Retrying automatically.") : error);
      pollError = true;
      pollFailing = true;
      updateFreshness();
    }
  } finally {
    clearTimeout(timeout);
    if (pollAbort === controller) pollAbort = null;
  }
}
function tick() {
  if (!token && !exiting) {
    if (bootAbort && monotonic() - bootStartedAt > BOOT_MS) bootAbort.abort();
    else if (!bootAbort && bootFailedAt !== null && monotonic() - bootFailedAt > RETRY_MS) bootstrap();
  }
  updateFreshness();
  refresh();
}
$("retry-connect").addEventListener("click", () => { if (!token) { bootFailedAt = null; bootstrap(); } else refresh(); });
function bootstrap() {
  if (exiting || token || bootAbort) return;
  // Browsers always provide AbortController; without it (Node VM harnesses) a timed-out attempt is ignored.
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  const attempt = {timedOut: false, settled: false, abort() {
    if (this.settled) return;
    this.timedOut = true;
    if (controller) controller.abort(); else failed(new Error("Session timed out"));
  }};
  const failed = (error) => {
    if (attempt.settled) return;
    attempt.settled = true;
    if (bootAbort === attempt) bootAbort = null;
    if (exiting) return;
    if (!token) {
      pollFailing = true;
      failure = attempt.timedOut ? "session timeout" : "session unavailable";
      bootFailedAt = monotonic();
      updateFreshness();
      showError(attempt.timedOut ? new Error("Jarvis did not answer within 8 seconds. Retrying automatically.") : error);
    } else showError(error);
  };
  bootAbort = attempt;
  bootStartedAt = monotonic();
  bootFailedAt = null;
  fetch("/api/session", controller ? {signal: controller.signal} : {}).then((response) => {
    if (attempt.timedOut) throw new Error("Session timed out");
    if (!response.ok) throw new Error("Session unavailable");
    return response.json();
  }).then(async (session) => {
    if (exiting || attempt.timedOut) return;
    if (typeof session.instance !== "string" || !/^[0-9a-f]{16}$/.test(session.instance)) throw new Error("Session unavailable");
    statusInstance = session.instance;
    token = session.token;
    attempt.settled = true;
    if (bootAbort === attempt) bootAbort = null;
    $("error").hidden = true;
    await refresh();
    if (exiting) return;
    const response = await fetch("/api/setup/diagnostics");
    if (!response.ok) return;
    const report = await response.json();
    if (exiting) return;
    diagnosticsReport = report;
    $("setup-link").hidden = report.setup_available !== true;
    $("exit").hidden = report.can_exit !== true;
  }).catch((error) => {
    if (attempt.settled) { if (!exiting) showError(error); return; }
    failed(error);
  });
}
bootstrap();
const pollTimer = setInterval(tick, 500);
