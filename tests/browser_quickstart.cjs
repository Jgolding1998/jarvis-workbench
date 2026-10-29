"use strict";
/* Headless browser checks for the one-question-per-screen web/setup.html with a mocked local API.
   Usage: node tests/browser_quickstart.cjs [screenshot-dir]
   Prints "SKIP: ..." and exits 0 when no Chromium-based browser is installed.
   Never touches a real Jarvis service, camera, microphone, cursor, speaker or provider: every request is
   answered by the in-process mock, speech synthesis is replaced by a recording fake, and any request to
   another origin fails the run. */
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
const STEPS = ["role", "budget", "existing", "dictation", "reply", "automation"];
const PARTS = {desk: "Desk", ai: "AI chat", input: "Talk or type", reply: "Replies", automation: "Automation"};
const TAGS = {builtin: "In Jarvis", external: "Outside Jarvis", manual: "Manual setup", later: "Not built in", off: "Off"};
const full = (choices) => lib.validateChoices(choices, catalog);

/* Replaces speech synthesis with a recorder and makes any microphone request visible. Installed before page scripts. */
const SPEECH_FAKE = `(() => {
  const record = {spoken: [], cancels: 0, mic: 0, voices: [
    {name: "Remote voice", lang: "en-US", localService: false, default: true},
    {name: "Local voice", lang: "en-US", localService: true, default: false}]};
  window.__speech = record;
  const synth = {getVoices: () => record.voices, speak: (u) => { record.spoken.push({text: u.text, voice: u.voice && u.voice.name}); record.current = u; },
    cancel: () => { record.cancels += 1; }, speaking: false, pending: false};
  Object.defineProperty(window, "speechSynthesis", {value: synth, configurable: true});
  window.SpeechSynthesisUtterance = function (text) { this.text = text; };
  if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => { record.mic += 1; return Promise.reject(new Error("blocked")); };
  for (const name of ["SpeechRecognition", "webkitSpeechRecognition"]) Object.defineProperty(window, name, {get: () => { record.mic += 1; return undefined; }, configurable: true});
})();`;

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
    install: {state: "idle", message: LEAK}, providers: [], read_aloud_allowed: true}, overrides || {});
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
mock.catalogText = null;
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
    if (name === "quickstart-catalog.json" && mock.catalogText !== null) { res.writeHead(200, {"Content-Type": "application/json"}); return res.end(mock.catalogText); }
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
let session;
function send(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, {resolve, reject});
    ws.send(JSON.stringify({id, method, params: params || {}, sessionId: session}));
  });
}
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
const focused = () => evaluate("document.activeElement && document.activeElement.id");
const currentStep = () => evaluate("(() => { if (!document.getElementById('result').hidden) return 'plan'; const s = [...document.querySelectorAll('#wizard .step')].filter(n => !n.hidden); return s.length === 1 ? s[0].dataset.step : 'steps:' + s.length; })()");
async function select(id, value) {
  await evaluate(`(() => { const s = document.getElementById(${JSON.stringify(id)}); s.value = ${JSON.stringify(String(value))}; s.dispatchEvent(new Event("change", {bubbles: true})); })()`);
}
async function typeBudget(value) {
  await evaluate(`(() => { const s = document.getElementById("budget-exact"); s.value = ${JSON.stringify(String(value))}; s.dispatchEvent(new Event("input", {bubbles: true})); })()`);
}
async function click(selector) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
}
async function key(name) {
  const codes = {ArrowRight: 39, ArrowLeft: 37, Home: 36, End: 35, Escape: 27, Enter: 13, Tab: 9};
  for (const type of ["keyDown", "keyUp"]) await send("Input.dispatchKeyEvent", {type, key: name, code: name, windowsVirtualKeyCode: codes[name]});
  await sleep(30);
}
async function nextStep(expected) {
  await click("#next");
  await waitFor(`(() => { const r = !document.getElementById('result').hidden; const s = [...document.querySelectorAll('#wizard .step')].find(n => !n.hidden); return ${JSON.stringify(expected)} === (r ? 'plan' : s && s.dataset.step); })()`, "step " + expected);
  await sleep(30);
  equal(await focused(), expected === "plan" ? "plan-title" : expected + "-title", "Next focuses the question heading, never its picker: " + expected);
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
  await waitFor("(!document.getElementById('wizard').hidden || !document.getElementById('result').hidden || !document.getElementById('notice').hidden)", "page ready " + pathname);
  await sleep(150);
}
async function noSecretAnywhere(label) {
  const dump = await evaluate("document.documentElement.outerHTML + JSON.stringify([...document.querySelectorAll('input,textarea')].map(n => n.value)) + JSON.stringify(Object.assign({}, localStorage)) + JSON.stringify(Object.assign({}, sessionStorage)) + document.cookie + JSON.stringify(history.state)");
  check(!dump.includes(SECRET), label + ": key absent from page, fields, storage and history");
  check(!dump.includes(LEAK), label + ": server text absent from page");
}
function plan(choices) { return lib.recommend(choices, catalog); }
const stackRows = () => evaluate("[...document.querySelectorAll('#plan-stack li')].map(li => [li.querySelector('.part').textContent, li.querySelector('.name span').textContent, li.querySelector('.tag').textContent, (li.querySelector('.guide') || {}).href || null])");
const expectedRows = (choices) => plan(choices).stack.map((row) => [PARTS[row.part], row.name, TAGS[row.status], row.url || null]);
async function planMatches(choices, label) {
  const expected = plan(choices);
  equal(await text("plan-title"), expected.title, "title parity " + label);
  equal(await text("plan-summary"), expected.summary, "summary parity " + label);
  equal(await stackRows(), expectedRows(choices), "stack parity " + label);
  equal(await evaluate("[...document.querySelectorAll('#plan-steps li span:first-child')].map(n => n.textContent)"), expected.steps, "steps parity " + label);
  equal(await evaluate("[...document.querySelectorAll('#plan-tradeoffs li')].map(n => n.textContent)"), expected.tradeoffs, "tradeoffs parity " + label);
  equal(await evaluate("document.getElementById('provider-link').href"), expected.setup_url, "provider link " + label);
  const limit = catalog.providers[expected.provider].limit_url || null;
  equal(await evaluate("document.getElementById('limit-help').hidden ? null : document.getElementById('limit-link').href"), limit, "hard limit link " + label);
}
/* Walks the questions with the given answers using the Next button. Leaves the page on the plan, or on the
   last question when stopBeforePlan is set. */
async function walk(answers, stopBeforePlan) {
  for (let index = 0; index < STEPS.length; index++) {
    const step = STEPS[index];
    if (own(answers, step)) {
      if (step === "budget") await typeBudget(answers.budget);
      else await select(step, answers[step]);
    }
    if (index === STEPS.length - 1 && stopBeforePlan) return;
    await nextStep(index === STEPS.length - 1 ? "plan" : STEPS[index + 1]);
  }
}
const own = (object, name) => Object.prototype.hasOwnProperty.call(object, name);

