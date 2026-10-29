"use strict";
/* Headless browser checks for web/setup.html with a mocked local API.
   Usage: node tests/browser_quickstart.cjs [screenshot-dir]
   Prints "SKIP: ..." and exits 0 when no Chromium-based browser is installed.
   Never touches a real Jarvis service, camera, cursor or provider: every request is
   answered by the in-process mock, and any request to another origin fails the run. */
const http = require("node:http");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {spawn} = require("node:child_process");

const ROOT = path.resolve(__dirname, "..");
const WEB = path.join(ROOT, "web");
const lib = require(path.join(WEB, "setup.js"));
const catalog = JSON.parse(fs.readFileSync(path.join(WEB, "quickstart-catalog.json"), "utf8"));
const SHOTS = process.argv[2] || "";
const importDir = fs.mkdtempSync(path.join(os.tmpdir(), "jarvis-qs-files-"));
const SECRET = "nvapi-FIXTURE-" + "do-not-leak-7f3a";
const LEAK = "SERVER-ERROR-TEXT-" + "c:\\Users\\private";
const TOKEN = "fixture-token-123";
const MESSAGES_SOURCE = fs.readFileSync(path.join(WEB, "setup.js"), "utf8");
const MESSAGES = Function('"use strict";' + /const MESSAGES = (\{[\s\S]*?\n\});/.exec(MESSAGES_SOURCE)[0].replace("const MESSAGES = ", "return ") )();

function findBrowser() {
  const env = process.env.JARVIS_TEST_BROWSER;
  const candidates = [env,
    "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"];
  return candidates.find((item) => item && fs.existsSync(item)) || null;
}

/* ---------- mock local service ---------- */
const mock = {requests: [], routes: {}, state: null};
function freshState(overrides) {
  return Object.assign({local: true, choices: null, plan: null, credentials: {nvidia: false, openai: false, anthropic: false},
    install: {state: "idle", message: LEAK}, providers: []}, overrides || {});
}
/* Strict defaults that behave like the real service: choices must validate, changes are refused
   while an install runs, install needs saved choices, and overlapping saves are counted. */
mock.saveDelay = 0;
mock.inflight = 0;
mock.maxInflight = 0;
function strictSave(res, body) {
  mock.inflight += 1;
  mock.maxInflight = Math.max(mock.maxInflight, mock.inflight);
  setTimeout(() => {
    mock.inflight -= 1;
    if (mock.state.install.state === "running") return json(res, 409, {error: LEAK});
    let choices;
    try {
      if (!body || Object.keys(body).length !== 1) throw new Error("shape");
      choices = lib.validateChoices(body.choices, catalog);
    } catch (error) { return json(res, 400, {error: LEAK}); }
    mock.state.choices = choices;
    mock.state.plan = lib.recommend(choices, catalog);
    json(res, 200, mock.state);
  }, mock.saveDelay);
}
function strictInstall(res, body) {
  if (!body || body.consent !== true || !mock.state.choices) return json(res, 409, {error: LEAK});
  mock.state.install = {state: mock.installOutcome || "ready", message: LEAK};
  json(res, 200, mock.state);
}
const TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"};
function serveFile(res, file, transform) {
  let body = fs.readFileSync(file, "utf8");
  if (transform) body = transform(body);
  res.writeHead(200, {"Content-Type": TYPES[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-store"});
  res.end(body);
}
function json(res, status, data) {
  res.writeHead(status, {"Content-Type": "application/json", "Cache-Control": "no-store"});
  res.end(JSON.stringify(data));
}
const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    const url = new URL(req.url, "http://127.0.0.1");
    const p = url.pathname;
    if (p.startsWith("/api/")) {
      let parsed = null;
      try { parsed = body ? JSON.parse(body) : null; } catch (error) { parsed = "INVALID"; }
      mock.requests.push({method: req.method, path: p, token: req.headers["x-jarvis-token"] || null, body: parsed, host: req.headers.host});
      const route = mock.routes[req.method + " " + p];
      if (route) return route(req, res, parsed);
      if (req.method === "GET" && p === "/api/session") return json(res, 200, {token: TOKEN});
      if (req.method === "GET" && p === "/api/quickstart") return json(res, 200, mock.state);
      if (req.method === "POST" && p === "/api/quickstart/save") return strictSave(res, parsed);
      if (req.method === "POST" && p === "/api/quickstart/install") return strictInstall(res, parsed);
      return json(res, 404, {error: LEAK});
    }
    if (p === "/setup" || p === "/setup.html") return serveFile(res, path.join(WEB, "setup.html"));
    if (p === "/hosted/setup.html") return serveFile(res, path.join(WEB, "setup.html"), (text) => text.replace('name="jarvis-mode" content="auto"', 'name="jarvis-mode" content="hosted"'));
    const name = p.replace(/^\/(hosted\/)?/, "");
    if (["setup.js", "setup.css", "quickstart-catalog.json", "favicon.svg"].includes(name)) return serveFile(res, path.join(WEB, name));
    if (p === "/" || p === "/dashboard") { res.writeHead(200, {"Content-Type": "text/html"}); return res.end("<!doctype html><title>Dashboard fixture</title><p id=dash>dashboard</p>"); }
    res.writeHead(404, {"Content-Type": "text/plain"});
    res.end("not found");
  });
});

