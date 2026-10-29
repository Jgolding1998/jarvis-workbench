"use strict";
let token = "";
let exiting = false;
const $ = (id) => document.getElementById(id);
async function send(path, data) {
  if (exiting) return;
  const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json", "X-Jarvis-Token": token}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed");
  $("error").hidden = true;
  if (!exiting) render(result);
}
function showError(error) { if (exiting) return; $("error").textContent = error.message; $("error").hidden = false; }
function render(state) {
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
let lastMove = 0;
$("pad").addEventListener("pointermove", (event) => {
  if (!$("pad").classList.contains("live") || Date.now() - lastMove < 80) return;
  lastMove = Date.now();
  const box = $("pad").getBoundingClientRect();
  const clamp = (value) => Math.min(1, Math.max(0, Math.round(value * 1000) / 1000));
  send("/api/hand/practice", {type: "pointer", x: clamp((event.clientX - box.left) / box.width), y: clamp((event.clientY - box.top) / box.height)}).catch(showError);
});
$("practice").addEventListener("click", () => send("/api/hand/practice", {type: $("practice").textContent === "Stop practice" ? "stop" : "start"}).catch(showError));
document.querySelectorAll("[data-gesture]").forEach((button) => button.addEventListener("click", () => {
  const kind = button.dataset.gesture;
  send("/api/hand/practice", kind === "clap" ? {type: "clap"} : {type: "click", button: kind}).catch(showError);
}));
$("hand-stop").addEventListener("click", () => send("/api/command", {command: "stop mouse"}).catch(showError));
$("hand-arm").addEventListener("click", () => send("/api/hand/arm", {voice: $("hand-voice").checked, move_cursor: $("hand-cursor").checked}).catch(showError));
$("exit").addEventListener("click", async () => {
  $("exit").disabled = true;
  try {
    const response = await fetch("/api/setup/exit", {method: "POST", headers: {"Content-Type": "application/json", "X-Jarvis-Token": token}, body: "{}"});
    if (!response.ok || (await response.json()).stopped !== true) throw new Error("Could not exit Jarvis. Try again.");
    exiting = true;
    clearInterval(pollTimer);
    $("state").textContent = "Jarvis stopped";
    $("state-note").textContent = "This local app has closed. You can close this tab.";
    $("orb").classList.remove("running");
    $("pad").classList.remove("live");
    $("virtual-cursor").hidden = true;
    $("hand-label").textContent = "OFF";
    $("armed-label").textContent = "DISARMED";
    $("error").hidden = true;
    document.querySelectorAll("button, input").forEach((control) => { control.disabled = true; });
  } catch (error) { $("exit").disabled = false; showError(new Error("Could not exit Jarvis. Try again.")); }
});
async function refresh() { if (exiting) return; try { const response = await fetch("/api/status"); if (!response.ok) throw new Error("Session unavailable"); const state = await response.json(); if (!exiting) render(state); } catch (error) { if (!exiting) showError(error); } }
fetch("/api/session").then((response) => response.json()).then(async (session) => {
  token = session.token;
  await refresh();
  const response = await fetch("/api/setup/diagnostics");
  if (!response.ok) return;
  const report = await response.json();
  $("setup-link").hidden = report.setup_available !== true;
  $("exit").hidden = report.can_exit !== true;
}).catch(showError);
const pollTimer = setInterval(refresh, 500);