/* ---------- scenarios ---------- */
async function hostedDesktop() {
  await viewport(1280, 900, false);
  mock.requests = [];
  await open("/hosted/setup.html");
  equal(await text("mode-badge"), "PLANNER", "hosted badge");
  equal(await currentStep(), "role", "hosted opens on the first question");
  equal(await text("progress"), "Question 1 of 6", "progress text");
  check(!(await visible("back")), "no Back button on the first question");
  equal(await evaluate("[...document.querySelectorAll('select, input[type=range], input[type=number]')].filter(n => n.getClientRects().length).length"), 1, "only one control visible on a question");
  check(!(await visible("result")), "plan hidden until the questions are answered");
  equal(await evaluate("document.querySelectorAll('svg.orb, .orb, .hud').length"), 0, "no reactor graphic or HUD panels");
  equal(await evaluate("document.querySelectorAll('input[type=password], #key-input').length"), 0, "hosted page has no key field");
  equal(await evaluate("[...document.getElementById('role').options].map(o => o.value)"), catalog.roles.map((r) => r.id), "purpose options from the catalog");
  equal(await text("role-hint"), catalog.roles.find((r) => r.id === "office").hint, "default purpose hint");
  equal(await evaluate("location.hash"), "#role", "first question has its own address");
  await shot("01-hosted-purpose");

  await select("role", "handsfree");
  equal(await text("role-hint"), catalog.roles.find((r) => r.id === "handsfree").hint, "purpose hint follows the choice");
  await nextStep("budget");
  equal(await focused(), "budget-title", "focus moves to the question heading");
  await key("Tab");
  equal(await focused(), "budget", "the slider remains next in keyboard order");
  equal(await text("progress"), "Question 2 of 6", "progress on question 2");
  equal(await evaluate("location.hash"), "#budget", "question 2 address");
  check(await visible("back"), "Back shown from question 2");
  equal(await evaluate("(() => { const b = document.getElementById('budget'); return [b.type, b.min, b.max, b.step, b.value]; })()"), ["range", "0", "1000", "1", "0"], "slider is 0 to 1000 whole dollars");
  equal(await evaluate("(() => { const b = document.getElementById('budget-exact'); return [b.type, b.min, b.max, b.step]; })()"), ["number", "0", "10000", "1"], "exact amount field allows up to 10000");
  equal(await text("budget-value"), "$0, free only", "$0 is labelled free");
  for (let i = 0; i < 3; i++) await key("ArrowRight");
  equal(await evaluate("[budget.value, document.getElementById('budget-exact').value, document.getElementById('budget-value').textContent, budget.getAttribute('aria-valuetext')]"), ["3", "3", "$3 a month", "$3 a month"], "arrow keys move the slider by one dollar and fill the exact field");
  await key("End");
  equal(await evaluate("[budget.value, document.getElementById('budget-exact').value]"), ["1000", "1000"], "End reaches the slider maximum");
  await key("Home");
  equal(await evaluate("budget.value"), "0", "Home returns to $0");
  await typeBudget("5000");
  equal(await evaluate("[budget.value, document.getElementById('budget-value').textContent]"), ["1000", "$5,000 a month"], "typed amount above the slider pins the slider and shows the amount");
  await shot("02-hosted-budget");
  for (const bad of ["10001", "12.5", "", "-4", "007"]) {
    await typeBudget(bad);
    await click("#next");
    await sleep(60);
    equal(await currentStep(), "budget", "invalid amount " + JSON.stringify(bad) + " stays on the question");
    equal(await text("step-error"), lib.CHOICE_ERRORS.budget, "invalid amount " + JSON.stringify(bad) + " shows the fixed message");
    equal(await evaluate("[document.getElementById('budget-exact').getAttribute('aria-invalid'), document.activeElement.id]"), ["true", "budget-exact"], "invalid amount is marked and focused " + JSON.stringify(bad));
  }
  await typeBudget("2500");
  check(!(await visible("step-error")), "error clears once the amount is valid");
  await nextStep("existing");
  equal(await evaluate("[...document.getElementById('existing').options].map(o => o.value)"), catalog.existing.map((e) => e.id), "AI options from the catalog");
  await click("#back");
  await waitFor("location.hash === '#budget'", "Back button");
  equal(await evaluate("[document.getElementById('budget-exact').value, document.getElementById('budget-value').textContent]"), ["2500", "$2,500 a month"], "Back keeps the typed amount");
  equal(await focused(), "budget-title", "Back focuses the question heading without opening a control");
  await send("Runtime.evaluate", {expression: "history.back()"});
  await waitFor("location.hash === '#role'", "browser Back");
  equal(await currentStep(), "role", "browser Back shows question 1");
  await send("Runtime.evaluate", {expression: "history.forward()"});
  await waitFor("location.hash === '#budget'", "browser Forward");
  equal(await currentStep(), "budget", "browser Forward shows question 2");
  await nextStep("existing");
  await select("existing", "chat_only");
  equal(await text("existing-hint"), catalog.existing.find((e) => e.id === "chat_only").hint, "chat-only hint explains the subscription");
  await nextStep("dictation");
  equal(await evaluate("dictation.value"), "windows", "purpose suggests Windows voice typing for less mouse and keyboard");
  await select("dictation", "wispr");
  equal(await text("dictation-hint"), catalog.dictation.find((d) => d.id === "wispr").hint, "Wispr hint says there is no connector");
  await shot("04-hosted-dictation");
  await nextStep("reply");
  equal(await evaluate("reply.value"), "read_aloud", "purpose suggests read aloud for less mouse and keyboard");
  // A purpose change only re-suggests what the person has not picked.
  for (const step of ["dictation", "existing", "budget", "role"]) {
    await click("#back");
    await waitFor(`location.hash === '#${step}'`, "back to " + step);
  }
  await select("role", "developer");
  await nextStep("budget");
  await nextStep("existing");
  await nextStep("dictation");
  equal(await evaluate("dictation.value"), "wispr", "picked dictation survives a purpose change");
  await nextStep("reply");
  equal(await evaluate("reply.value"), "none", "unpicked reply follows the new purpose");
  await nextStep("automation");
  equal(await text("next"), "See my plan", "last question offers the plan");
  await select("automation", "agent_later");
  equal(await text("automation-hint"), catalog.automation.find((a) => a.id === "agent_later").hint, "MCP hint says not built in");
  await nextStep("plan");
  const chosen = {role: "developer", budget: 2500, existing: "chat_only", dictation: "wispr", reply: "none", automation: "agent_later"};
  equal(await focused(), "plan-title", "focus moves to the plan title");
  equal(await evaluate("location.hash"), "#plan", "plan has its own address");
  await planMatches(chosen, "walked plan");
  check(await visible("download") || await visible("download-pending"), "plan shows the download as the next step");
  check(!(await visible("local")) && !(await visible("phone")), "hosted plan hides local and phone panels");
  equal(await evaluate("[...document.querySelectorAll('#result details')].filter(d => d.open).length"), 0, "cost details start collapsed");
  check(!(await evaluate("document.getElementById('plan-tradeoffs').checkVisibility()")), "tradeoffs are not visible until opened");
  check((await stackRows()).some((row) => row[2] === "Not built in"), "unbuilt automation is labelled as not built in");
  equal(await evaluate("document.querySelectorAll('#result .local-only:not([hidden])').length"), 0, "local-only buttons stay hidden on the hosted page");
  equal(await evaluate("[document.getElementById('speak-row').closest('[hidden]') !== null, window.__speech.spoken.length, window.__speech.mic]"), [true, 0, 0], "hosted page never offers or uses speech or the microphone");
  await noOverflow("hosted plan");
  await shot("07-hosted-plan");
  await evaluate("document.querySelector('#result details').open = true");
  await shot("08-hosted-plan-details");
  await send("Runtime.evaluate", {expression: "history.back()"});
  await waitFor("location.hash === '#automation'", "browser Back from the plan");
  equal(await evaluate("automation.value"), "agent_later", "answers kept after Back from the plan");
  await send("Runtime.evaluate", {expression: "history.forward()"});
  await waitFor("location.hash === '#plan' && !document.getElementById('result').hidden", "browser Forward to the plan");
  await click('[data-action="change"]');
  await waitFor("location.hash === '#role'", "change answers");
  equal(await focused(), "role-title", "Change answers focuses the first heading without opening a picker");
  equal(await evaluate("role.value"), "developer", "Change answers keeps the answers");

  for (const choices of [{role: "developer", budget: 37, existing: "openai", dictation: "vosk", reply: "cloud_voice", automation: "calendar_email"},
    {role: "office", budget: 1, existing: "anthropic", dictation: "keyboard", reply: "read_aloud", automation: "none"},
    {role: "study", budget: 0, existing: "ollama", dictation: "windows", reply: "none", automation: "agent_later"},
    {role: "creator", budget: 10000, existing: "nvidia", dictation: "wispr", reply: "read_aloud", automation: "none"},
    {role: "handsfree", budget: 101, existing: "chat_only", dictation: "vosk", reply: "none", automation: "none"}]) {
    await open("/hosted/setup.html" + lib.choicesQuery(choices));
    equal(await currentStep(), "plan", "linked choices open the plan " + choices.role);
    await planMatches(choices, JSON.stringify(choices));
  }
  check((await stackRows()).some((row) => row[2] === "Manual setup" && row[3] === catalog.dictation.find((d) => d.id === "vosk").url), "offline voice is a manual setup with its guide");
  await open("/hosted/setup.html?role=study&budget=25&existing=ollama");
  equal(await currentStep(), "plan", "old three-choice link opens the plan");
  await planMatches(full({role: "study", budget: 25, existing: "ollama"}), "old link migrated");
  equal(new URL(await text("handoff-link")).search, lib.choicesQuery(full({role: "study", budget: 25, existing: "ollama"})), "handoff link carries all six answers");
  const evil = "<img src=x onerror=alert(1)>";
  for (const query of ["?role=" + encodeURIComponent(evil) + "&budget=0&existing=none", "?role=office&role=study&budget=0&existing=none", "?role=office&budget=10001&existing=none", "?role=office&budget=0&existing=none&reply=elevenlabs"]) {
    await open("/hosted/setup.html" + query);
    equal(await text("notice"), MESSAGES.query, "invalid link shows the fixed notice " + query.slice(0, 30));
    equal(await currentStep(), "role", "invalid link starts the questions " + query.slice(0, 30));
    check(!(await evaluate("document.body.innerHTML.includes('onerror')")), "invalid link value is not echoed");
  }
  equal(await evaluate("role.value"), "office", "invalid link falls back to defaults");
  await open("/hosted/setup.html?role=office&budget=0&existing=none");
  await click('[data-action="save-json"]');
  equal(await text("status"), MESSAGES.savedJson, "save JSON status");
  equal(mock.requests.length, 0, "hosted page made no API requests");
  mock.requests = [];
  await navigate(origin.replace("127.0.0.1", "localhost") + "/setup");
  await waitFor("!document.getElementById('wizard').hidden", "localhost page");
  await sleep(200);
  equal(mock.requests.length, 0, "auto page on a non-loopback-IP host made no API requests");
  check(!(await visible("local")), "auto page on localhost name stays a planner");

  // A malformed or duplicate-key catalog fails closed with the fixed message.
  for (const broken of [JSON.stringify(Object.assign({}, catalog, {extra: 1})), JSON.stringify(catalog).replace('"schema"', '"schema":"x","schema"'), "{"]) {
    mock.catalogText = broken;
    await navigate(origin + "/hosted/setup.html");
    await waitFor("!document.getElementById('notice').hidden", "broken catalog");
    equal([await text("notice"), await visible("wizard"), await visible("result")], [MESSAGES.catalog, false, false], "broken catalog shows only the fixed message");
  }
  mock.catalogText = null;
}

