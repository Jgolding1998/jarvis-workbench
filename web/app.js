"use strict";
let token = "";
const $ = (id) => document.getElementById(id);
async function send(path, data) {
  const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json", "X-Jarvis-Token": token}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed");
  $("error").hidden = true;
  render(result);
}
function showError(error) { $("error").textContent = error.message; $("error").hidden = false; }
function render(state) {
  $("mode").textContent = state.demo ? "SAFE DEMO" : "LOCAL CAMERA";
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
  $("demo-controls").hidden = !state.demo;
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
async function refresh() { try { const response = await fetch("/api/status"); if (!response.ok) throw new Error("Session unavailable"); render(await response.json()); } catch (error) { showError(error); } }
fetch("/api/session").then((response) => response.json()).then((session) => { token = session.token; return refresh(); }).catch(showError);
setInterval(refresh, 500);
