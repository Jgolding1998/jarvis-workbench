"use strict";
/* Jarvis setup. The three-choice quickstart mirrors jarvis/quickstart.py (tests/test_quickstart.py).
   The older detailed planner below mirrors jarvis/planner.py (tests/test_planner.py) and stays exported for it.
   The hosted page never calls an API and never shows a key field. */
(function (root) {
  const ANSWERS_SCHEMA = "jarvis-setup-answers/1";
  const PLAN_SCHEMA = "jarvis-setup-plan/1";
  const VOICE_MODEL = "models/voice-en";
  const CAMERA_FEATURES = ["presence_guard", "privacy_lock", "boss_alert", "hand_control"];
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const ids = (items) => items.map((item) => item.id);

  function isPlainObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
  }
  function exact(value, keys, name) {
    if (!isPlainObject(value)) throw new Error(name + " has missing or unexpected fields");
    const found = Object.keys(value).sort();
    const wanted = keys.slice().sort();
    if (found.length !== wanted.length || found.some((key, index) => key !== wanted[index])) throw new Error(name + " has missing or unexpected fields");
  }
  function choice(value, allowed, name) {
    if (typeof value !== "string" || !allowed.includes(value)) throw new Error("Unknown " + name);
    return value;
  }
  function cents(value) {
    return Math.floor(value * 100 + 0.5) / 100;
  }
  function money(value) {
    return "$" + (Number.isInteger(value) ? String(value) : value.toFixed(2));
  }

  function validateAnswers(data, catalog) {
    exact(data, ["schema", "install", "preset", "features", "budget_usd", "ai_tool", "voice", "device", "preferences"], "Answers");
    if (data.schema !== ANSWERS_SCHEMA) throw new Error("Unsupported answers schema");
    if (typeof data.install !== "boolean") throw new Error("Install must be true or false");
    const preset = choice(data.preset, ids(catalog.presets), "preset");
    const selectable = catalog.features.filter((item) => item.selectable).map((item) => item.id);
    const features = data.features;
    if (!Array.isArray(features) || features.length > selectable.length || new Set(features.map(String)).size !== features.length) throw new Error("Features must be a short list without repeats");
    features.forEach((feature) => choice(feature, selectable, "feature"));
    const budget = data.budget_usd;
    if (typeof budget !== "number" || !Number.isFinite(budget) || budget < catalog.budget.min || budget > catalog.budget.max) throw new Error("Budget must be a number from 0 to 500");
    const aiTool = choice(data.ai_tool, ids(catalog.ai_tools), "AI tool");
    const voice = choice(data.voice, ids(catalog.voice_options), "voice option");
    const device = data.device;
    exact(device, ["os", "camera", "microphone", "ram"], "Device");
    if (typeof device.camera !== "boolean" || typeof device.microphone !== "boolean") throw new Error("Camera and microphone must be true or false");
    const preferences = data.preferences;
    exact(preferences, ["camera_index", "hand_move_cursor"], "Preferences");
    if (!Number.isInteger(preferences.camera_index) || preferences.camera_index < 0 || preferences.camera_index > 9) throw new Error("Camera index must be 0 to 9");
    if (typeof preferences.hand_move_cursor !== "boolean") throw new Error("Cursor preference must be true or false");
    return {
      schema: ANSWERS_SCHEMA, install: data.install, preset,
      features: features.slice().sort((a, b) => selectable.indexOf(a) - selectable.indexOf(b)),
      budget_usd: cents(budget), ai_tool: aiTool, voice,
      device: {os: choice(device.os, ids(catalog.os), "operating system"), camera: device.camera, microphone: device.microphone, ram: choice(device.ram, ids(catalog.ram), "memory size")},
      preferences: {camera_index: preferences.camera_index, hand_move_cursor: preferences.hand_move_cursor},
    };
  }

  function defaultAnswers(catalog) {
    return {schema: ANSWERS_SCHEMA, install: true, preset: "desk_privacy",
      features: catalog.presets.find((preset) => preset.id === "desk_privacy").features.slice(),
      budget_usd: 0, ai_tool: "none", voice: "no_microphone",
      device: {os: "windows", camera: true, microphone: false, ram: "unknown"},
      preferences: {camera_index: 0, hand_move_cursor: false}};
  }

  function blockers(feature, answers) {
    const needs = feature.requires || {};
    const device = answers.device;
    const reasons = [];
    if (own(needs, "os") && !needs.os.includes(device.os)) reasons.push("Needs Windows");
    if (needs.camera && !device.camera) reasons.push("Needs a camera");
    if (needs.microphone && !device.microphone) reasons.push("Needs a microphone");
    if (own(needs, "voice") && !needs.voice.includes(answers.voice)) reasons.push("Choose offline voice to enable");
    return reasons;
  }

  function buildPlan(answers, catalog) {
    if (!answers.install) {
      return {schema: PLAN_SCHEMA, install: false, answers, features: [], tasks: [], launch_command: null, cost: null,
        notes: ["Nothing was installed or changed. You can close this page."]};
    }
    const features = new Map(catalog.features.map((item) => [item.id, item]));
    const wanted = new Set(answers.features);
    catalog.features.forEach((item) => {
      if (item.always || (item.id === "offline_voice" && answers.voice === "offline_vosk")) wanted.add(item.id);
    });
    Array.from(wanted).forEach((name) => ((features.get(name).requires || {}).depends || []).forEach((dependency) => wanted.add(dependency)));
    const included = new Set();
    const rows = catalog.features.map((item) => {
      const name = item.id;
      const reasons = blockers(item, answers);
      ((item.requires || {}).depends || []).forEach((dependency) => {
        if (blockers(features.get(dependency), answers).length) reasons.push("Needs " + features.get(dependency).name);
      });
      const notes = [];
      let status;
      if (!wanted.has(name)) status = "not_selected";
      else if (reasons.length) status = "unavailable";
      else {
        status = "included";
        included.add(name);
        if ((item.ram_caution || []).includes(answers.device.ram)) notes.push("May run slowly with less than 4 GB of memory. Try the simulator first.");
        if (item.platform_note && !item.platform_note.os.includes(answers.device.os)) notes.push(item.platform_note.text);
        if (!answers.features.includes(name) && !["dashboard", "simulator", "offline_voice"].includes(name)) notes.push("Added because another selected feature needs it.");
      }
      return {id: name, name: item.name, maturity: item.maturity, status, reasons: status === "unavailable" ? reasons : [], notes};
    });
    const optionalTools = CAMERA_FEATURES.some((name) => included.has(name)) || included.has("offline_voice");
    const interpreter = optionalTools ? (answers.device.os === "windows" ? ".\\.venv\\Scripts\\python.exe" : "./.venv/bin/python") : "python";
    const command = [interpreter, "-m", "jarvis.server"];
    if (["presence_guard", "privacy_lock", "boss_alert"].some((name) => included.has(name))) command.push("--camera");
    if (CAMERA_FEATURES.some((name) => included.has(name))) {
      if (answers.preferences.camera_index) command.push("--camera-index", String(answers.preferences.camera_index));
      if (included.has("privacy_lock")) command.push("--allow-lock");
      if (included.has("boss_alert")) command.push("--allow-spreadsheet");
      if (included.has("hand_control")) command.push("--allow-hand-control", "--hand-model", "models/hand_landmarker.task");
    } else command.push("--demo");
    if (included.has("offline_voice")) command.push("--voice-model", VOICE_MODEL);
    const launch = command.join(" ");
    const tasks = [];
    catalog.tasks.forEach((task) => {
      if (own(task, "when_features") && !task.when_features.some((name) => included.has(name))) return;
      tasks.push({id: task.id, kind: task.kind, title: task.title, detail: task.detail,
        url: task.url === "release" ? catalog.release.download_url : (own(task, "url") ? task.url : null),
        command: task.command === "launch" ? launch : (own(task, "command") ? task.command : null),
        check: (task.check || []).slice()});
    });
    const budget = answers.budget_usd;
    if (included.has("hand_control") && answers.preferences.hand_move_cursor) tasks.push({id: "cursor_permission", kind: "manual", title: "Enable real cursor control after practice",
      detail: "On your Windows computer, stop hand control, select Move the real cursor, apply hand settings, then start watch me again. Saving this plan never enables movement.", url: null, command: null, check: []});
    const tool = catalog.ai_tools.find((item) => item.id === answers.ai_tool);
    const voice = catalog.voice_options.find((item) => item.id === answers.voice);
    const cost = ["Jarvis costs $0 per month. Desktop features run on the computer where you install Jarvis.",
      "Your ceiling is " + money(budget) + " per month. It is a limit for planning, not a target and not a price guarantee. No purchase is recommended."];
    if (tool.kind === "api") {
      cost.push(budget === 0 ? "API usage is billed per use by the provider. With a $0 ceiling, leave it out; Jarvis does not need it."
        : "If you use this API on its own, set a spending limit in the provider console at or below " + money(budget) + ". Jarvis cannot see or cap that spend.");
    } else if (tool.kind === "subscription") {
      cost.push("Your subscription is billed by its provider and is not needed by Jarvis. Some plans or programs include credits or other benefits; check the official page for your current eligibility.");
    } else if (tool.kind === "local") {
      cost.push("Ollama is free software. It uses your own hardware and electricity.");
    }
    if (voice.id === "elevenlabs_existing") cost.push("ElevenLabs is billed by its provider. Jarvis does not use it, so it adds nothing to this plan.");
    const guidance = [
      {topic: "ai_tool", name: tool.name, kind: tool.kind, text: tool.guidance, url: tool.url, connector: false},
      {topic: "voice", name: voice.name, kind: voice.kind, text: voice.guidance, url: voice.url, connector: voice.kind === "implemented"},
    ];
    if ((tool.ram_caution || []).includes(answers.device.ram)) guidance[0].text += " Larger local models need a lot of memory; compare the model size with your RAM before downloading.";
    return {schema: PLAN_SCHEMA, install: true, answers, features: rows, tasks, launch_command: launch, guidance,
      cost: {jarvis_usd_per_month: 0, ceiling_usd_per_month: budget, notes: cost},
      notes: ["Importing or saving a plan never arms the camera, microphone, mouse, or Windows actions."]};
  }

  /* Reads the root-maintained web/provider-catalog.json. Untrusted: only known ids, plain text, https links. */
  function readProviderCatalog(data, catalog) {
    const items = catalog.ai_tools.concat(catalog.voice_options);
    const known = new Set(items.map((item) => item.provider).filter(Boolean).concat(ids(items)));
    const text = (value) => (typeof value === "string" && value.length <= 600 ? value : "");
    const link = (value) => (typeof value === "string" && /^https:\/\/[^\s"'<>\\]+$/.test(value) ? value : null);
    const finite = (value) => (typeof value === "number" && Number.isFinite(value) && value >= 0 && value < 100000 ? value : null);
    if (isPlainObject(data) && Array.isArray(data.entries)) {
      /* Shape jarvis-provider-catalog/1: entries keyed by setup-catalog ids. */
      const entries = data.entries.slice(0, 50).filter((entry) => isPlainObject(entry) && typeof entry.id === "string" && known.has(entry.id)).map((entry) => ({
        id: entry.id, name: text(entry.name), description: text(entry.summary), creditNote: text(entry.benefits), billing: "", monthly: null, exampleRate: "",
        links: link(entry.url) ? [{label: "Official page", url: link(entry.url)}] : []}));
      return {checked: text(data.checked) || text(data.checked_on), budgetNote: text(data.budget_note), entries};
    }
    if (!isPlainObject(data) || !Array.isArray(data.providers)) return {checked: "", budgetNote: "", entries: []};
    const entries = data.providers.slice(0, 50).filter((entry) => isPlainObject(entry) && typeof entry.id === "string" && known.has(entry.id)).map((entry) => {
      const rate = isPlainObject(entry.example_rate) ? entry.example_rate : {};
      const perThousand = finite(rate.usd_per_1000_characters);
      const links = [["Pricing or source", link(entry.source_url)], ["Setup guide", link(entry.setup_url)], ["Credits and eligibility", link(entry.credits_url)]]
        .filter(([, url]) => url).map(([label, url]) => ({label, url}));
      return {id: entry.id, name: text(entry.name), description: text(entry.description), creditNote: text(entry.credit_note),
        billing: text(entry.billing), monthly: finite(entry.monthly_software_usd),
        exampleRate: perThousand === null ? "" : "Example rate listed in the catalog: $" + perThousand + " per 1,000 characters" + (text(rate.model) ? " (" + text(rate.model) + ")" : "") + ". Check the official page before you rely on it.",
        links};
    });
    return {checked: text(data.checked_on), budgetNote: text(data.budget_note), entries};
  }

  function parseImportJson(text) {
    const data = JSON.parse(text);
    const stack = [];
    for (let index = 0; index < text.length; index++) {
      const character = text[index];
      if (character === "{" || character === "[") {
        stack.push(character === "{" ? new Set() : null);
        if (stack.length > 32) throw new Error();
      } else if (character === "}" || character === "]") stack.pop();
      else if (character === '"') {
        const start = index;
        let escaped = false;
        for (index++; index < text.length; index++) {
          if (escaped) escaped = false;
          else if (text[index] === "\\") escaped = true;
          else if (text[index] === '"') break;
        }
        let next = index + 1;
        while (next < text.length && /\s/.test(text[next])) next++;
        if (text[next] === ":") {
          const keys = stack[stack.length - 1];
          const key = JSON.parse(text.slice(start, index + 1));
          if (keys.has(key)) throw new Error();
          keys.add(key);
        }
      }
    }
    return data;
  }

  function importedAnswers(text, catalog) {
    try {
      if (typeof text !== "string" || text.length > 16384) throw new Error();
      const data = parseImportJson(text);
      const wrapped = isPlainObject(data) && own(data, "schema") && data.schema === PLAN_SCHEMA;
      return validateAnswers(wrapped ? data.answers : data, catalog);
    } catch (error) {
      throw new Error("Choose a valid Jarvis plan JSON file under 16 KB.");
    }
  }

  function isMobilePlanner(info) {
    return Boolean(info.userAgentData && info.userAgentData.mobile) || /Android|iPhone|iPad|iPod/i.test(info.userAgent || "") ||
      (/MacIntel/i.test(info.platform || "") && Number(info.maxTouchPoints) > 1);
  }

  /* Three-choice quickstart. Mirrors jarvis/quickstart.py; tests/test_quickstart.py checks both agree. */
  const QUICKSTART_SCHEMA = "jarvis-quickstart-catalog/1";
  const CHOICES_SCHEMA = "jarvis-quickstart-choices/1";
  const CHOICE_KEYS = ["role", "budget", "existing"];
  const CHOICE_ERRORS = {
    shape: "Choose a role, a monthly budget and an existing tool.",
    role: "Choose a role from the list.",
    budget: "Choose a monthly budget from the list.",
    existing: "Choose an existing tool from the list.",
  };
  const findId = (items, id) => items.find((item) => item.id === id) || null;

  function validateChoices(data, catalog) {
    if (!isPlainObject(data) || Object.keys(data).length !== CHOICE_KEYS.length || !CHOICE_KEYS.every((key) => own(data, key))) throw new Error(CHOICE_ERRORS.shape);
    const role = data.role;
    let budget = data.budget;
    const existing = data.existing;
    if (typeof role !== "string" || !findId(catalog.roles, role)) throw new Error(CHOICE_ERRORS.role);
    const allowed = ids(catalog.budgets);
    if (typeof budget === "string" && allowed.map(String).includes(budget)) budget = Number(budget);
    if (typeof budget !== "number" || !Number.isInteger(budget) || !allowed.includes(budget)) throw new Error(CHOICE_ERRORS.budget);
    if (typeof existing !== "string" || !findId(catalog.existing, existing)) throw new Error(CHOICE_ERRORS.existing);
    return {role, budget, existing};
  }

  function recommend(data, catalog) {
    const choices = validateChoices(data, catalog);
    const role = findId(catalog.roles, choices.role);
    const existing = findId(catalog.existing, choices.existing);
    const paid = choices.budget > 0;
    const providerId = paid ? existing.paid : existing.zero;
    const provider = catalog.providers[providerId];
    const features = role.features.slice();
    const tradeoffs = existing[paid ? "paid_notes" : "zero_notes"].map((note) => catalog.notes[note])
      .concat(features.map((name) => catalog.features[name].note), [catalog.budget_note]);
    const steps = [catalog.steps.download, catalog.steps.start, provider.step]
      .concat(features.map((name) => catalog.features[name].step), [catalog.steps.dashboard]);
    if (features.includes("hand")) steps.push(catalog.steps.practice);
    const plan = {choices, provider: providerId, features, title: role.title + " with " + provider.name,
      summary: role.summary + " " + provider.summary, tradeoffs, setup_url: provider.setup_url, steps};
    if (own(provider, "model")) plan.model = provider.model;
    return plan;
  }

  /* Returns null when the address carries no choices; throws when it carries invalid ones. */
  function choicesFromQuery(search, catalog) {
    const params = new URLSearchParams(search);
    if (!CHOICE_KEYS.some((key) => params.has(key))) return null;
    const data = {};
    CHOICE_KEYS.forEach((key) => { if (params.has(key)) data[key] = params.get(key); });
    return validateChoices(data, catalog);
  }

  function isLoopbackPage(where, forced) {
    return forced !== "hosted" && where.protocol === "http:" && where.hostname === "127.0.0.1";
  }

  function isSafeLaunchUrl(value) {
    const match = typeof value === "string" ? /^http:\/\/127\.0\.0\.1:(\d{1,5})\/$/.exec(value) : null;
    return Boolean(match) && Number(match[1]) >= 1 && Number(match[1]) <= 65535;
  }

  /* The local service answer is untrusted input: keep only the contracted fields. */
  function readQuickstartState(data, catalog) {
    if (!isPlainObject(data) || data.local !== true) return null;
    let choices = null;
    try { choices = data.choices === null || data.choices === undefined ? null : validateChoices(data.choices, catalog); } catch (error) { choices = null; }
    const keys = isPlainObject(data.credentials) ? data.credentials : {};
    const credentials = {nvidia: keys.nvidia === true, openai: keys.openai === true, anthropic: keys.anthropic === true};
    const install = isPlainObject(data.install) && ["idle", "running", "ready", "error"].includes(data.install.state) ? data.install.state : "idle";
    return {local: true, choices, credentials, install};
  }

  /* A saved choices file is untrusted: at most 2 KiB, no duplicate keys, exact fields, fixed error text. */
  const IMPORT_LIMIT = 2048;
  const IMPORT_ERROR = "Choose a Jarvis choices file saved from this setup page (under 2 KB).";
  function importedChoices(text, catalog) {
    try {
      if (typeof text !== "string" || new TextEncoder().encode(text).length > IMPORT_LIMIT) throw new Error();
      const data = parseImportJson(text);
      if (!isPlainObject(data) || Object.keys(data).length !== 2 || data.schema !== CHOICES_SCHEMA || !own(data, "choices")) throw new Error();
      return validateChoices(data.choices, catalog);
    } catch (error) {
      throw new Error(IMPORT_ERROR);
    }
  }

  const api = {validateAnswers, buildPlan, defaultAnswers, readProviderCatalog, importedAnswers, isMobilePlanner, money,
    validateChoices, recommend, choicesFromQuery, isLoopbackPage, isSafeLaunchUrl, readQuickstartState, importedChoices, IMPORT_LIMIT, IMPORT_ERROR, CHOICE_ERRORS, QUICKSTART_SCHEMA, CHOICES_SCHEMA};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", () => boot(api));
})(typeof globalThis !== "undefined" ? globalThis : this);

/* Fixed, input-free messages. Server error text is never shown. */
const MESSAGES = {
  catalog: "Setup could not load. Refresh the page to try again.",
  local: "Jarvis could not reach its setup service on this computer. Close this page and open the Jarvis start file again.",
  query: "That link had choices this page does not recognize, so the defaults are shown.",
  save: "Your choices were not saved. Try again.",
  keyEmpty: "Paste a key first.",
  key: "The key was not saved. Check that you copied the whole key, then try again.",
  keySaved: "Key saved on this computer. The field was cleared.",
  keyDelete: "The key was not removed. Try again.",
  keyDeleted: "Key removed from this computer.",
  consent: "Tick the box to allow a short request first.",
  test: "The test did not succeed. Check the key, your internet connection and the provider's status page.",
  tested: "Connection works.",
  promptEmpty: "Type a question first.",
  chat: "No reply. Check the key and your internet connection, then try again.",
  install: "Install did not start. Try again.",
  installIdle: "Downloads only what your plan needs. Nothing is switched on.",
  installRunning: "Installing. You can keep this page open.",
  installReady: "Selected tools are installed. Nothing was switched on.",
  installError: "Install stopped before finishing. Check your internet connection, then choose Install selected tools again.",
  launch: "The dashboard did not open. Try again, or close Jarvis and open its start file again.",
  launchWait: "Install the selected tools first. The dashboard opens once they are ready.",
  exit: "Setup could not close itself. Close the Jarvis window to stop it.",
  exited: "Setup closed. You can close this page.",
  copied: "Link copied. Open it on your Windows computer.",
  copyFailed: "Select the link above and copy it yourself.",
  savedJson: "Choices saved. The file holds your three answers only, nothing secret.",
  importBad: "Choose a Jarvis choices file saved from this setup page (under 2 KB).",
  imported: "Saved choices loaded. Nothing was switched on.",
};

function boot(api) {
  const $ = (id) => document.getElementById(id);
  const meta = document.querySelector('meta[name="jarvis-mode"]');
  const forced = meta ? meta.content : "auto";
  const state = {catalog: null, choices: null, token: "", local: false, server: null, phone: false, busy: false, polls: 0};

  function el(tag, attributes, children) {
    const node = document.createElement(tag);
    Object.entries(attributes || {}).forEach(([key, value]) => {
      if (key === "text") node.textContent = value;
      else if (key === "className") node.className = value;
      else node.setAttribute(key, value);
    });
    (children || []).forEach((child) => node.append(child));
    return node;
  }
  function status(message, bad) {
    $("status").textContent = message;
    $("status").classList.toggle("bad", Boolean(bad));
  }
  function notice(message) {
    $("notice").textContent = message;
    $("notice").hidden = !message;
  }

  async function localJson(path, body) {
    if (!api.isLoopbackPage(location, forced) || (!state.local && path !== "/api/session" && path !== "/api/quickstart")) throw new Error("not local");
    const options = body === undefined ? {cache: "no-store"} : {method: "POST", cache: "no-store", headers: {"Content-Type": "application/json", "X-Jarvis-Token": state.token}, body: JSON.stringify(body)};
    const response = await fetch(path, options);
    if (!response.ok) throw new Error("request failed");
    return response.json();
  }
  const closed = () => document.body.classList.contains("closed");
  function lockSelects() {
    const locked = closed() || state.busy || Boolean(state.local && state.server && state.server.install === "running");
    ["role", "budget", "existing"].forEach((id) => { $(id).disabled = locked; });
  }
  /* One local mutation at a time: the dropdowns and the triggering button stay disabled until it settles. */
  async function act(button, work) {
    if (state.busy) return;
    state.busy = true;
    if (button) button.disabled = true;
    lockSelects();
    try { await work(); } finally {
      state.busy = false;
      if (button && !closed()) button.disabled = false;
      if (state.local && state.choices) render();
      lockSelects();
    }
  }

  function fillSelect(id, items) {
    $(id).replaceChildren(...items.map((item) => el("option", {value: String(item.id), text: item.label})));
  }
  function provider(plan) { return state.catalog.providers[plan.provider]; }

  function render() {
    const catalog = state.catalog;
    const plan = api.recommend(state.choices, catalog);
    $("role").value = plan.choices.role;
    $("budget").value = String(plan.choices.budget);
    $("existing").value = plan.choices.existing;
    $("plan").hidden = false;
    $("plan-title").textContent = plan.title;
    $("plan-summary").textContent = plan.summary;
    const chips = plan.features.map((name) => el("li", {text: catalog.features[name].name}));
    chips.push(el("li", {className: "ai", text: "AI: " + provider(plan).short + (plan.model ? " (" + plan.model + ")" : "")}));
    $("plan-chips").replaceChildren(...chips);
    $("plan-steps").replaceChildren(...plan.steps.map((text, index) => {
      const done = state.local && index < 2;
      return el("li", {className: done ? "done" : ""}, done ? [el("span", {text}), el("span", {className: "done-tag", text: "Done"})] : [el("span", {text})]);
    }));
    $("plan-caveat").textContent = plan.tradeoffs[0];
    $("plan-tradeoffs").replaceChildren(...plan.tradeoffs.map((text) => el("li", {text})));
    $("provider-link").href = plan.setup_url;
    $("provider-link").textContent = provider(plan).short + " setup page";
    $("handoff-link").textContent = handoffLink(plan.choices);
    if (state.local) renderLocal(plan);
    lockSelects();
  }

  function handoffLink(choices) {
    const url = new URL(location.href);
    url.search = new URLSearchParams({role: choices.role, budget: String(choices.budget), existing: choices.existing}).toString();
    url.hash = "";
    return url.href;
  }

  function keyInput() {
    let input = $("key-input");
    if (!input) {
      input = el("input", {id: "key-input", type: "password", autocomplete: "off", autocapitalize: "off", spellcheck: "false", maxlength: "2048", "data-lpignore": "true", "data-1p-ignore": "true"});
      $("key-row").prepend(input);
    }
    return input;
  }

  function renderLocal(plan) {
    const server = state.server;
    const info = provider(plan);
    const cloud = info.kind === "cloud";
    const saved = cloud && server.credentials[plan.provider];
    $("key-box").hidden = !cloud;
    if (cloud) {
      keyInput();
      $("key-help").href = plan.setup_url;
      $("key-help").textContent = "Get my " + info.short + " API key";
      $("key-label").textContent = "Paste your " + info.short + " API key";
      $("key-form").hidden = saved;
      $("key-saved").hidden = !saved;
      $("key-saved-text").textContent = info.short + " key saved on this computer.";
    }
    $("ai-box").hidden = !(saved || info.kind === "local");
    const install = server.install;
    $("install").disabled = install === "running" || closed();
    $("launch").disabled = install !== "ready" || closed();
    $("install").textContent = install === "running" ? "Installing..." : install === "ready" ? "Install again" : "Install selected tools";
    $("install").className = "button " + (install === "ready" ? "ghost" : "primary");
    $("launch").className = "button " + (install === "ready" ? "primary" : "ghost");
    $("install-state").textContent = MESSAGES[{idle: "installIdle", running: "installRunning", ready: "installReady", error: "installError"}[install]];
    $("install-state").classList.toggle("bad", install === "error");
  }

  function applyServer(data) {
    const next = api.readQuickstartState(data, state.catalog);
    if (!next) throw new Error("bad state");
    state.server = next;
    return next;
  }

  function savedOrDefault() {
    return (state.server && state.server.choices) || api.validateChoices(state.catalog.defaults, state.catalog);
  }

  /* Saves choices to the local service. Its answer is authoritative; on failure the display rolls back. */
  async function commit(next) {
    state.choices = next;
    render();
    try {
      const server = applyServer(await localJson("/api/quickstart/save", {choices: next}));
      state.choices = server.choices || next;
      return true;
    } catch (error) {
      state.choices = savedOrDefault();
      status(MESSAGES.save, true);
      return false;
    } finally {
      render();
    }
  }

  async function choose() {
    let next;
    try {
      next = api.validateChoices({role: $("role").value, budget: $("budget").value, existing: $("existing").value}, state.catalog);
    } catch (error) {
      status(error.message, true);
      render();
      return;
    }
    notice("");
    status("");
    if (!state.local) {
      state.choices = next;
      render();
      return;
    }
    if (state.busy || state.server.install === "running") { render(); return; }
    await act(null, () => commit(next));
  }

  async function saveKey(event) {
    event.preventDefault();
    const input = keyInput();
    const plan = api.recommend(state.choices, state.catalog);
    const value = input.value.trim();
    input.value = "";
    if (!value) { status(MESSAGES.keyEmpty, true); input.focus(); return; }
    await act(event.submitter || null, async () => {
      try {
        applyServer(await localJson("/api/quickstart/key", {provider: plan.provider, key: value}));
        status(MESSAGES.keySaved);
      } catch (error) {
        status(MESSAGES.key, true);
      }
      input.value = "";
      render();
    });
  }

  function needConsent() {
    if ($("consent").checked) return false;
    status(MESSAGES.consent, true);
    $("consent").focus();
    return true;
  }

  async function pollInstall() {
    if (state.server.install !== "running" || state.polls > 600) return;
    state.polls += 1;
    await new Promise((resolve) => setTimeout(resolve, 1500));
    try { applyServer(await localJson("/api/quickstart")); } catch (error) { state.server.install = "error"; }
    render();
    pollInstall();
  }

  const actions = {
    async "key-delete"(button) {
      const plan = api.recommend(state.choices, state.catalog);
      try {
        applyServer(await localJson("/api/quickstart/key/delete", {provider: plan.provider}));
        status(MESSAGES.keyDeleted);
      } catch (error) { status(MESSAGES.keyDelete, true); }
      render();
    },
    async test() {
      if (needConsent()) return;
      const plan = api.recommend(state.choices, state.catalog);
      try {
        await localJson("/api/quickstart/test", {provider: plan.provider, consent: true});
        status(MESSAGES.tested);
      } catch (error) { status(MESSAGES.test, true); }
    },
    async chat() {
      if (needConsent()) return;
      const prompt = $("prompt").value.trim();
      if (!prompt) { status(MESSAGES.promptEmpty, true); $("prompt").focus(); return; }
      const plan = api.recommend(state.choices, state.catalog);
      try {
        const result = await localJson("/api/quickstart/chat", {provider: plan.provider, prompt, consent: true});
        if (typeof result.reply !== "string") throw new Error("bad reply");
        $("reply").textContent = result.reply.slice(0, 4000);
        $("reply").hidden = false;
        status("");
      } catch (error) {
        $("reply").hidden = true;
        status(MESSAGES.chat, true);
      }
    },
    async install() {
      if (!(await commit(state.choices))) return;
      try {
        applyServer(await localJson("/api/quickstart/install", {consent: true}));
        status("");
      } catch (error) { status(MESSAGES.install, true); }
      render();
      state.polls = 0;
      pollInstall();
    },
    async launch() {
      if (state.server.install !== "ready") { status(MESSAGES.launchWait, true); return; }
      try {
        const result = await localJson("/api/quickstart/launch", {consent: true});
        if (!api.isSafeLaunchUrl(result.url)) throw new Error("unexpected address");
        location.assign(result.url);
      } catch (error) { status(MESSAGES.launch, true); }
    },
    async exit() {
      try {
        await localJson("/api/setup/exit", {});
        document.body.classList.add("closed");
        document.querySelectorAll("button, input, select, textarea").forEach((control) => { control.disabled = true; });
        status(MESSAGES.exited);
      } catch (error) { status(MESSAGES.exit, true); }
    },
    async "import-choices"() {
      $("import-choices").click();
    },
    async "copy-link"() {
      const text = $("handoff-link").textContent;
      try {
        if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") throw new Error("no clipboard");
        await navigator.clipboard.writeText(text);
        status(MESSAGES.copied);
      } catch (error) { status(MESSAGES.copyFailed, true); }
    },
    async "save-json"() {
      const body = JSON.stringify({schema: api.CHOICES_SCHEMA, choices: state.choices}, null, 2) + "\n";
      const url = URL.createObjectURL(new Blob([body], {type: "application/json"}));
      const link = el("a", {href: url, download: "jarvis-choices.json"});
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status(MESSAGES.savedJson);
    },
  };

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button || !state.choices || !own(actions, button.dataset.action)) return;
    act(button, () => actions[button.dataset.action](button));
  });
  $("choices").addEventListener("change", choose);
  $("choices").addEventListener("submit", (event) => event.preventDefault());
  $("key-form").addEventListener("submit", saveKey);
  $("import-choices").addEventListener("change", () => {
    const input = $("import-choices");
    const file = input.files && input.files[0];
    if (!file || !state.local || !state.choices) { input.value = ""; return; }
    act(null, async () => {
      let next;
      try {
        if (file.size > api.IMPORT_LIMIT) throw new Error("too large");
        next = api.importedChoices(await file.text(), state.catalog);
      } catch (error) {
        status(MESSAGES.importBad, true);
        return;
      } finally {
        input.value = "";
      }
      if (await commit(next)) status(MESSAGES.imported);
    });
  });
  function own(object, key) { return Object.prototype.hasOwnProperty.call(object, key); }

  function links(release) {
    $("feedback").href = release.feedback;
    $("source-repo").href = release.source_repo;
    $("source-zip").href = release.source_zip;
    document.querySelectorAll(".agent-guide").forEach((link) => { link.href = release.agent_guide; });
    const ready = release.status === "available";
    $("download").hidden = !ready;
    $("download-pending").hidden = ready;
    if (ready) $("download").href = release.windows_zip;
    $("download-note").textContent = ready ? "Unzip it, then open Start-Jarvis-Windows.cmd." : "The first Windows package is still being prepared. Until then, the source ZIP below runs with Python 3.11 or newer through Start-Jarvis.cmd.";
  }

  async function start() {
    try {
      const response = await fetch("quickstart-catalog.json", {cache: "no-store"});
      if (!response.ok) throw new Error("catalog unavailable");
      state.catalog = await response.json();
      if (state.catalog.schema !== api.QUICKSTART_SCHEMA) throw new Error("catalog schema");
      fillSelect("role", state.catalog.roles);
      fillSelect("budget", state.catalog.budgets);
      fillSelect("existing", state.catalog.existing);
      state.choices = api.validateChoices(state.catalog.defaults, state.catalog);
      links(state.catalog.release);
    } catch (error) {
      $("mode-badge").textContent = "UNAVAILABLE";
      $("choices").hidden = true;
      notice(MESSAGES.catalog);
      return;
    }
    if (api.isLoopbackPage(location, forced)) {
      try {
        const session = await localJson("/api/session");
        if (!session || typeof session.token !== "string" || !session.token) throw new Error("no session");
        state.token = session.token;
        const server = api.readQuickstartState(await localJson("/api/quickstart"), state.catalog);
        if (!server) throw new Error("not local");
        state.local = true;
        state.server = server;
        if (server.choices) state.choices = server.choices;
      } catch (error) {
        state.local = false;
        state.token = "";
        notice(MESSAGES.local);
      }
    } else {
      try {
        const linked = api.choicesFromQuery(location.search, state.catalog);
        if (linked) state.choices = linked;
      } catch (error) {
        notice(MESSAGES.query);
      }
    }
    state.phone = !state.local && api.isMobilePlanner(navigator);
    $("mode-badge").textContent = state.local ? "ON THIS COMPUTER" : state.phone ? "PHONE PLANNER" : "PLANNER";
    $("local").hidden = !state.local;
    $("get").hidden = state.local || state.phone;
    $("phone").hidden = !state.phone;
    $("privacy-link").hidden = state.local;
    render();
    if (state.local && state.server.install === "running") pollInstall();
  }
  start();
}
