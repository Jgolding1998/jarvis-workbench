"use strict";
/* Headless browser checks for the local dashboard (web/index.html, app.js, style.css) with a mocked API.
   Usage: node tests/browser_dashboard.cjs [screenshot-dir]
   Local runs may skip missing prerequisites. Set JARVIS_REQUIRE_BROWSER=1 in CI to fail instead.
   Covers connection freshness (live, delayed, lost, hung status, failed status, old snapshots, recovery and
   restart), the fixed Start/Stop controls, truthful Stop acknowledgement, sanitized diagnostics, keyboard,
   mobile layout and reduced motion. Never touches a real Jarvis service, camera, microphone, cursor or
   provider: every request is answered by the in-process mock and any other origin fails the run.
   Also run by tests/browser_quickstart.cjs so the required CI browser job executes it. */
const http = require("node:http");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {spawn} = require("node:child_process");

const ROOT = path.resolve(__dirname, "..");
const WEB = path.join(ROOT, "web");
const SHOTS = process.argv[2] || "";
const TOKEN = "dash-fixture-token-" + "5c1e";
const CANARY = "C:\\Users\\" + "private-canary\\jarvis";
const INSTANCE = "0123456789abcdef";
const OTHER = "fedcba9876543210";

/* ---------- mock local service ---------- */
const mock = {requests: [], sessionMode: "ok", statusMode: "ok", commandMode: "ok", held: [], heldReplies: [], sequence: 0, oldSequence: 1,
  state: {active: false, lock_armed: false, sheet_armed: false, people: 1, camera: "waiting", hand: "off"}};
function snapshot(instance, sequence) {
  const s = mock.state;
  return {instance: instance || INSTANCE, sequence: sequence || ++mock.sequence, active: s.active, established: s.active,
    action_error: null, camera: s.active ? "receiving" : "waiting", people: s.active ? s.people : null, absent_seconds: 0,
    voice: "disabled", uptime: 12, lock_armed: s.lock_armed, sheet_armed: s.sheet_armed, demo: true, allow_lock: true,
    allow_sheet: true, events: [{at: 1.5, message: "Command: active"}], camera_owner: s.active ? "presence" : null,
    hand: {mode: s.hand, allowed: false, clicks: 0, tracking_available: false, move_cursor: false, voice_armed: false}};
}
function json(res, status, data) {
  res.writeHead(status, {"Content-Type": "application/json", "Cache-Control": "no-store"});
  res.end(JSON.stringify(data));
}
function serveFile(res, name, type) {
  res.writeHead(200, {"Content-Type": type, "Cache-Control": "no-store"});
  res.end(fs.readFileSync(path.join(WEB, name)));
}
const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    const p = new URL(req.url, "http://127.0.0.1").pathname;
    if (p.startsWith("/api/")) {
      let parsed = null;
      try { parsed = body ? JSON.parse(body) : null; } catch (error) { parsed = "INVALID"; }
      mock.requests.push({method: req.method, path: p, token: req.headers["x-jarvis-token"] || null, body: parsed});
      if (req.method === "GET" && p === "/api/session") {
        if (mock.sessionMode === "hang") { mock.held.push(res); return; }
        return json(res, 200, {token: TOKEN, instance: INSTANCE});
      }
      if (req.method === "GET" && p === "/api/setup/diagnostics") return json(res, 200, {python: "3.12.4", python_ok: true, os: "windows",
        packages: {cv2: false, vosk: false, sounddevice: false, mediapipe: false}, hand_model: false, voice_model: false, config_saved: false,
        local: true, mode: "demo", hand_allowed: false, can_exit: false, setup_available: true, root: CANARY, user: CANARY});
      if (req.method === "GET" && p === "/api/status") {
        if (mock.statusMode === "hang") { mock.held.push(res); return; }
        if (mock.statusMode === "fail") return json(res, 500, {error: "Session unavailable"});
        if (mock.statusMode === "old") return json(res, 200, snapshot(INSTANCE, mock.oldSequence));
        if (mock.statusMode === "restart") return json(res, 200, snapshot(OTHER, 1));
        return json(res, 200, snapshot());
      }
      if (req.method === "POST" && req.headers["x-jarvis-token"] !== TOKEN) return json(res, 403, {error: "Untrusted"});
      if (req.method === "POST" && p === "/api/command") {
        if (mock.commandMode === "hang") { mock.held.push(res); return; }
        if (mock.commandMode === "fail") return json(res, 503, {error: "Server busy"});
        const command = parsed && parsed.command;
        if (command === "active") mock.state.active = true;
        if (command === "stop" || command === "inactive") Object.assign(mock.state, {active: false, lock_armed: false, sheet_armed: false, hand: "off"});
        // "delay": Jarvis applies the command now but its answer arrives later (out of order with polls).
        if (mock.commandMode === "delay") { mock.heldReplies.push({res, data: snapshot()}); return; }
        return json(res, 200, snapshot());
      }
      if (req.method === "POST" && p === "/api/hand/practice") {
        if (parsed && parsed.type === "start") mock.state.hand = "practice";
        if (parsed && parsed.type === "stop") mock.state.hand = "off";
        return json(res, 200, snapshot());
      }
      if (req.method === "POST" && p === "/api/demo") { mock.state.people = parsed.people; return json(res, 200, snapshot()); }
      return json(res, 404, {error: "Not found"});
    }
    if (p === "/") return serveFile(res, "index.html", "text/html; charset=utf-8");
    if (p === "/app.js") return serveFile(res, "app.js", "text/javascript; charset=utf-8");
    if (p === "/style.css") return serveFile(res, "style.css", "text/css; charset=utf-8");
    if (p === "/favicon.svg") return serveFile(res, "favicon.svg", "image/svg+xml");
    res.writeHead(404, {"Content-Type": "text/plain"});
    res.end("not found");
  });
});
function releaseHeld() { for (const res of mock.held.splice(0)) { try { res.destroy(); } catch (error) { /* closed */ } } }
function releaseReplies() { for (const item of mock.heldReplies.splice(0)) json(item.res, 200, item.data); }