async function hostedPhone() {
  await viewport(390, 844, true);
  mock.requests = [];
  await open("/hosted/setup.html");
  equal(await text("mode-badge"), "PHONE PLANNER", "phone badge");
  await noOverflow("phone question");
  await shot("10-phone-purpose");
  await nextStep("budget");
  await noOverflow("phone budget");
  await shot("11-phone-budget");
  await click("#back");
  await waitFor("location.hash === '#role'", "phone back");
  await walk({budget: 10, existing: "nvidia"});
  check(await visible("phone"), "phone handoff shown on the plan");
  check(!(await visible("get")) && !(await visible("local")), "phone hides download and local panels");
  const choices = full({role: "office", budget: 10, existing: "nvidia", dictation: "windows"});
  equal(new URL(await text("handoff-link")).search, lib.choicesQuery(choices), "handoff link carries the answers only");
  await click('[data-action="copy-link"]');
  await waitFor("document.getElementById('status').textContent.length > 0", "copy status");
  check([MESSAGES.copied, MESSAGES.copyFailed].includes(await text("status")), "copy link gives a fixed message");
  check(await evaluate("document.querySelector('#phone a[href=\"practice.html#practice\"]') !== null"), "phone links to practice");
  await noOverflow("phone plan");
  await shot("12-phone-plan");
  equal(mock.requests.length, 0, "phone page made no API requests");
}

function route(name, handler) { mock.routes[name] = handler; }
function last(pathname) { return mock.requests.filter((item) => item.path === pathname).pop(); }