/* ---------- tiny CDP client ---------- */
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let ws = null;
let nextId = 0;
const pending = new Map();
const external = [];
const consoleText = [];
const exceptions = [];
let origin = "";
const allowedOrigins = new Set();
function send(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, {resolve, reject});
    ws.send(JSON.stringify({id, method, params: params || {}, sessionId: session}));
  });
}
let session;
async function evaluate(expression) {
  const result = await send("Runtime.evaluate", {expression, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw new Error("evaluate failed: " + expression + " " + JSON.stringify(result.exceptionDetails.exception && result.exceptionDetails.exception.description));
  return result.result.value;
}
async function waitFor(expression, label, timeout) {
  const end = Date.now() + (timeout || 8000);
  while (Date.now() < end) {
    try { if (await evaluate(expression)) return; } catch (error) { /* page may be navigating */ }
    await sleep(50);
  }
  throw new Error("Timed out waiting for " + (label || expression));
}

/* ---------- assertions ---------- */
let passed = 0;
const failures = [];
function check(condition, label) {
  if (condition) passed += 1; else failures.push(label);
}
function equal(actual, expected, label) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  check(ok, label + (ok ? "" : " (got " + JSON.stringify(actual) + ", expected " + JSON.stringify(expected) + ")"));
}
const text = (id) => evaluate("document.getElementById(" + JSON.stringify(id) + ").textContent");
const visible = (id) => evaluate("(() => { const n = document.getElementById(" + JSON.stringify(id) + "); return Boolean(n) && !n.closest('[hidden]') && n.getClientRects().length > 0; })()");
async function select(id, value) {
  await evaluate(`(() => { const s = document.getElementById(${JSON.stringify(id)}); s.value = ${JSON.stringify(String(value))}; s.dispatchEvent(new Event("change", {bubbles: true})); })()`);
}
async function click(selector) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
}
async function noOverflow(label) {
  const result = await evaluate("[document.documentElement.scrollWidth, window.innerWidth]");
  check(result[0] <= result[1], label + " has no horizontal overflow (" + result.join(" > ") + ")");
}
async function shot(name) {
  if (!SHOTS) return;
  const result = await send("Page.captureScreenshot", {format: "png", captureBeyondViewport: true});
  fs.mkdirSync(SHOTS, {recursive: true});
  fs.writeFileSync(path.join(SHOTS, name + ".png"), Buffer.from(result.data, "base64"));
}
async function viewport(width, height, mobile) {
  await send("Emulation.setDeviceMetricsOverride", {width, height, deviceScaleFactor: 1, mobile});
  await send("Emulation.setUserAgentOverride", {userAgent: mobile
    ? "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36", platform: mobile ? "iPhone" : "Win32"});
}
async function navigate(url) {
  await evaluate("window.__previousPage = true").catch(() => {});
  await send("Page.navigate", {url});
  await waitFor("!window.__previousPage && document.readyState === 'complete'", "navigation to " + url);
}
async function open(pathname) {
  await navigate(origin + pathname);
  await waitFor("(!document.getElementById('plan').hidden || !document.getElementById('notice').hidden)", "page ready " + pathname);
  await sleep(150);
}
async function noSecretAnywhere(label) {
  const dump = await evaluate("document.documentElement.outerHTML + JSON.stringify([...document.querySelectorAll('input,textarea')].map(n => n.value)) + JSON.stringify(Object.assign({}, localStorage)) + JSON.stringify(Object.assign({}, sessionStorage)) + document.cookie");
  check(!dump.includes(SECRET), label + ": key absent from page, fields and storage");
  check(!dump.includes(LEAK), label + ": server text absent from page");
}
function plan(choices) { return lib.recommend(choices, catalog); }

