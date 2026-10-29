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
const $ = (id) => document.getElementById(id);
async function send(path, data, signal) {
  if (exiting) return;
  const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json", "X-Jarvis-Token": token}, body: JSON.stringify(data), signal});
  const result = await response.json();
  if (signal && signal.aborted) return;
  if (!response.ok) throw new Error(result.error || "Request failed");
  $("error").hidden = true;
  if (!exiting) render(result);
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
  document.querySelectorAll("button, input").forEach((control) => { control.disabled = true; });
}
function render(state) {
  if (exiting) return;
  if (typeof state.instance !== "string" || !/^[0-9a-f]{16}$/.test(state.instance)
      || !Number.isSafeInteger(state.sequence) || state.sequence < 1) throw new Error("Session unavailable");
  if (statusInstance && statusInstance !== state.instance) {
    endSession("Jarvis restarted", "Reload this tab to connect to the new local session.");
    $("armed-label").textContent = "RELOAD REQUIRED";
    $("hand-label").textContent = "UNKNOWN";
    $("people").textContent = "?";
    $("camera").textContent = "Unknown";
    return;
  }
  statusInstance = state.instance;
  if (state.sequence <= statusSequence) return;
  statusSequence = state.sequence;
  $("mode").textContent = state.hand && state.hand.allowed ? (state.hand.mode === "watching" ? "LOCAL HAND CAMERA" : "LOCAL HARDWARE READY") : state.demo ? "SAFE DEMO" : "LOCAL CAMERA";
  $("state").textContent = state.active ? (state.action_error ? "Action failed" : state.camera === "fault" ? "Camera unavailable" : "Guard active") : "Standby";
  $("state-note").textContent = state.action_error || (state.active ? (state.established ? "Presence established. Policy is evaluating local observations." : "Waiting to establish your presence in the camera view.") : "Camera is idle. Say or type active when you are ready.");
  $("action-error").textContent = state.action_error || "";
  $("action-error").hidden = !state.action_error;
  $("orb").classList.toggle("running", state.active);
  $("people").textContent = state.people === null ? "?" : state.people;
  $("absence").textContent = Number(state.absent_seconds).toFixed(1);
  $("camera").textContent = {fault:"Fault",receiving:"Receiving",waiting:"Waiting"}[state.camera];
  $("voice").textContent = "Voice: " + state.voice;
  $("uptime").textContent = state.uptime + "s uptime";
  $("armed-label").textContent = state.lock_armed ? "PRIVACY LOCK ARMED" : state.sheet_armed ? "SHEET DEMO ARMED" : "DISARMED";
  $("lock-option").disabled = !state.allow_lock;
  $("sheet-option").disabled = !state.allow_sheet;
  $("demo-controls").hidden = !state.demo || state.hand.mode === "watching";
  renderHand(state.hand);
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
}
$("command-form").addEventListener("submit", (event) => { event.preventDefault(); send("/api/command", {command: $("command").value}).catch(showError); });
$("stop").addEventListener("click", () => { document.querySelectorAll('input[name="action"]').forEach((input) => delete input.dataset.dirty); send("/api/command", {command: "stop"}).catch(showError); });
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
async function refresh() {
  if (exiting || !token || pollAbort) return;
  const controller = new AbortController();
  pollAbort = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch("/api/status", {signal: controller.signal});
    if (!response.ok) throw new Error("Session unavailable");
    const state = await response.json();
    if (!exiting && !controller.signal.aborted) {
      render(state);
      if (pollError) { $("error").hidden = true; pollError = false; }
    }
  } catch (error) {
    if (!exiting) {
      showError(controller.signal.aborted ? new Error("Status request timed out. Retrying automatically.") : error);
      pollError = true;
    }
  } finally {
    clearTimeout(timeout);
    if (pollAbort === controller) pollAbort = null;
  }
}
fetch("/api/session").then((response) => {
  if (!response.ok) throw new Error("Session unavailable");
  return response.json();
}).then(async (session) => {
  if (exiting) return;
  if (typeof session.instance !== "string" || !/^[0-9a-f]{16}$/.test(session.instance)) throw new Error("Session unavailable");
  statusInstance = session.instance;
  token = session.token;
  await refresh();
  if (exiting) return;
  const response = await fetch("/api/setup/diagnostics");
  if (!response.ok) return;
  const report = await response.json();
  if (exiting) return;
  $("setup-link").hidden = report.setup_available !== true;
  $("exit").hidden = report.can_exit !== true;
}).catch(showError);
const pollTimer = setInterval(refresh, 500);