async function localFlow() {
  await viewport(1280, 900, false);
  mock.routes = {};
  mock.state = freshState();
  mock.requests = [];
  await open("/setup");
  await waitFor("document.getElementById('mode-badge').textContent === 'ON THIS COMPUTER'", "local badge");
  equal(await currentStep(), "role", "fresh local setup starts the questions");
  equal(mock.requests.map((r) => r.method + " " + r.path), ["GET /api/session", "GET /api/quickstart"], "local load only reads session and state");
  equal(await evaluate("document.querySelectorAll('#key-input').length"), 0, "no key field while answering questions");
  const answers = {role: "office", budget: 25, existing: "nvidia", dictation: "keyboard", reply: "read_aloud", automation: "none"};
  await walk(answers, true);
  equal(mock.requests.filter((r) => r.method === "POST").length, 0, "nothing saved before the last question");

  // A failed save keeps the last question open and recovers on retry.
  route("POST /api/quickstart/save", (req, res) => json(res, 500, {error: LEAK}));
  await click("#next");
  await waitFor(`document.getElementById('step-error').textContent === ${JSON.stringify(MESSAGES.save)}`, "save error");
  equal(await currentStep(), "automation", "failed save stays on the last question");
  check(!(await evaluate("document.getElementById('next').disabled")), "Next is usable again after a failed save");
  equal(await focused(), "automation-title", "a failed save focuses the heading without opening a picker");
  await shot("20-local-save-failed");
  delete mock.routes["POST /api/quickstart/save"];
  await nextStep("plan");
  const save = last("/api/quickstart/save");
  equal(save && save.body, {choices: answers}, "save sends all six answers");
  equal(save && save.token, TOKEN, "save sends the session token");
  check(await visible("key-input"), "key field shown on the local plan for a cloud provider");
  equal(await evaluate("[ki => ki.type, ki => ki.autocomplete].map(f => f(document.getElementById('key-input')))"), ["password", "off"], "key field is masked with autocomplete off");
  equal(await evaluate("[...document.querySelectorAll('#plan-steps li.done')].length"), 2, "download and start steps marked done locally");
  check(await evaluate("document.getElementById('launch').disabled"), "launch disabled before install is ready");
  await planMatches(answers, "local plan");

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
  await shot("21-local-plan");

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

  // Read aloud: offered only for the read-aloud answer, off until ticked, on-device voice only, stoppable.
  check(await visible("speak-row"), "read-aloud option shown for the read-aloud answer");
  equal(await evaluate("document.getElementById('speak').checked"), false, "read aloud is off until ticked");
  await click('[data-action="chat"]');
  await waitFor("!document.getElementById('chat-reply').hidden", "chat reply");
  equal(await text("chat-reply"), reply, "reply shown as text");
  equal(await evaluate("[document.querySelectorAll('#chat-reply img').length, window.pwned === undefined]"), [0, true], "reply is not parsed as HTML");
  equal(last("/api/quickstart/chat").body, {provider: "nvidia", prompt: "Hello", consent: true}, "chat body");
  equal(await evaluate("window.__speech.spoken.length"), 0, "nothing spoken without the tick");
  await click("#speak");
  await click('[data-action="chat"]');
  await waitFor("window.__speech.spoken.length === 1", "reply spoken");
  equal(await evaluate("window.__speech.spoken"), [{text: reply, voice: "Local voice"}], "reply spoken with the on-device voice, never the remote default");
  check(await visible("stop-speaking"), "Stop shown while speaking");
  let cancels = await evaluate("window.__speech.cancels");
  await key("Escape");
  check((await evaluate("window.__speech.cancels")) > cancels && !(await visible("stop-speaking")), "Escape cancels speech");
  equal(await text("status"), MESSAGES.stopped, "Escape reports that speech stopped");
  await click('[data-action="chat"]');
  await waitFor("window.__speech.spoken.length === 2", "second reply spoken");
  cancels = await evaluate("window.__speech.cancels");
  await click('[data-action="stop-speaking"]');
  check((await evaluate("window.__speech.cancels")) > cancels && !(await visible("stop-speaking")), "Stop cancels speech");
  await click('[data-action="chat"]');
  await waitFor("window.__speech.spoken.length === 3", "third reply spoken");
  cancels = await evaluate("window.__speech.cancels");
  await evaluate("Object.defineProperty(document, 'visibilityState', {get: () => 'hidden', configurable: true}); document.dispatchEvent(new Event('visibilitychange')); delete document.visibilityState; true");
  check((await evaluate("window.__speech.cancels")) > cancels, "hiding the page cancels speech");
  cancels = await evaluate("window.__speech.cancels");
  await evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))");
  check((await evaluate("window.__speech.cancels")) > cancels, "leaving the page cancels speech");
  await evaluate("window.__speech.voices = [{name: 'Remote voice', lang: 'en-US', localService: false, default: true}]");
  await click('[data-action="chat"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.noVoice)}`, "no local voice");
  equal(await evaluate("window.__speech.spoken.length"), 3, "no remote voice fallback");
  equal(await evaluate("window.__speech.mic"), 0, "microphone and speech recognition never requested");
  await click("#speak");
  await shot("22-local-chat-read-aloud");

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
  check(await evaluate("document.getElementById('change').disabled"), "answers cannot change during install");
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installReady)}`, "install ready", 10000);
  equal(await text("install"), "Install again", "install button after ready");
  check(!(await evaluate("document.getElementById('change').disabled")), "answers can change after install");
  delete mock.routes["GET /api/quickstart"];
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
  mock.state = freshState({choices: full({role: "office", budget: 0, existing: "none"}), install: {state: "ready", message: LEAK}});
  await open("/setup");
  route("POST /api/quickstart/launch", (req, res) => json(res, 200, {url: origin + "/"}));
  await click('[data-action="launch"]');
  await waitFor("location.pathname === '/' && document.getElementById('dash') !== null", "safe launch navigation");
  check(true, "safe launch opened the dashboard address");
}

async function waitIdle(label) {
  await waitFor("!document.getElementById('change').disabled", label || "idle", 10000);
  await sleep(50);
}
async function setImportFile(name, content) {
  const file = path.join(importDir, name);
  fs.writeFileSync(file, content);
  const handle = await send("Runtime.evaluate", {expression: "document.getElementById('import-choices')"});
  await send("DOM.setFileInputFiles", {files: [file], objectId: handle.result.objectId});
}

/* Regressions: suggested defaults reach the service before install, unchanged answers are not re-saved,
   old saved choices migrate, browser history works locally, and saved choices files are validated. */
async function localRegressions() {
  await viewport(1280, 900, false);
  mock.routes = {};
  mock.installOutcome = "ready";
  mock.state = freshState();
  mock.requests = [];
  await open("/setup");
  await waitFor("document.getElementById('mode-badge').textContent === 'ON THIS COMPUTER'", "local badge regressions");
  await walk({});
  await click('[data-action="install"]');
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installReady)}`, "fresh install ready");
  const suggested = full(Object.assign({}, catalog.defaults, catalog.roles.find((r) => r.id === catalog.defaults.role).suggest));
  equal(mock.requests.filter((r) => r.method === "POST").map((r) => [r.path, r.body]),
    [["/api/quickstart/save", {choices: suggested}], ["/api/quickstart/install", {consent: true}]], "default answers with purpose suggestions are saved before install");
  equal(await text("status"), "", "fresh install shows no error");

  // Saved three-field choices from an older version open the plan with migrated answers.
  mock.state = freshState({choices: {role: "creator", budget: 10, existing: "anthropic"}});
  mock.requests = [];
  await open("/setup");
  equal(await currentStep(), "plan", "saved choices open the plan");
  await planMatches(full({role: "creator", budget: 10, existing: "anthropic"}), "migrated service choices");
  // Changing nothing does not re-save; browser Back and Forward stay on this page.
  await click('[data-action="change"]');
  await waitFor("location.hash === '#role'", "change answers locally");
  await walk({});
  equal(mock.requests.filter((r) => r.method === "POST").length, 0, "unchanged answers are not saved again");
  await send("Runtime.evaluate", {expression: "history.back()"});
  await waitFor("location.hash === '#automation'", "local browser Back");
  await select("automation", "calendar_email");
  route("POST /api/quickstart/save", (req, res) => json(res, 500, {error: LEAK}));
  await send("Runtime.evaluate", {expression: "history.forward()"});
  await waitFor(`document.getElementById('step-error').textContent === ${JSON.stringify(MESSAGES.save)}`, "Forward with a failed save");
  equal([await currentStep(), await evaluate("location.hash")], ["automation", "#automation"], "Forward with a failed save stays on the question");
  delete mock.routes["POST /api/quickstart/save"];
  await nextStep("plan");
  equal(mock.state.choices.automation, "calendar_email", "changed answer saved after recovery");

  // While installing, a changed answer is not saved.
  mock.installOutcome = "running";
  await click('[data-action="install"]');
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installRunning)}`, "install running regression");
  check(await evaluate("document.getElementById('change').disabled"), "Change answers locked during install");
  const saves = mock.requests.filter((r) => r.path === "/api/quickstart/save").length;
  await send("Runtime.evaluate", {expression: "history.back()"});
  await waitFor("location.hash === '#automation'", "Back during install");
  await select("automation", "none");
  await click("#next");
  await waitFor(`document.getElementById('step-error').textContent === ${JSON.stringify(MESSAGES.saveBusy)}`, "busy message");
  equal(mock.requests.filter((r) => r.path === "/api/quickstart/save").length, saves, "no save sent during install");
  mock.state.install = {state: "ready", message: LEAK};
  mock.installOutcome = "ready";
  await waitFor("true");
  await sleep(1700);
  await nextStep("plan");
  equal(mock.state.choices.automation, "none", "save goes through once the install finished");

  // Load saved choices: bad files rejected with a fixed message, valid files saved through the service.
  check(await evaluate("document.querySelector('[data-action=\"import-choices\"]').offsetParent !== null && document.getElementById('import-choices').hidden"), "load button shown, file input hidden");
  const pii = "alice.private@example.com";
  const valid = {schema: lib.CHOICES_SCHEMA, choices: {role: "study", budget: 2500, existing: "nvidia", dictation: "wispr", reply: "read_aloud", automation: "agent_later"}};
  const bad = {
    "too-large.json": JSON.stringify(Object.assign({}, valid, {pad: "x".repeat(3000)})),
    "duplicate.json": '{"schema":"' + lib.CHOICES_SCHEMA + '","choices":{"role":"study","role":"office","budget":0,"existing":"none"}}',
    "unknown-field.json": JSON.stringify(Object.assign({}, valid, {email: pii})),
    "extra-choice.json": JSON.stringify({schema: lib.CHOICES_SCHEMA, choices: Object.assign({}, valid.choices, {key: pii})}),
    "proto.json": '{"schema":"' + lib.CHOICES_SCHEMA + '","choices":{"__proto__":{"x":1},"role":"study","budget":0,"existing":"none"}}',
    "wrong-schema.json": JSON.stringify({schema: "jarvis-setup-answers/1", choices: valid.choices}),
    "bad-value.json": JSON.stringify({schema: lib.CHOICES_SCHEMA, choices: {role: pii, budget: 0, existing: "none"}}),
    "bad-reply.json": JSON.stringify({schema: lib.CHOICES_SCHEMA, choices: Object.assign({}, valid.choices, {reply: "elevenlabs"})}),
    "budget-too-high.json": JSON.stringify({schema: lib.CHOICES_SCHEMA, choices: Object.assign({}, valid.choices, {budget: 10001})}),
    "deep.json": "[".repeat(40) + "]".repeat(40),
    "not-json.txt": "role=study " + pii,
  };
  const shown = await stackRows();
  for (const [name, content] of Object.entries(bad)) {
    mock.requests = [];
    await evaluate("document.getElementById('status').textContent = ''");
    await setImportFile(name, content);
    await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.importBad)}`, "import rejected " + name);
    await waitIdle("import " + name);
    equal(mock.requests.filter((r) => r.method === "POST").length, 0, "no request for " + name);
    check(!(await evaluate("document.body.innerHTML.includes(" + JSON.stringify(pii) + ")")), "file content not echoed for " + name);
    equal(await stackRows(), shown, "plan unchanged by " + name);
  }
  mock.requests = [];
  await setImportFile("valid.json", JSON.stringify(valid, null, 2));
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.imported)}`, "valid import");
  await waitIdle("valid import");
  equal(mock.requests.filter((r) => r.method === "POST").map((r) => [r.path, r.body]), [["/api/quickstart/save", {choices: valid.choices}]], "valid file saved through the service only");
  await planMatches(valid.choices, "imported plan");
  mock.requests = [];
  const old = {schema: "jarvis-quickstart-choices/1", choices: {role: "developer", budget: 50, existing: "openai"}};
  await setImportFile("old.json", JSON.stringify(old));
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.imported)}`, "old file import");
  await waitIdle("old import");
  equal(mock.requests.filter((r) => r.method === "POST").map((r) => r.body), [{choices: full(old.choices)}], "an older choices file is migrated before saving");
  route("POST /api/quickstart/save", (req, res) => json(res, 500, {error: LEAK}));
  await setImportFile("valid-again.json", JSON.stringify(valid));
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.importSave)}`, "import save failure");
  await waitIdle("import save failure");
  await planMatches(full(old.choices), "plan kept after a failed import save");
  mock.routes = {};
}

const BUDGET = {month: "2026-10", limit_usd: 25, reserved_usd: 0.12, remaining_usd: 24.88, paid_requests: 12, reserve_per_request_usd: 0.01,
  kind: "local_allowance", provider_limit_required: true, pricing_checked: "2026-10-28", valid_until: "2026-11-27"};

/* The allowance meter only shows service data and sits in the collapsed cost details; AI calls refresh it. */
async function localBudget() {
  await viewport(1280, 900, false);
  mock.routes = {};
  mock.state = freshState({choices: full({role: "office", budget: 25, existing: "openai"}), credentials: {nvidia: false, openai: true, anthropic: false}, budget: Object.assign({}, BUDGET)});
  mock.requests = [];
  await open("/setup");
  await waitFor("document.getElementById('mode-badge').textContent === 'ON THIS COMPUTER'", "local badge budget");
  check(!(await evaluate("document.getElementById('budget-meter').checkVisibility()")), "allowance meter tucked into the collapsed details");
  await evaluate("document.querySelectorAll('#result details').forEach(d => { d.open = true; })");
  check(await visible("budget-meter"), "allowance meter shown in local mode");
  equal(await text("meter-text"), "$0.12 reserved of $25.00, $24.88 left, 12 paid attempts", "meter shows service figures");
  equal(await text("meter-month"), "2026-10", "meter shows the month");
  equal(await evaluate("[document.getElementById('meter-bar').max, document.getElementById('meter-bar').value]"), [25, 0.12], "meter bar follows the service");
  equal(await text("meter-note"), MESSAGES.meterNote + " Pricing assumptions checked 2026-10-28, due for review 2026-11-27; update Jarvis after that date.", "meter explains the local allowance and its review date");
  equal(await evaluate("document.getElementById('limit-link').href"), catalog.providers.openai.limit_url, "OpenAI hard limit link");
  equal(await text("budget-line"), "Monthly budget for paid AI: $25 a month.", "budget line");
  check(!(await visible("budget-paused")), "no paused warning when the allowance is available");
  await shot("30-local-costs-open");

  // Test and chat refresh the allowance even when they fail.
  await click("#consent");
  route("POST /api/quickstart/test", (req, res) => { mock.state.budget.reserved_usd = 0.13; mock.state.budget.remaining_usd = 24.87; mock.state.budget.paid_requests = 13; json(res, 502, {error: LEAK}); });
  const gets = mock.requests.filter((r) => r.method === "GET" && r.path === "/api/quickstart").length;
  await click('[data-action="test"]');
  await waitFor(`document.getElementById('meter-text').textContent.startsWith('$0.13')`, "meter refresh after failed test");
  check(mock.requests.filter((r) => r.method === "GET" && r.path === "/api/quickstart").length > gets, "state re-read after a failed test");
  equal(await text("status"), MESSAGES.test, "failed test keeps its fixed message");
  await evaluate("document.getElementById('prompt').value = 'Hi'");
  route("POST /api/quickstart/chat", (req, res) => { mock.state.budget.reserved_usd = 0.14; mock.state.budget.remaining_usd = 24.86; mock.state.budget.paid_requests = 14; json(res, 200, {reply: "ok"}); });
  await click('[data-action="chat"]');
  await waitFor(`document.getElementById('meter-text').textContent.startsWith('$0.14')`, "meter refresh after chat");
  check(!(await visible("speak-row")), "no read-aloud option when text replies were chosen");
  equal(await evaluate("window.__speech.spoken.length"), 0, "nothing spoken for text replies");

  // Large budgets show the large-budget note; free providers and $0 explain what the allowance does not cover.
  mock.routes = {};
  mock.state = freshState({choices: full({role: "office", budget: 5000, existing: "anthropic"}), budget: Object.assign({}, BUDGET, {limit_usd: 5000, remaining_usd: 4999.88})});
  await open("/setup");
  equal(await text("budget-line"), "Monthly budget for paid AI: $5,000 a month.", "large budget shown with separators");
  check((await evaluate("[...document.querySelectorAll('#plan-tradeoffs li')].map(n => n.textContent)")).includes(catalog.notes.large_budget), "large budget note shown");
  mock.state = freshState({choices: full({role: "office", budget: 0, existing: "ollama"}), budget: Object.assign({}, BUDGET, {limit_usd: 0, remaining_usd: 0, reserved_usd: 0, paid_requests: 0})});
  await open("/setup");
  equal(await text("meter-note"), MESSAGES.meterNote + " " + MESSAGES.meterFree + " Pricing assumptions checked 2026-10-28, due for review 2026-11-27; update Jarvis after that date.", "Ollama does not use the allowance");
  check(await evaluate("document.getElementById('limit-help').hidden"), "no hard limit link for Ollama");
  mock.state = freshState({choices: full({role: "office", budget: 0, existing: "openai"}), budget: Object.assign({}, BUDGET, {limit_usd: 0, remaining_usd: 0, reserved_usd: 0, paid_requests: 0})});
  await open("/setup");
  equal(await text("plan-title"), plan({role: "office", budget: 0, existing: "openai"}).title, "$0 with OpenAI recommends trial access");

  // Unavailable allowance data: fixed paused text shown outside the details, no figures, no service message.
  const CANARY = "PRIVATE-LEDGER-" + "c:\\Users\\someone\\budget.json";
  mock.state = freshState({choices: full({role: "office", budget: 25, existing: "openai"}), credentials: {nvidia: false, openai: true, anthropic: false},
    budget: {available: false, kind: "local_allowance", limit_usd: 25, message: CANARY}});
  await open("/setup");
  check(await visible("budget-paused"), "paused warning visible without opening details");
  equal(await text("budget-paused"), MESSAGES.budgetUnavailable, "paused warning uses the fixed text");
  await evaluate("document.querySelectorAll('#result details').forEach(d => { d.open = true; })");
  equal(await text("meter-text"), MESSAGES.budgetUnavailable, "unavailable allowance shows the fixed paused text");
  equal(await text("meter-note"), MESSAGES.budgetUnavailableNote, "unavailable allowance explains what still works");
  check(!(await visible("meter-bar")) && !(await visible("meter-month")), "unavailable allowance hides the bar and month");
  check(!(await evaluate("document.body.innerHTML.includes('PRIVATE-LEDGER')")), "service budget message never displayed");
  check(await visible("ai-box") && !(await evaluate("document.getElementById('consent').disabled")), "chat stays available while paid AI is paused");
  await shot("31-local-budget-paused");

  // Known budget error code: fixed paused message. Other codes and statuses keep the usual fixed text.
  await click("#consent");
  await evaluate("document.getElementById('prompt').value = 'Hi'");
  const cases = [
    ["test", 400, {code: "budget_unavailable", error: CANARY}, MESSAGES.budgetPaused],
    ["chat", 400, {code: "budget_unavailable", error: CANARY}, MESSAGES.budgetPaused],
    ["test", 400, {code: "other", error: CANARY}, MESSAGES.test],
    ["chat", 500, {code: "budget_unavailable", error: CANARY}, MESSAGES.chat],
    ["test", 400, ["budget_unavailable"], MESSAGES.test],
  ];
  for (const [action, statusCode, body, expected] of cases) {
    route("POST /api/quickstart/" + action, (req, res) => json(res, statusCode, body));
    await evaluate("document.getElementById('status').textContent = ''");
    await click('[data-action="' + action + '"]');
    await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(expected)}`, action + " " + statusCode + " " + JSON.stringify(body).slice(0, 30));
    await waitIdle(action + " settled");
    check(!(await evaluate("document.body.innerHTML.includes('PRIVATE-LEDGER')")), "error body never displayed for " + action + " " + statusCode);
  }
  mock.routes = {};
  for (const broken of [{kind: "invoice"}, Object.assign({}, BUDGET, {reserved_usd: -1}), Object.assign({}, BUDGET, {month: "2026-13"}), Object.assign({}, BUDGET, {paid_requests: 1.5}), "25"]) {
    mock.state = freshState({choices: full({role: "office", budget: 25, existing: "openai"}), budget: broken});
    await open("/setup");
    await evaluate("document.querySelectorAll('#result details').forEach(d => { d.open = true; })");
    check(!(await visible("budget-meter")) && !(await visible("budget-paused")), "malformed allowance hidden " + JSON.stringify(broken).slice(0, 40));
  }
  await viewport(390, 844, true);
  mock.state = freshState({choices: full({role: "office", budget: 25, existing: "openai"}), budget: Object.assign({}, BUDGET)});
  await open("/setup");
  await noOverflow("local plan mobile");
  await shot("32-local-plan-mobile");
  await viewport(1280, 900, false);
  mock.routes = {};
}