/* ---------- scenarios ---------- */
async function hostedDesktop() {
  await viewport(1280, 900, false);
  mock.requests = [];
  await open("/hosted/setup.html");
  equal(await evaluate("[role.value, budget.value, existing.value]"), ["office", "0", "none"], "hosted defaults");
  equal(await text("plan-title"), plan(catalog.defaults).title, "hosted default title matches recommend()");
  equal(await text("mode-badge"), "PLANNER", "hosted badge");
  check(await visible("get"), "hosted shows the Windows card");
  check(!(await visible("local")) && !(await visible("phone")), "hosted hides local and phone cards");
  const ready = catalog.release.status === "available";
  check((await visible("download")) === ready && (await visible("download-pending")) === !ready, "download availability follows the catalog");
  equal(await evaluate("document.getElementById('source-zip').href"), catalog.release.source_zip, "source ZIP link");
  equal(await evaluate("[...document.querySelectorAll('.agent-guide')].map(a => a.href)"), [catalog.release.agent_guide, catalog.release.agent_guide], "agent guide links");
  equal(await evaluate("document.getElementById('feedback').href"), catalog.release.feedback, "feedback link");
  equal(await evaluate("document.querySelectorAll('input[type=password], #key-input').length"), 0, "hosted page has no key field");
  equal(await evaluate("document.querySelectorAll('select').length"), 3, "exactly three dropdowns");
  check(await evaluate("document.querySelector('[data-action=\"import-choices\"]').offsetParent === null"), "hosted page has no load-choices button");
  equal(await evaluate("document.querySelectorAll('input[type=range], select[multiple]').length"), 0, "no sliders or multiselects");
  await shot("hosted-desktop");
  for (const choices of [{role: "developer", budget: 50, existing: "openai"}, {role: "study", budget: 0, existing: "ollama"}, {role: "creator", budget: 10, existing: "anthropic"}]) {
    await select("role", choices.role);
    await select("budget", choices.budget);
    await select("existing", choices.existing);
    const expected = plan(choices);
    equal(await text("plan-title"), expected.title, "title parity " + JSON.stringify(choices));
    equal(await evaluate("[...document.querySelectorAll('#plan-steps li span:first-child')].map(n => n.textContent)"), expected.steps, "steps parity " + JSON.stringify(choices));
    equal(await evaluate("[...document.querySelectorAll('#plan-tradeoffs li')].map(n => n.textContent)"), expected.tradeoffs, "tradeoffs parity " + JSON.stringify(choices));
    equal(await evaluate("document.getElementById('provider-link').href"), expected.setup_url, "provider link " + JSON.stringify(choices));
  }
  await noOverflow("hosted desktop");
  await open("/hosted/setup.html?role=study&budget=25&existing=ollama");
  equal(await evaluate("[role.value, budget.value, existing.value]"), ["study", "25", "ollama"], "linked choices restore");
  const evil = "<img src=x onerror=alert(1)>";
  await open("/hosted/setup.html?role=" + encodeURIComponent(evil) + "&budget=0&existing=none");
  equal(await text("notice"), MESSAGES.query, "invalid link shows fixed notice");
  check(!(await evaluate("document.body.innerHTML.includes('onerror')")), "invalid link value is not echoed");
  equal(await evaluate("role.value"), "office", "invalid link falls back to defaults");
  await click('#get [data-action="save-json"]');
  equal(await text("status"), MESSAGES.savedJson, "save JSON status");
  equal(mock.requests.length, 0, "hosted page made no API requests");
  mock.requests = [];
  await navigate(origin.replace("127.0.0.1", "localhost") + "/setup");
  await waitFor("!document.getElementById('plan').hidden", "localhost page");
  await sleep(200);
  equal(mock.requests.length, 0, "auto page on a non-loopback-IP host made no API requests");
  check(!(await visible("local")), "auto page on localhost name stays a planner");
}

async function hostedPhone() {
  await viewport(390, 844, true);
  mock.requests = [];
  await open("/hosted/setup.html");
  equal(await text("mode-badge"), "PHONE PLANNER", "phone badge");
  check(await visible("phone"), "phone card visible");
  check(!(await visible("get")) && !(await visible("local")), "phone hides download and local cards");
  await select("role", "developer");
  await select("budget", "10");
  const link = await text("handoff-link");
  equal(new URL(link).search, "?role=developer&budget=10&existing=none", "handoff link carries the three choices only");
  await click('#phone [data-action="copy-link"]');
  await waitFor("document.getElementById('status').textContent.length > 0", "copy status");
  check([MESSAGES.copied, MESSAGES.copyFailed].includes(await text("status")), "copy link gives a fixed message");
  check(await evaluate("document.querySelector('#phone a[href=\"index.html#practice\"]') !== null"), "phone links to practice");
  await noOverflow("phone");
  await shot("hosted-phone");
  equal(mock.requests.length, 0, "phone page made no API requests");
}