/* ---------- tiny CDP client (same approach as browser_quickstart.cjs) ---------- */
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let ws = null, nextId = 0, session, origin = "";
const pending = new Map(), external = [], exceptions = [];
function send(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, {resolve, reject});
    ws.send(JSON.stringify({id, method, params: params || {}, sessionId: session}));
  });
}
async function evaluate(expression) {
  const result = await send("Runtime.evaluate", {expression, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw new Error("evaluate failed: " + expression);
  return result.result.value;
}
async function waitFor(expression, label, timeout) {
  const end = Date.now() + (timeout || 8000);
  while (Date.now() < end) {
    try { if (await evaluate(expression)) return true; } catch (error) { /* navigating */ }
    await sleep(50);
  }
  failures.push("Timed out waiting for " + (label || expression));
  return false;
}
let passed = 0;
const failures = [];
function check(condition, label) { if (condition) passed += 1; else failures.push(label); }
function equal(actual, expected, label) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  check(ok, label + (ok ? "" : " (got " + JSON.stringify(actual) + ", expected " + JSON.stringify(expected) + ")"));
}
const text = (id) => evaluate("document.getElementById(" + JSON.stringify(id) + ").textContent");
const attr = (id, name) => evaluate("document.getElementById(" + JSON.stringify(id) + ").getAttribute(" + JSON.stringify(name) + ")");
const conn = () => attr("connection", "data-state");
const click = (id) => evaluate("document.getElementById(" + JSON.stringify(id) + ").click()");
async function key(name) {
  const codes = {Tab: 9, Enter: 13};
  for (const type of ["keyDown", "keyUp"]) await send("Input.dispatchKeyEvent", {type, key: name, code: name, windowsVirtualKeyCode: codes[name]});
  await sleep(40);
}
async function viewport(width, height, mobile) { await send("Emulation.setDeviceMetricsOverride", {width, height, deviceScaleFactor: 1, mobile}); }
async function noOverflow(label) {
  const result = await evaluate("[document.documentElement.scrollWidth, window.innerWidth]");
  check(result[0] <= result[1], label + " has no horizontal overflow (" + result.join(" > ") + ")");
}
async function shot(name) {
  if (!SHOTS) return;
  await evaluate("document.querySelector('.topbar').style.position = 'static'");
  const result = await send("Page.captureScreenshot", {format: "png", captureBeyondViewport: true});
  await evaluate("document.querySelector('.topbar').style.position = ''");
  fs.mkdirSync(SHOTS, {recursive: true});
  fs.writeFileSync(path.join(SHOTS, name + ".png"), Buffer.from(result.data, "base64"));
}
async function open() {
  await send("Page.navigate", {url: origin + "/"});
  await waitFor("document.readyState === 'complete' && document.getElementById('connection').dataset.state === 'live'", "dashboard live");
}
const unsafe = (value) => /stopped|disarmed|is off|guard off/i.test(value);

/* ---------- scenarios ---------- */
const lockedBeforeData = "['start','arm','practice','command','command-run','hand-arm'].every(id => document.getElementById(id).disabled) && [...document.querySelectorAll('input[name=action],[data-gesture]')].every(n => n.disabled)";
async function navigateOnly() {
  await evaluate("window.__previousPage = true").catch(() => {});
  await send("Page.navigate", {url: origin + "/"});
  await waitFor("!window.__previousPage && document.readyState === 'complete'", "navigation");
}
async function unknownBeforeData(label) {
  equal(await text("armed-label"), "UNKNOWN", label + ": actions are unknown, not disarmed");
  equal(await text("hand-label"), "UNKNOWN", label + ": hand control is unknown, not off");
  check(await evaluate("document.getElementById('demo-controls').hidden"), label + ": no simulator before demo mode is confirmed");
  check(await evaluate(lockedBeforeData), label + ": Start, permissions, practice and typed commands wait for data");
  check(await evaluate("!document.getElementById('stop').disabled"), label + ": Stop remains available as an attempt");
  check(!/standby|disarmed|stopped|safe demo/i.test(await text("state") + " " + await text("state-note") + " " + await text("mode")), label + ": nothing claims standby, disarmed or demo");
}
async function hungSession() {
  await viewport(1280, 900, false);
  mock.sessionMode = "hang";
  await navigateOnly();
  await sleep(800);
  equal(await text("state"), "Connecting", "hung session: shows connecting");
  await unknownBeforeData("hung session");
  await shot("00-connecting");
  mock.requests = [];
  await click("stop");
  await waitFor("document.getElementById('stop-status').dataset.kind === 'bad'", "stop before data");
  check((await text("stop-status")).startsWith("Stop was not confirmed"), "Stop before any data is reported as unconfirmed");
  check(mock.requests.some((item) => item.path === "/api/command") && !mock.requests.some((item) => /\/api\/(arm|hand)/.test(item.path)), "Stop attempt is the only command sent");
  await waitFor("document.getElementById('connection').dataset.state === 'lost'", "bounded session request", 11000);
  equal(await text("connection-text"), "Cannot reach Jarvis", "hung session ends in a finite failure");
  check(await evaluate("!document.getElementById('retry-connect').hidden"), "a retry control is offered");
  await unknownBeforeData("session timeout");
  await shot("00b-cannot-reach");
  mock.sessionMode = "ok";
  releaseHeld();
  await waitFor("document.getElementById('connection').dataset.state === 'live'", "automatic session retry", 9000);
  equal(await text("state"), "Standby", "same-session recovery shows the real state");
  check(await evaluate("!document.getElementById('start').disabled && !document.getElementById('demo-controls').hidden && document.getElementById('retry-connect').hidden"), "recovery enables Start and the confirmed demo simulator");
  check(await evaluate("document.getElementById('error').hidden"), "recovery clears the connection error");
}
async function hungFirstStatus() {
  await viewport(390, 844, true);
  mock.statusMode = "hang";
  await navigateOnly();
  await waitFor("document.getElementById('connection').dataset.state === 'lost'", "first status timeout", 11000);
  equal(await text("connection-text"), "Cannot reach Jarvis", "hung first status ends in a finite failure");
  equal(await text("state"), "Not connected", "no first snapshot means not connected");
  await unknownBeforeData("hung first status");
  await noOverflow("phone before first status");
  await shot("00c-phone-cannot-reach");
  releaseHeld();
  mock.statusMode = "ok";
  await waitFor("document.getElementById('connection').dataset.state === 'live'", "first status recovery", 9000);
  equal(await text("armed-label"), "DISARMED", "the first accepted snapshot supplies the real action state");
}
async function clockChanges() {
  mock.oldSequence = 1;
  mock.statusMode = "old";
  await evaluate("(() => { const real = Date.now.bind(Date); window.__realNow = real; Date.now = () => real() - 3600000; })()");
  await sleep(7000);
  const seen = await evaluate("({c: document.getElementById('connection').dataset.state, orb: document.getElementById('orb').classList.contains('running'), state: document.getElementById('state').textContent})");
  equal(seen, {c: "lost", orb: false, state: "Status unknown"}, "a clock moved back an hour cannot keep old snapshots live");
  mock.statusMode = "ok";
  await waitFor("document.getElementById('connection').dataset.state === 'live'", "recovery after clock rollback", 6000);
  await evaluate("(() => { const real = window.__realNow; Date.now = () => real() + 3600000; })()");
  await sleep(2000);
  equal(await conn(), "live", "a clock moved forward does not expire fresh snapshots");
  await evaluate("Date.now = window.__realNow");
  equal(await text("state"), "Guard active", "clock changes leave the accepted state intact");
}
async function practiceAfterStop() {
  await click("stop");
  await waitFor("document.getElementById('stop-status').dataset.kind === 'ok'", "stop confirmed before practice");
  await click("practice");
  await waitFor("document.getElementById('hand-label').textContent === 'PRACTICE'", "practice started");
  check(await evaluate("document.getElementById('stop-status').hidden"), "starting practice retires the earlier Stop confirmation");
  await shot("10-practice");
  await click("practice");
  await waitFor("document.getElementById('hand-label').textContent === 'OFF'", "practice stopped");
  mock.commandMode = "delay";
  await click("stop");
  for (let tries = 0; !mock.heldReplies.length && tries < 60; tries++) await sleep(50);
  mock.state.hand = "practice";
  await waitFor("document.getElementById('hand-label').textContent === 'PRACTICE'", "newer poll shows practice");
  releaseReplies();
  mock.commandMode = "ok";
  await waitFor("document.getElementById('stop-status').dataset.kind !== 'pending' || document.getElementById('stop-status').textContent.includes('newer update')", "late stop answer");
  const late = await evaluate("[document.getElementById('stop-status').dataset.kind, document.getElementById('stop-status').textContent]");
  check(late[0] !== "ok" && late[1].includes("newer update shows something running"), "an older Stop answer cannot confirm over a newer running snapshot");
  mock.state.hand = "off";
  await waitFor("document.getElementById('hand-label').textContent === 'OFF'", "practice off again");
}
async function firstRun() {
  await viewport(1280, 900, false);
  await open();
  equal(await text("state"), "Standby", "first run shows standby");
  equal(await text("start-label"), "Start observing", "fixed Start control is labelled");
  check(await evaluate("!document.getElementById('start').disabled && !document.getElementById('stop').disabled"), "Start and Stop are both available");
  check(await evaluate("!document.getElementById('demo-controls').hidden && document.querySelector('#demo-controls .tag.sim').textContent === 'SIMULATED'"), "demo simulator is marked simulated");
  check((await text("readings-note")).startsWith("Simulated readings"), "demo readings say they are simulated");
  check(await evaluate("!document.querySelector('details.advanced').open && document.getElementById('command').value === ''"), "typed commands are optional and collapsed, with no prefilled command");
  await shot("01-first-run");
  await key("Tab");
  equal(await evaluate("document.activeElement.className"), "skip", "first Tab reaches the skip link");
  await key("Enter");
  await sleep(100);
  equal(await evaluate("document.activeElement.id"), "presence", "skip link moves focus to the presence controls");
  mock.requests = [];
  await click("start");
  await waitFor("document.getElementById('state').textContent === 'Guard active'", "start observing");
  const posts = mock.requests.filter((item) => item.method === "POST");
  equal(posts.map((item) => [item.path, item.body]), [["/api/command", {command: "active"}]], "Start sends only the allowlisted active command");
  check(posts.every((item) => item.token === TOKEN), "Start uses the session token");
  check(!mock.requests.some((item) => /\/api\/(arm|hand\/arm)/.test(item.path)), "Start never arms actions or hand control");
  check(await evaluate("document.getElementById('orb').classList.contains('running') && document.getElementById('start').disabled"), "fresh active state looks live and Start is not repeatable");
  equal(await text("armed-label"), "DISARMED", "observing leaves actions disarmed");
  await click("copy-diagnostics");
  await waitFor("!document.getElementById('diagnostics-text').hidden || document.getElementById('diagnostics-status').textContent.includes('copied')", "diagnostics");
  const report = await evaluate("document.getElementById('diagnostics-text').value");
  check(report.includes("Mode: demo") && report.includes("Python: 3.12.4") && report.includes("Connection: live"), "diagnostics report mode, version and connection");
  check(!report.includes(TOKEN) && !report.includes("private-canary") && !report.includes("127.0.0.1") && !report.includes(origin), "diagnostics exclude token, paths and host names");
  await shot("02-observing");
}

async function hungStatus() {
  mock.statusMode = "hang";
  await waitFor("document.getElementById('connection').dataset.state === 'delayed'", "delayed after a hung status", 6000);
  check(await evaluate("!document.getElementById('orb').classList.contains('running')"), "delayed status removes the live orb");
  check((await text("readings-note")).startsWith("Delayed"), "delayed readings are labelled");
  await shot("03-delayed");
  await waitFor("document.getElementById('connection').dataset.state === 'lost'", "lost after the freshness limit", 8000);
  equal(await text("state"), "Status unknown", "lost connection shows unknown status, not standby");
  const note = await text("state-note");
  check(note.includes("Guard active") && note.includes("may still be running") && !unsafe(note), "lost note reports last known state without claiming stopped");
  equal(await text("people"), "?", "people reading becomes unknown");
  equal(await text("camera"), "Unknown", "camera reading becomes unknown");
  equal(await text("armed-label"), "LAST KNOWN: DISARMED", "armed label is marked last known");
  check(await evaluate("document.getElementById('start').disabled && !document.getElementById('stop').disabled"), "Stop stays available; Start waits for a fresh status");
  check(await evaluate("[...document.querySelectorAll('[data-people],[data-gesture],input[name=action]')].every(n => n.disabled) && document.getElementById('arm').disabled && document.getElementById('practice').disabled"), "simulator, practice and permission controls wait for a fresh status");
  equal(await text("start-label"), "Waiting for fresh status", "disabled Start does not claim to be observing");
  check(!unsafe(await text("connection-text")), "connection text never claims the guard stopped");
  await shot("04-lost");
}

async function stopDuringLoss() {
  mock.commandMode = "fail";
  await click("stop");
  await waitFor("document.getElementById('stop-status').dataset.kind === 'bad'", "unconfirmed stop");
  const status = await text("stop-status");
  check(status.startsWith("Stop was not confirmed") && status.includes("may still be running"), "failed Stop is reported as unconfirmed");
  equal(await text("state"), "Status unknown", "failed Stop does not repaint the guard as stopped");
  await shot("05-stop-unconfirmed");
  mock.commandMode = "hang";
  await click("stop");
  await waitFor("document.getElementById('stop-status').textContent.startsWith('Stop timed out')", "stop timeout", 11000);
  check(await evaluate("document.getElementById('stop-status').textContent.includes('may still be running')"), "timed-out Stop stays truthful");
  mock.commandMode = "ok";
  await click("stop");
  await waitFor("document.getElementById('stop-status').dataset.kind === 'ok'", "confirmed stop");
  check((await text("stop-status")).startsWith("Stop confirmed by Jarvis"), "Stop is confirmed only by a fresh Jarvis snapshot");
  equal(await text("state"), "Standby", "confirmed Stop shows standby from the fresh snapshot");
  releaseHeld();
}

async function recovery() {
  mock.statusMode = "ok";
  const settled = "document.getElementById('connection').dataset.state === 'live' && !document.getElementById('start').disabled";
  await waitFor(settled, "live again", 10000);
  await sleep(700);
  await waitFor(settled, "live and settled", 10000);
  await click("start");
  await waitFor("document.getElementById('state').textContent === 'Guard active'", "active again");
  check(await evaluate("document.getElementById('stop-status').hidden"), "an earlier Stop confirmation is retired once the guard runs again");
  mock.statusMode = "fail";
  await waitFor("document.getElementById('connection').dataset.state === 'lost'", "lost after failed status", 4000);
  check((await text("error")).length > 0 && await evaluate("!document.getElementById('error').hidden"), "failed status shows an error");
  mock.oldSequence = 1;
  mock.statusMode = "old";
  await sleep(1500);
  equal(await conn(), "lost", "an old snapshot cannot restore the current view");
  equal(await text("state"), "Status unknown", "old snapshot leaves status unknown");
  mock.statusMode = "ok";
  await waitFor("document.getElementById('connection').dataset.state === 'live'", "recovered");
  equal(await text("state"), "Guard active", "a fresh same-instance snapshot restores the view");
  check(await evaluate("document.getElementById('orb').classList.contains('running') && document.getElementById('error').hidden"), "recovery restores live visuals and clears the poll error");
  await shot("06-recovered");
}

async function phoneAndMotion() {
  await viewport(390, 844, true);
  await sleep(200);
  await noOverflow("phone dashboard");
  await send("Emulation.setEmulatedMedia", {features: [{name: "prefers-reduced-motion", value: "reduce"}]});
  equal(await evaluate("getComputedStyle(document.getElementById('orb'), '::after').animationName"), "none", "reduced motion stops the orb animation");
  await send("Emulation.setEmulatedMedia", {features: []});
  await shot("07-phone-live");
  mock.statusMode = "hang";
  await waitFor("document.getElementById('connection').dataset.state === 'lost'", "phone lost", 9000);
  await noOverflow("phone lost state");
  check(await evaluate("document.getElementById('stop-status').hidden || document.getElementById('stop-status').dataset.kind !== 'ok'"), "no old Stop confirmation is shown while status is unknown");
  check(await evaluate("(() => { const b = document.getElementById('stop').getBoundingClientRect(); return b.width > 200 && b.height >= 44 && b.top < innerHeight; })()"), "phone keeps a large Stop above the fold");
  await shot("08-phone-lost");
  releaseHeld();
  mock.statusMode = "ok";
  await waitFor("document.getElementById('connection').dataset.state === 'live'", "phone recovered", 9000);
  await viewport(1280, 900, false);
}

async function restart() {
  mock.statusMode = "restart";
  await waitFor("document.getElementById('state').textContent === 'Jarvis restarted'", "restart detected");
  equal(await text("connection-text"), "Reload required", "restart asks for a reload");
  check(await evaluate("document.getElementById('stop').disabled && document.getElementById('start').disabled"), "stale tab cannot send commands to a new instance");
  equal(await text("armed-label"), "RELOAD REQUIRED", "restart does not claim disarmed");
  await shot("09-restart");
  const before = mock.requests.filter((item) => item.path === "/api/status").length;
  await sleep(1200);
  equal(mock.requests.filter((item) => item.path === "/api/status").length, before, "polling stops after a restart");
}

function findBrowser() {
  const env = process.env.JARVIS_TEST_BROWSER;
  const candidates = [env,
    "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"];
  return candidates.find((item) => item && fs.existsSync(item)) || null;
}

async function main() {
  const browser = findBrowser();
  const missing = !browser ? "No Chromium-based browser found (set JARVIS_TEST_BROWSER)" :
    typeof WebSocket !== "function" ? "Node WebSocket API is unavailable" : "";
  if (missing) {
    if (process.env.JARVIS_REQUIRE_BROWSER === "1") throw new Error(missing);
    console.log("SKIP: " + missing);
    return;
  }
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  origin = "http://127.0.0.1:" + server.address().port;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "jarvis-dash-"));
  const child = spawn(browser, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + profile, "--no-first-run",
    "--no-default-browser-check", "--disable-extensions", "--disable-background-networking", "--disable-sync", "--disable-component-update",
    "--deny-permission-prompts", "about:blank"], {stdio: "ignore"});
  try {
    let port = null;
    for (let i = 0; i < 200 && !port; i++) {
      try { port = fs.readFileSync(path.join(profile, "DevToolsActivePort"), "utf8").split("\n")[0].trim(); } catch (error) { await sleep(100); }
    }
    if (!port) throw new Error("browser did not start");
    const version = await (await fetch("http://127.0.0.1:" + port + "/json/version")).json();
    ws = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.addEventListener("open", resolve); ws.addEventListener("error", reject); });
    ws.addEventListener("message", (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result); return; }
      if (msg.method === "Fetch.requestPaused") {
        const url = msg.params.request.url;
        const ok = url.startsWith("data:") || url === origin || url.startsWith(origin + "/");
        if (!ok) external.push(url);
        const reply = ok ? {method: "Fetch.continueRequest", params: {requestId: msg.params.requestId}} : {method: "Fetch.failRequest", params: {requestId: msg.params.requestId, errorReason: "BlockedByClient"}};
        ws.send(JSON.stringify({id: ++nextId, method: reply.method, params: reply.params, sessionId: msg.sessionId}));
      }
      if (msg.method === "Runtime.exceptionThrown") exceptions.push(JSON.stringify(msg.params.exceptionDetails));
    });
    session = undefined;
    const target = await send("Target.createTarget", {url: "about:blank"});
    session = (await send("Target.attachToTarget", {targetId: target.targetId, flatten: true})).sessionId;
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Fetch.enable", {patterns: [{urlPattern: "*"}]});
    await send("Browser.setPermission", {permission: {name: "clipboard-write"}, setting: "denied"}).catch(() => {});
    await hungSession();
    await hungFirstStatus();
    await firstRun();
    await clockChanges();
    await hungStatus();
    await stopDuringLoss();
    await recovery();
    await practiceAfterStop();
    await phoneAndMotion();
    await restart();
    equal(external, [], "no requests left the local fixture server");
    equal(exceptions, [], "no uncaught page exceptions");
  } finally {
    releaseReplies();
    releaseHeld();
    try { ws && ws.close(); } catch (error) { /* closing */ }
    child.kill();
    server.close();
    await sleep(300);
    try { fs.rmSync(profile, {recursive: true, force: true}); } catch (error) { /* browser may still hold files */ }
  }
  console.log(JSON.stringify({passed, failures}, null, 1));
  if (failures.length) process.exitCode = 1;
}

main().catch((error) => { console.error(error && error.stack || String(error)); console.log(JSON.stringify({passed, failures})); process.exitCode = 1; });