async function localBundledPackage() {
  // A complete portable package: no install step, one clear next action, and opening still needs a click.
  const choices = full({role: "office", budget: 0, existing: "none"});
  mock.routes = {};
  mock.requests = [];
  mock.state = freshState({choices, install: {state: "ready", message: LEAK, included: true}});
  await open("/setup");
  await waitFor("document.getElementById('mode-badge').textContent === 'ON THIS COMPUTER'", "bundled local badge");
  await waitFor("!document.getElementById('local').hidden", "bundled local controls");
  check(!(await visible("install")), "no redundant install button for a complete package");
  equal(await text("install-state"), MESSAGES.installIncluded, "package says nothing needs installing");
  equal(await evaluate("[document.getElementById('launch').disabled, document.getElementById('launch').className]"), [false, "button primary"], "Open dashboard is the one next action");
  equal(mock.requests.filter((r) => r.method === "POST").length, 0, "nothing is installed, launched or saved on load");
  route("POST /api/quickstart/launch", (req, res) => json(res, 200, {url: origin + "/"}));
  await click('[data-action="launch"]');
  await waitFor("location.pathname === '/' && document.getElementById('dash') !== null", "bundled launch navigation");
  equal(last("/api/quickstart/launch").body, {consent: true}, "opening still sends the explicit click consent");
  equal(mock.requests.filter((r) => r.path === "/api/quickstart/install").length, 0, "install never requested for a complete package");

  // "included" is believed only together with a ready state; otherwise the normal install step stays.
  for (const install of [{state: "idle", message: LEAK, included: true}, {state: "ready", message: LEAK, included: "true"}, {state: "ready", message: LEAK}]) {
    mock.routes = {};
    mock.state = freshState({choices, install});
    await open("/setup");
    await waitFor("!document.getElementById('local').hidden", "local controls " + JSON.stringify(install));
    check(await visible("install"), "install step kept " + JSON.stringify(install));
    check((await text("install-state")) !== MESSAGES.installIncluded, "no included claim " + JSON.stringify(install));
  }
  mock.routes = {};
}