function route(key, handler) { mock.routes[key] = handler; }
function last(pathname) { return mock.requests.filter((item) => item.path === pathname).pop(); }

async function localFlow() {
  await viewport(1280, 900, false);
  mock.routes = {};
  mock.state = freshState();
  mock.requests = [];
  await open("/setup");
  await waitFor("document.getElementById('mode-badge').textContent === 'ON THIS COMPUTER'", "local badge");
  check(await visible("local"), "local card visible");
  check(!(await visible("get")) && !(await visible("phone")), "local hides download and phone cards");
  check(await visible("key-input"), "key field shown for a cloud recommendation");
  equal(await evaluate("[ki => ki.type, ki => ki.autocomplete].map(f => f(document.getElementById('key-input')))"), ["password", "off"], "key field is masked with autocomplete off");
  equal(await evaluate("[...document.querySelectorAll('#plan-steps li.done')].length"), 2, "download and start steps marked done locally");
  equal(mock.requests.map((r) => r.method + " " + r.path), ["GET /api/session", "GET /api/quickstart"], "local load only reads session and state");
  check(await evaluate("document.getElementById('launch').disabled"), "launch disabled before install is ready");

  await select("role", "developer");
  await waitFor("true");
  await sleep(100);
  const save = last("/api/quickstart/save");
  equal(save && save.body, {choices: {role: "developer", budget: 0, existing: "none"}}, "save sends exact choices");
  equal(save && save.token, TOKEN, "save sends the session token");

  route("POST /api/quickstart/save", (req, res) => json(res, 500, {error: LEAK}));
  await select("budget", "25");
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.save)}`, "save error");
  equal(await evaluate("[role.value, budget.value, existing.value]"), ["developer", "0", "none"], "failed save rolls the display back");
  delete mock.routes["POST /api/quickstart/save"];

  // Key: empty, failure, success.
  await click('#key-form button[type="submit"]');
  equal(await text("status"), MESSAGES.keyEmpty, "empty key message");
  route("POST /api/quickstart/key", (req, res) => json(res, 400, {error: LEAK + " " + SECRET}));
  await evaluate("document.getElementById('key-input').focus()");
  await send("Input.insertText", {text: SECRET});
  await click('#key-form button[type="submit"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.key)}`, "key error");
  equal(await evaluate("document.getElementById('key-input').value"), "", "key field cleared after failure");
  equal(last("/api/quickstart/key").body, {provider: "nvidia", key: SECRET}, "key request body");
  await noSecretAnywhere("after key failure");
  route("POST /api/quickstart/key", (req, res, body) => { mock.state.credentials[body.provider] = true; json(res, 200, mock.state); });
  await evaluate("document.getElementById('key-input').focus()");
  await send("Input.insertText", {text: SECRET});
  await click('#key-form button[type="submit"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.keySaved)}`, "key saved");
  check(await visible("key-saved") && !(await visible("key-form")), "saved key replaces the field");
  equal(await evaluate("document.getElementById('key-input').value"), "", "key field cleared after success");
  await noSecretAnywhere("after key save");
  check(await visible("ai-box"), "AI chat offered once a key is saved");
  await shot("local-desktop");

  // Test connection and chat.
  await evaluate("document.getElementById('ai-box').open = true");
  const before = mock.requests.length;
  await click('[data-action="test"]');
  equal(await text("status"), MESSAGES.consent, "test needs consent");
  equal(mock.requests.length, before, "no test request without consent");
  await click("#consent");
  route("POST /api/quickstart/test", (req, res) => json(res, 502, {error: LEAK}));
  await click('[data-action="test"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.test)}`, "test error");
  equal(last("/api/quickstart/test").body, {provider: "nvidia", consent: true}, "test body");
  route("POST /api/quickstart/test", (req, res) => json(res, 200, {ok: true}));
  await click('[data-action="test"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.tested)}`, "test ok");
  await click('[data-action="chat"]');
  equal(await text("status"), MESSAGES.promptEmpty, "chat needs a prompt");
  await evaluate("document.getElementById('prompt').value = 'Hello'");
  route("POST /api/quickstart/chat", (req, res) => json(res, 500, {error: LEAK}));
  await click('[data-action="chat"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.chat)}`, "chat error");
  const reply = "<img src=x onerror=window.pwned=1> plain reply";
  route("POST /api/quickstart/chat", (req, res) => json(res, 200, {reply}));
  await click('[data-action="chat"]');
  await waitFor("!document.getElementById('reply').hidden", "chat reply");
  equal(await text("reply"), reply, "reply shown as text");
  equal(await evaluate("[document.querySelectorAll('#reply img').length, window.pwned === undefined]"), [0, true], "reply is not parsed as HTML");
  equal(last("/api/quickstart/chat").body, {provider: "nvidia", prompt: "Hello", consent: true}, "chat body");

  // Key delete.
  route("POST /api/quickstart/key/delete", (req, res) => json(res, 500, {error: LEAK}));
  await click('[data-action="key-delete"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.keyDelete)}`, "delete error");
  route("POST /api/quickstart/key/delete", (req, res, body) => { mock.state.credentials[body.provider] = false; json(res, 200, mock.state); });
  await click('[data-action="key-delete"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.keyDeleted)}`, "delete ok");
  check(await visible("key-input"), "field returns after delete");
  equal(last("/api/quickstart/key/delete").body, {provider: "nvidia"}, "delete body");

  // Install: failure, then running to ready, then error state.
  route("POST /api/quickstart/install", (req, res) => json(res, 500, {error: LEAK}));
  await click('[data-action="install"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.install)}`, "install error");
  let polls = 0;
  route("POST /api/quickstart/install", (req, res) => { mock.state.install = {state: "running", message: LEAK}; json(res, 200, mock.state); });
  route("GET /api/quickstart", (req, res) => { polls += 1; if (polls >= 2) mock.state.install = {state: "ready", message: LEAK}; json(res, 200, mock.state); });
  await click('[data-action="install"]');
  equal(last("/api/quickstart/install").body, {consent: true}, "install body");
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installRunning)}`, "install running");
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installReady)}`, "install ready", 10000);
  equal(await text("install"), "Install again", "install button after ready");
  delete mock.routes["GET /api/quickstart"];
  mock.state.install = {state: "error", message: LEAK};
  route("POST /api/quickstart/install", (req, res) => { mock.state.install = {state: "error", message: LEAK}; json(res, 200, mock.state); });
  await click('[data-action="install"]');
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installError)}`, "install error state");
  await noSecretAnywhere("after install");

  check(await evaluate("document.getElementById('launch').disabled"), "launch disabled after a failed install");
  mock.installOutcome = "ready";
  delete mock.routes["POST /api/quickstart/install"];
  await click('[data-action="install"]');
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installReady)}`, "install ready again");
  check(!(await evaluate("document.getElementById('launch').disabled")), "launch enabled once install is ready");

  // Launch: failure, unsafe address, exit failure, exit.
  route("POST /api/quickstart/launch", (req, res) => json(res, 500, {error: LEAK}));
  await click('[data-action="launch"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.launch)}`, "launch error");
  for (const url of ["http://evil.example/", "javascript:alert(1)", "http://127.0.0.1:8080/x", "http://127.0.0.1.evil.example:80/"]) {
    route("POST /api/quickstart/launch", (req, res) => json(res, 200, {url}));
    await evaluate("document.getElementById('status').textContent = ''");
    await click('[data-action="launch"]');
    await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.launch)}`, "unsafe launch " + url);
    check((await evaluate("location.pathname")) === "/setup", "no navigation to " + url);
  }
  equal(last("/api/quickstart/launch").body, {consent: true}, "launch body");
  route("POST /api/setup/exit", (req, res) => json(res, 500, {error: LEAK}));
  await click('[data-action="exit"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.exit)}`, "exit error");
  route("POST /api/setup/exit", (req, res) => json(res, 200, {ok: true}));
  await click('[data-action="exit"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.exited)}`, "exit ok");
  equal(last("/api/setup/exit").body, {}, "exit body");
  check(await evaluate("[...document.querySelectorAll('button, select, input, textarea')].every(n => n.disabled)"), "controls disabled after exit");
  await noSecretAnywhere("end of local flow");
  check(mock.requests.filter((r) => r.method === "POST").every((r) => r.token === TOKEN), "every POST carries the token");
  check(mock.requests.every((r) => r.host === new URL(origin).host), "every API call stays on the page origin");

  // Safe launch navigates to the returned loopback address.
  mock.routes = {};
  mock.state = freshState({choices: {role: "office", budget: 0, existing: "none"}, install: {state: "ready", message: LEAK}});
  await open("/setup");
  route("POST /api/quickstart/launch", (req, res) => json(res, 200, {url: origin + "/"}));
  await click('[data-action="launch"]');
  await waitFor("location.pathname === '/' && document.getElementById('dash') !== null", "safe launch navigation");
  check(true, "safe launch opened the dashboard address");
}