async function localReadAloudGuard() {
  // The service runs offline voice commands: spoken replies could be heard as commands, so read-aloud is refused.
  const choices = full({role: "office", budget: 0, existing: "ollama", reply: "read_aloud"});
  const reply = "To start the guard, say active.";
  mock.routes = {"POST /api/quickstart/chat": (req, res) => json(res, 200, {reply})};
  for (const value of [false, undefined, "true", 1]) {
    mock.state = freshState({choices, read_aloud_allowed: value});
    await open("/setup");
    await waitFor("document.getElementById('mode-badge').textContent === 'ON THIS COMPUTER'", "local badge " + String(value));
    await waitFor("!document.getElementById('ai-box').hidden", "chat offered " + String(value));
    check(await visible("speak-row"), "read-aloud row still explains itself when refused " + String(value));
    equal(await evaluate("[document.getElementById('speak').disabled, document.getElementById('speak').checked]"), [true, false], "read-aloud box disabled " + String(value));
    equal(await text("speak-off"), MESSAGES.readAloudOff, "refusal explained " + String(value));
    await evaluate("document.getElementById('consent').checked = true; document.getElementById('prompt').value = 'Hello'; document.getElementById('speak').checked = true; true");
    const asked = mock.requests.filter((r) => r.path === "/api/quickstart/chat").length;
    await click('[data-action="chat"]');
    await waitFor("!document.getElementById('chat-reply').hidden", "typed reply shown " + String(value));
    equal(await text("chat-reply"), reply, "reply stays as text " + String(value));
    equal(mock.requests.filter((r) => r.path === "/api/quickstart/chat").length, asked + 1, "typed question still sent " + String(value));
    equal(await evaluate("[window.__speech.spoken.length, document.getElementById('speak').checked, document.getElementById('stop-speaking').hidden]"), [0, false, true], "forced tick still speaks nothing " + String(value));
  }

  // Allowed, speaking, then a fetched snapshot refuses: speech is cancelled and does not start again.
  mock.state = freshState({choices});
  await open("/setup");
  await waitFor("!document.getElementById('ai-box').hidden", "chat offered when allowed");
  check(!(await visible("speak-off")), "no refusal note when allowed");
  await evaluate("document.getElementById('consent').checked = true; document.getElementById('prompt').value = 'Hello'; true");
  await click("#speak");
  await click('[data-action="chat"]');
  await waitFor("window.__speech.spoken.length === 1", "allowed reply spoken");
  check(await visible("stop-speaking"), "Stop shown while speaking");
  mock.state = freshState({choices, read_aloud_allowed: false});
  const cancels = await evaluate("window.__speech.cancels");
  route("POST /api/quickstart/test", (req, res) => json(res, 200, {ok: true}));
  await click('[data-action="test"]');
  await waitFor("document.getElementById('speak').disabled", "refusing snapshot applied");
  check((await evaluate("window.__speech.cancels")) > cancels, "refusing snapshot cancels speech in progress");
  equal(await evaluate("[document.getElementById('speak').checked, document.getElementById('stop-speaking').hidden]"), [false, true], "box cleared and Stop hidden");
  equal(await text("speak-off"), MESSAGES.readAloudOff, "refusal explained after update");
  const quickstartReads = () => mock.requests.filter((r) => r.method === "GET" && r.path === "/api/quickstart").length;
  const reads = quickstartReads();
  await click('[data-action="chat"]');
  for (let i = 0; i < 250 && quickstartReads() === reads; i += 1) await sleep(20);
  check(quickstartReads() > reads, "chat finished and refreshed");
  equal(await evaluate("window.__speech.spoken.length"), 1, "no speech after the refusing snapshot");
  equal(await evaluate("window.__speech.mic"), 0, "microphone never requested");
  mock.routes = {};
}

async function localVariants() {
  // Saved choices from the service restore; a local provider hides the key field.
  mock.routes = {};
  mock.state = freshState({choices: full({role: "creator", budget: 10, existing: "ollama"})});
  await viewport(390, 844, true);
  await open("/setup");
  await waitFor("document.getElementById('mode-badge').textContent === 'ON THIS COMPUTER'", "local badge mobile");
  await planMatches(full({role: "creator", budget: 10, existing: "ollama"}), "restored local plan");
  check(!(await visible("key-box")), "no key field for Ollama");
  check(await visible("ai-box"), "AI chat offered for Ollama");
  check(!(await visible("phone")), "local mode on a small screen is not the phone planner");
  await noOverflow("local mobile");
  await viewport(1280, 900, false);

  // Session failure.
  mock.routes = {"GET /api/session": (req, res) => json(res, 500, {error: LEAK})};
  mock.requests = [];
  await open("/setup");
  equal(await text("notice"), MESSAGES.local, "session failure notice");
  check(!(await visible("local")), "no local controls without a session");
  equal(mock.requests.map((r) => r.path), ["/api/session"], "nothing else called after session failure");

  // Service answering without local:true: the page stays a planner and never posts.
  mock.routes = {"GET /api/quickstart": (req, res) => json(res, 200, Object.assign(freshState(), {local: false}))};
  mock.requests = [];
  await open("/setup");
  equal(await text("notice"), MESSAGES.local, "non-local state notice");
  await walk({role: "study"});
  check(!(await visible("local")), "no local controls when the service is not local");
  equal(mock.requests.filter((r) => r.method === "POST").length, 0, "no POST when local mode is not confirmed");
  equal(await evaluate("document.querySelectorAll('#key-input').length"), 0, "no key field when local mode is not confirmed");
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
  await viewport(390, 844, true);
  await navigate(origin + "/site/index.html");
  await noOverflow("site mobile");
}