async function waitIdle(label) {
  await waitFor("!document.getElementById('role').disabled", label || "dropdowns unlocked", 10000);
  await sleep(50);
}
async function setImportFile(name, content) {
  const file = path.join(importDir, name);
  fs.writeFileSync(file, content);
  const handle = await send("Runtime.evaluate", {expression: "document.getElementById('import-choices')"});
  await send("DOM.setFileInputFiles", {files: [file], objectId: handle.result.objectId});
}

/* Regressions: fresh defaults reach the service before install, saves never overlap, failed saves roll
   back, the dropdowns lock during an install, and saved choices files are validated before use. */
async function localRegressions() {
  await viewport(1280, 900, false);
  mock.routes = {};
  mock.installOutcome = "ready";
  mock.state = freshState();
  mock.requests = [];
  await open("/setup");
  await waitFor("document.getElementById('mode-badge').textContent === 'ON THIS COMPUTER'", "local badge regressions");
  await click('[data-action="install"]');
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installReady)}`, "fresh install ready");
  equal(mock.requests.filter((r) => r.method === "POST").map((r) => [r.path, r.body]),
    [["/api/quickstart/save", {choices: catalog.defaults}], ["/api/quickstart/install", {consent: true}]], "fresh defaults are saved before install");
  equal(await text("status"), "", "fresh install shows no error");

  // Saves are serialized: a second change while one is pending is refused and the display follows the service.
  mock.state = freshState({choices: catalog.defaults});
  await open("/setup");
  mock.requests = [];
  mock.maxInflight = 0;
  mock.saveDelay = 400;
  await select("role", "developer");
  check(await evaluate("['role','budget','existing'].every(id => document.getElementById(id).disabled)"), "dropdowns locked while a save is pending");
  await select("budget", "25");
  await select("existing", "openai");
  await waitIdle("serialized save");
  mock.saveDelay = 0;
  equal(mock.maxInflight, 1, "saves never overlap");
  equal(mock.requests.filter((r) => r.path === "/api/quickstart/save").map((r) => r.body.choices), [{role: "developer", budget: 0, existing: "none"}], "only the first change was sent");
  equal(await evaluate("[role.value, budget.value, existing.value]"), ["developer", "0", "none"], "display matches the saved choices");
  equal(await text("plan-title"), lib.recommend(mock.state.choices, catalog).title, "plan matches the service choices");
  for (const [id, value] of [["budget", "10"], ["existing", "anthropic"], ["role", "creator"]]) {
    await select(id, value);
    await waitIdle("sequential save " + id);
  }
  equal(mock.state.choices, {role: "creator", budget: 10, existing: "anthropic"}, "sequential changes all reach the service in order");
  equal(await evaluate("[role.value, budget.value, existing.value]"), ["creator", "10", "anthropic"], "display after sequential changes");

  // Failed save rolls back to the service choices.
  route("POST /api/quickstart/save", (req, res) => json(res, 500, {error: LEAK}));
  await select("existing", "ollama");
  await waitIdle("failed save");
  equal(await text("status"), MESSAGES.save, "failed save message");
  equal(await evaluate("[role.value, budget.value, existing.value]"), ["creator", "10", "anthropic"], "failed save restores the service choices");
  equal(await text("plan-title"), lib.recommend(mock.state.choices, catalog).title, "failed save restores the plan");
  // A failed pre-install save blocks the install.
  mock.requests = [];
  await click('[data-action="install"]');
  await waitIdle("blocked install");
  equal(mock.requests.filter((r) => r.path === "/api/quickstart/install").length, 0, "no install when the save before it fails");
  delete mock.routes["POST /api/quickstart/save"];

  // While installing, the dropdowns stay locked and nothing is saved.
  mock.installOutcome = "running";
  mock.requests = [];
  await click('[data-action="install"]');
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installRunning)}`, "install running regression");
  check(await evaluate("['role','budget','existing'].every(id => document.getElementById(id).disabled) && document.getElementById('launch').disabled"), "dropdowns and launch locked during install");
  const saves = mock.requests.filter((r) => r.path === "/api/quickstart/save").length;
  await select("role", "office");
  await sleep(200);
  equal(mock.requests.filter((r) => r.path === "/api/quickstart/save").length, saves, "no save sent during install");
  equal(await evaluate("role.value"), "creator", "display unchanged during install");
  mock.state.install = {state: "ready", message: LEAK};
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installReady)}`, "install finished regression");
  await waitIdle("unlocked after install");
  mock.installOutcome = "ready";

  // Load saved choices: bad files rejected with a fixed message, a valid file saved through the service.
  check(await evaluate("document.querySelector('[data-action=\"import-choices\"]').offsetParent !== null && document.getElementById('import-choices').hidden"), "load button shown, file input hidden");
  const pii = "alice.private@example.com";
  const valid = {schema: lib.CHOICES_SCHEMA, choices: {role: "study", budget: 25, existing: "nvidia"}};
  const bad = {
    "too-large.json": JSON.stringify(Object.assign({}, valid, {pad: "x".repeat(3000)})),
    "duplicate.json": '{"schema":"' + lib.CHOICES_SCHEMA + '","choices":{"role":"study","role":"office","budget":0,"existing":"none"}}',
    "unknown-field.json": JSON.stringify(Object.assign({}, valid, {email: pii})),
    "extra-choice.json": JSON.stringify({schema: lib.CHOICES_SCHEMA, choices: Object.assign({}, valid.choices, {key: pii})}),
    "wrong-schema.json": JSON.stringify({schema: "jarvis-setup-answers/1", choices: valid.choices}),
    "bad-value.json": JSON.stringify({schema: lib.CHOICES_SCHEMA, choices: {role: pii, budget: 0, existing: "none"}}),
    "deep.json": "[".repeat(40) + "]".repeat(40),
    "not-json.txt": "role=study " + pii,
  };
  for (const [name, content] of Object.entries(bad)) {
    mock.requests = [];
    await evaluate("document.getElementById('status').textContent = ''");
    await setImportFile(name, content);
    await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.importBad)}`, "import rejected " + name);
    await waitIdle("import " + name);
    equal(mock.requests.filter((r) => r.method === "POST").length, 0, "no request for " + name);
    check(!(await evaluate("document.body.innerHTML.includes(" + JSON.stringify(pii) + ")")), "file content not echoed for " + name);
    equal(await evaluate("[role.value, budget.value, existing.value]"), ["creator", "10", "anthropic"], "choices unchanged by " + name);
  }
  mock.requests = [];
  await setImportFile("valid.json", JSON.stringify(valid, null, 2));
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.imported)}`, "valid import");
  await waitIdle("valid import");
  equal(mock.requests.filter((r) => r.method === "POST").map((r) => [r.path, r.body]), [["/api/quickstart/save", {choices: valid.choices}]], "valid file saved through the service only");
  equal(await evaluate("[role.value, budget.value, existing.value]"), ["study", "25", "nvidia"], "valid file restores the dropdowns");
  mock.routes = {};
}

async function localVariants() {
  // Saved choices from the service restore, local provider hides the key field.
  mock.routes = {};
  mock.state = freshState({choices: {role: "creator", budget: 10, existing: "ollama"}});
  await viewport(390, 844, true);
  await open("/setup");
  await waitFor("document.getElementById('mode-badge').textContent === 'ON THIS COMPUTER'", "local badge mobile");
  equal(await evaluate("[role.value, budget.value, existing.value]"), ["creator", "10", "ollama"], "saved choices restore");
  check(!(await visible("key-box")), "no key field for Ollama");
  check(await visible("ai-box"), "AI chat offered for Ollama");
  check(!(await visible("phone")), "local mode on a small screen is not the phone planner");
  await noOverflow("local mobile");
  await shot("local-mobile");
  await viewport(1280, 900, false);

  // Session failure.
  mock.routes = {"GET /api/session": (req, res) => json(res, 500, {error: LEAK})};
  mock.requests = [];
  await open("/setup");
  equal(await text("notice"), MESSAGES.local, "session failure notice");
  check(!(await visible("local")), "no local controls without a session");
  equal(mock.requests.map((r) => r.path), ["/api/session"], "nothing else called after session failure");

  // Service answering without local:true.
  mock.routes = {"GET /api/quickstart": (req, res) => json(res, 200, Object.assign(freshState(), {local: false}))};
  mock.requests = [];
  await open("/setup");
  equal(await text("notice"), MESSAGES.local, "non-local state notice");
  check(!(await visible("local")), "no local controls when the service is not local");
  await select("role", "study");
  await sleep(100);
  equal(mock.requests.filter((r) => r.method === "POST").length, 0, "no POST when local mode is not confirmed");
  mock.routes = {};
}

async function websitePages() {
  // The public landing page keeps the hand guide and practice pad and stays short.
  await viewport(1280, 900, false);
  await navigate(origin + "/site/index.html");
  equal(await evaluate("document.querySelectorAll('.gesture-card svg').length"), 4, "four hand graphics retained");
  check(await evaluate("document.getElementById('pad-stage') !== null"), "practice pad retained");
  equal(await evaluate("document.querySelectorAll('input[type=password]').length"), 0, "site has no key field");
  check(await evaluate("[...document.querySelectorAll('a')].some(a => a.href === " + JSON.stringify(catalog.release.feedback) + ")"), "site feedback link");
  check(await evaluate("[...document.querySelectorAll('a')].some(a => a.href === " + JSON.stringify(catalog.release.agent_guide) + ")"), "site agent guide link");
  await click('[data-pad="watch"]');
  await click('[data-pad="open"]');
  check((await text("pad-status")).startsWith("Hand found"), "practice pad still works");
  await noOverflow("site desktop");
  await shot("site-desktop");
  await viewport(390, 844, true);
  await navigate(origin + "/site/index.html");
  await noOverflow("site mobile");
  await shot("site-mobile");
}

async function main() {
  const browser = findBrowser();
  if (!browser) { console.log("SKIP: no Chromium-based browser found (set JARVIS_TEST_BROWSER)"); return; }
  if (typeof WebSocket !== "function") { console.log("SKIP: Node WebSocket API is unavailable"); return; }
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  origin = "http://127.0.0.1:" + server.address().port;
  allowedOrigins.add(origin);
  allowedOrigins.add(origin.replace("127.0.0.1", "localhost"));
  const site = path.join(ROOT, "website");
  const baseHandler = server.listeners("request")[0];
  server.removeAllListeners("request");
  server.on("request", (req, res) => {
    const p = new URL(req.url, "http://127.0.0.1").pathname;
    if (p.startsWith("/site/")) {
      const name = p.slice(6);
      if (["index.html", "site.css", "site.js", "privacy.html", "favicon.svg"].includes(name)) return serveFile(res, path.join(site, name));
      if (name === "dashboard-preview.png") { res.writeHead(200, {"Content-Type": "image/png"}); return res.end(fs.readFileSync(path.join(site, name))); }
    }
    return baseHandler(req, res);
  });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "jarvis-qs-"));
  const child = spawn(browser, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + profile, "--no-first-run",
    "--no-default-browser-check", "--disable-extensions", "--disable-background-networking", "--disable-sync", "--disable-component-update",
    "--use-fake-ui-for-media-stream", "--deny-permission-prompts", "about:blank"], {stdio: "ignore"});
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
        const ok = url.startsWith("data:") || [...allowedOrigins].some((item) => url === item || url.startsWith(item + "/"));
        if (!ok) external.push(url);
        const reply = ok ? {method: "Fetch.continueRequest", params: {requestId: msg.params.requestId}} : {method: "Fetch.failRequest", params: {requestId: msg.params.requestId, errorReason: "BlockedByClient"}};
        ws.send(JSON.stringify({id: ++nextId, method: reply.method, params: reply.params, sessionId: msg.sessionId}));
      }
      if (msg.method === "Runtime.consoleAPICalled") consoleText.push(JSON.stringify(msg.params.args));
      if (msg.method === "Runtime.exceptionThrown") exceptions.push(JSON.stringify(msg.params.exceptionDetails));
    });
    session = undefined;
    const target = await send("Target.createTarget", {url: "about:blank"});
    session = (await send("Target.attachToTarget", {targetId: target.targetId, flatten: true})).sessionId;
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Fetch.enable", {patterns: [{urlPattern: "*"}]});
    await send("Browser.setPermission", {permission: {name: "clipboard-write"}, setting: "denied"}).catch(() => {});
    await hostedDesktop();
    await hostedPhone();
    await localFlow();
    await localRegressions();
    await localVariants();
    await websitePages();
    equal(external, [], "no requests left the local fixture server");
    check(!consoleText.join(" ").includes(SECRET), "key never logged to the console");
    equal(exceptions, [], "no uncaught page exceptions");
  } finally {
    try { ws && ws.close(); } catch (error) { /* closing */ }
    child.kill();
    server.close();
    await sleep(300);
    try { fs.rmSync(profile, {recursive: true, force: true}); } catch (error) { /* browser may still hold files */ }
    try { fs.rmSync(importDir, {recursive: true, force: true}); } catch (error) { /* best effort */ }
  }
  console.log(JSON.stringify({passed, failures}, null, 1));
  if (failures.length) process.exitCode = 1;
}

main().catch((error) => { console.error(error && error.stack || String(error)); console.log(JSON.stringify({passed, failures})); process.exitCode = 1; });