async function localInstallStatusRecovery() {
  await viewport(1280, 900, false);
  const choices = full({role: catalog.defaults.role, budget: 0, existing: "none"});
  const reset = async () => {
    mock.routes = {};
    mock.state = freshState({choices, plan: lib.recommend(choices, catalog)});
    mock.requests = [];
    mock.installOutcome = "running";
    await open("/setup");
    await waitFor("!document.getElementById('result').hidden", "recovery plan shown");
  };
  await reset();
  let failed = false;
  route("GET /api/quickstart", (req, res) => {
    if (!failed) { failed = true; return json(res, 503, {error: LEAK}); }
    return json(res, 200, mock.state);
  });
  await click('[data-action="install"]');
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.installChecking)}`, "temporary status failure");
  equal(await text("install-state"), MESSAGES.installRunning, "failed status read does not claim installation failed");
  check(await evaluate("document.getElementById('install').disabled && document.getElementById('change').disabled"), "cannot start a duplicate install during a failed status read");
  mock.state.install = {state: "ready", message: LEAK};
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installReady)}`, "status check recovers", 10000);
  check(!await evaluate("document.getElementById('launch').disabled"), "recovered status enables launch");
  equal(mock.requests.filter(item => item.path === "/api/quickstart/install").length, 1, "recovery never resubmits the installer");
  equal(await text("status"), "", "temporary status message clears after recovery");

  await reset();
  await evaluate("window.__installClockOffset=0; const clock=performance.now.bind(performance); Object.defineProperty(performance,'now',{configurable:true,value:()=>clock()+window.__installClockOffset});");
  await click('[data-action="install"]');
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installRunning)}`, "slow install starts");
  await evaluate("window.__installClockOffset=46*60*1000");
  await waitFor(`document.getElementById('status').textContent === ${JSON.stringify(MESSAGES.installCheckLater)}`, "long install asks for status reload");
  equal(await text("install-state"), MESSAGES.installRunning, "monitoring deadline does not invent an installer failure");
  check(await evaluate("document.getElementById('install').disabled"), "monitoring deadline keeps duplicate install blocked");
  mock.state.install = {state: "ready", message: LEAK};
  await open("/setup");
  await waitFor(`document.getElementById('install-state').textContent === ${JSON.stringify(MESSAGES.installReady)}`, "reload checks actual installer result");

  await reset();
  let held = null;
  route("POST /api/setup/exit", (req, res) => json(res, 200, {stopped: true}));
  route("GET /api/quickstart", (req, res) => { held = res; });
  await click('[data-action="install"]');
  for (let tries = 0; !held && tries < 60; tries += 1) await sleep(50);
  check(Boolean(held), "install status request is in flight");
  await click('[data-action="exit"]');
  await waitFor("document.body.classList.contains('closed')", "setup closes during status check");
  const reads = mock.requests.filter(item => item.path === "/api/quickstart").length;
  await sleep(2000);
  equal(mock.requests.filter(item => item.path === "/api/quickstart").length, reads, "closed setup sends no further status checks");
  check(await evaluate("document.getElementById('install').disabled && document.getElementById('launch').disabled"), "pending status cannot re-enable closed controls");
  check(held.destroyed, "closing setup aborts the pending status read");
  await noSecretAnywhere("after status recovery");
  mock.installOutcome = "ready";
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
        const ok = url.startsWith("data:") || url.startsWith("blob:") || [...allowedOrigins].some((item) => url === item || url.startsWith(item + "/"));
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
    await send("Page.addScriptToEvaluateOnNewDocument", {source: SPEECH_FAKE});
    await send("Fetch.enable", {patterns: [{urlPattern: "*"}]});
    await send("Browser.setPermission", {permission: {name: "clipboard-write"}, setting: "denied"}).catch(() => {});
    await hostedDesktop();
    await hostedPhone();
    await localFlow();
    await localRegressions();
    await localBudget();
    await localVariants();
    await localReadAloudGuard();
    await localBundledPackage();
    await localInstallStatusRecovery();
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
