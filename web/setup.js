"use strict";
/* Jarvis setup wizard. Deterministic planner, no provider calls, no credentials.
   The planner mirrors jarvis/planner.py; tests/test_planner.py checks both agree. */
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

  const api = {validateAnswers, buildPlan, defaultAnswers, readProviderCatalog, importedAnswers, isMobilePlanner, money};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", () => boot(api));
})(typeof globalThis !== "undefined" ? globalThis : this);

function boot(api) {
  const $ = (id) => document.getElementById(id);
  const STEPS = ["welcome", "purpose", "budget", "tools", "voice", "device", "plan"];
  const LABELS = ["Start", "Use", "Budget", "AI tools", "Voice", "Computer", "Plan"];
  const meta = document.querySelector('meta[name="jarvis-mode"]');
  const forced = meta ? meta.content : "auto";
  const local = forced !== "hosted" && location.protocol === "http:" && location.hostname === "127.0.0.1";
  const phone = !local && api.isMobilePlanner(navigator);
  const state = {catalog: null, answers: null, step: "welcome", token: "", diagnostics: null, providers: null, saved: false};

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
  function copyText(text, success, failure) {
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
      status(failure, true);
      return;
    }
    navigator.clipboard.writeText(text).then(() => status(success), () => status(failure, true));
  }
  async function localJson(path, body) {
    const options = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-Jarvis-Token": state.token}, body: JSON.stringify(body)};
    const response = await fetch(path, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Request failed");
    return result;
  }

  function show(step) {
    state.step = step;
    document.querySelectorAll(".step").forEach((section) => { section.hidden = section.dataset.step !== step; });
    const index = STEPS.indexOf(step);
    $("progress").replaceChildren(...STEPS.map((name, position) => el("li", {className: position < index ? "done" : position === index ? "current" : "", text: LABELS[position], "aria-current": position === index ? "step" : "false"})));
    $("progress").parentElement.hidden = step === "declined";
    if (step === "plan") renderPlan();
    $("wizard").focus({preventScroll: true});
    window.scrollTo({top: 0});
  }

  function radioCard(group, item, checked, title, body, badge) {
    const input = el("input", {type: "radio", name: group, value: item.id});
    input.checked = checked;
    const children = [input, el("b", {text: title})];
    if (badge) children.push(el("span", {className: "tag " + badge.toLowerCase().replace(/[^a-z]+/g, "-"), text: badge}));
    children.push(el("p", {text: body}));
    return el("label", {className: "choice"}, children);
  }

  function renderPurpose() {
    const catalog = state.catalog;
    $("presets").replaceChildren(...catalog.presets.map((preset) => radioCard("preset", preset, state.answers.preset === preset.id, preset.name, preset.goal)));
    const selectable = catalog.features.filter((feature) => feature.selectable);
    $("custom-list").replaceChildren(...selectable.map((feature) => {
      const input = el("input", {type: "checkbox", value: feature.id});
      input.checked = state.answers.features.includes(feature.id);
      input.addEventListener("change", () => setFeature(feature.id, input.checked));
      return el("label", {className: "check"}, [input, el("span", {text: feature.name + (feature.maturity === "preview" ? " (preview)" : "")})]);
    }));
    $("custom-features").hidden = state.answers.preset !== "custom";
  }
  function setFeature(id, on) {
    const set = new Set(state.answers.features);
    if (on) set.add(id); else set.delete(id);
    state.answers.features = Array.from(set);
  }

  function renderChoices() {
    const catalog = state.catalog;
    $("ai-tool").replaceChildren(...catalog.ai_tools.map((tool) => el("option", {value: tool.id, text: tool.name})));
    $("ai-tool").value = state.answers.ai_tool;
    $("ai-guidance").textContent = catalog.ai_tools.find((tool) => tool.id === state.answers.ai_tool).guidance;
    $("voice-options").replaceChildren(...catalog.voice_options.map((voice) => radioCard("voice", voice, state.answers.voice === voice.id, voice.name, voice.guidance,
      voice.kind === "implemented" ? "BUILT IN" : voice.kind === "guidance" ? "GUIDANCE ONLY" : "")));
    $("device-os").replaceChildren(...catalog.os.map((item) => el("option", {value: item.id, text: item.name})));
    $("device-os").value = state.answers.device.os;
    $("device-ram").replaceChildren(...catalog.ram.map((item) => el("option", {value: item.id, text: item.name})));
    $("device-ram").value = state.answers.device.ram;
    ["camera", "microphone"].forEach((name) => {
      $("device-" + name).replaceChildren(...[[true, "Yes"], [false, "No"]].map(([value, label]) => {
        const input = el("input", {type: "radio", name: "device-" + name, value: String(value)});
        input.checked = state.answers.device[name] === value;
        input.addEventListener("change", () => { state.answers.device[name] = value; });
        return el("label", {className: "pill-choice"}, [input, el("span", {text: label})]);
      }));
    });
    $("budget-range").value = String(Math.min(500, Math.round(state.answers.budget_usd / 5) * 5));
    $("budget-number").value = String(state.answers.budget_usd);
  }

  function taskState(task) {
    if (task.kind === "pending") return ["pending", "Not built yet"];
    if (task.id === "save") return state.saved ? ["done", "Saved"] : local ? ["automatic", "Jarvis does this when you choose Apply"] : ["manual", "Available when you run the wizard on your computer"];
    if (!task.check.length) return [task.kind, task.kind === "automatic" ? "Jarvis does this" : "You do this"];
    const diagnostics = state.diagnostics;
    if (!diagnostics) return ["manual", "You do this"];
    const ok = task.check.every((check) => check === "local" ? true : check === "python" ? diagnostics.python_ok : check === "voice_model" ? diagnostics.voice_model : check === "hand_model" ? diagnostics.hand_model : Boolean(diagnostics.packages[check]));
    return ok ? ["done", "Detected on this computer"] : ["manual", "Remaining"];
  }

  function currentPlan() {
    return api.buildPlan(api.validateAnswers(state.answers, state.catalog), state.catalog);
  }

  function renderPlan() {
    let plan;
    try {
      plan = currentPlan();
    } catch (error) {
      status(error.message, true);
      return;
    }
    const included = plan.features.filter((feature) => feature.status === "included");
    const remaining = plan.tasks.filter((task) => taskState(task)[0] === "manual").length;
    $("plan-summary").textContent = included.length + " features match your choices. " + remaining + " task" + (remaining === 1 ? "" : "s") + " left to check. Monthly software cost for Jarvis: $0.";
    const selectable = new Set(state.catalog.features.filter((feature) => feature.selectable).map((feature) => feature.id));
    $("plan-features").replaceChildren(...plan.features.map((feature) => {
      const row = el("li", {className: "feature " + feature.status});
      const head = el("div", {className: "feature-head"});
      if (selectable.has(feature.id)) {
        const input = el("input", {type: "checkbox", "aria-label": "Use " + feature.name});
        input.checked = state.answers.features.includes(feature.id);
        input.addEventListener("change", () => { setFeature(feature.id, input.checked); state.saved = false; renderPlan(); });
        head.append(input);
      }
      head.append(el("b", {text: feature.name}));
      const label = feature.status === "included" ? (feature.maturity === "preview" ? "PREVIEW" : "INCLUDED") : feature.status === "unavailable" ? "UNAVAILABLE" : "OFF";
      head.append(el("span", {className: "tag " + feature.status, text: label}));
      row.append(head);
      feature.reasons.concat(feature.notes).forEach((note) => row.append(el("p", {text: note})));
      return row;
    }));
    $("camera-index").value = String(state.answers.preferences.camera_index);
    $("cursor-reminder").hidden = !included.some((feature) => feature.id === "hand_control");
    $("hand-cursor").checked = state.answers.preferences.hand_move_cursor;
    $("cost-notes").replaceChildren(...plan.cost.notes.map((note) => el("li", {text: note})));
    $("guidance").replaceChildren(...plan.guidance.map((item) => {
      const li = el("li", {}, [el("b", {text: item.name + ": "}), document.createTextNode(item.text + " ")]);
      if (item.url) li.append(el("a", {href: item.url, target: "_blank", rel: "noopener noreferrer", text: "Official page"}));
      return li;
    }));
    const chosen = [state.catalog.ai_tools.find((tool) => tool.id === plan.answers.ai_tool), state.catalog.voice_options.find((voice) => voice.id === plan.answers.voice)]
      .flatMap((item) => [item.id, item.provider]).filter(Boolean);
    const providers = state.providers ? state.providers.entries.filter((entry) => chosen.includes(entry.id)) : [];
    $("provider-info").hidden = !providers.length && !(state.providers && state.providers.budgetNote);
    const items = providers.map((entry) => {
      const billing = entry.monthly === 0 ? "No software fee." : entry.billing === "usage-based" ? "Billed by usage." : "";
      const li = el("li", {}, [el("b", {text: (entry.name || entry.id) + ": "}),
        document.createTextNode([entry.description, entry.creditNote, billing, entry.exampleRate].filter(Boolean).join(" ") + " ")]);
      entry.links.forEach((item, index) => {
        if (index) li.append(document.createTextNode(" · "));
        li.append(el("a", {href: item.url, target: "_blank", rel: "noopener noreferrer", text: item.label}));
      });
      return li;
    });
    if (state.providers && state.providers.budgetNote) items.push(el("li", {text: state.providers.budgetNote}));
    if (state.providers && state.providers.checked) items.push(el("li", {className: "note", text: "Catalog checked on " + state.providers.checked + ". Providers change prices and benefits; their official pages are the source of truth."}));
    $("provider-list").replaceChildren(...items);
    $("tasks").replaceChildren(...plan.tasks.map((task) => {
      const [kind, label] = taskState(task);
      const li = el("li", {className: "task " + kind}, [el("div", {className: "task-head"}, [el("b", {text: task.title}), el("span", {className: "pill " + kind, text: label})]), el("p", {text: task.detail})]);
      if (task.command) li.append(el("code", {text: task.command}));
      if (phone && ["download", "hand_model"].includes(task.id)) li.append(el("p", {className: "note", text: "Open the computer setup link above on your computer to complete this download."}));
      else if (task.url) li.append(el("a", {href: task.url, target: "_blank", rel: "noopener noreferrer", text: "Official download or page"}));
      else if (task.id === "download" && !local) li.append(el("p", {className: "note", text: "The release download link will be added here."}));
      return li;
    }));
    $("launch-command").textContent = plan.launch_command;
    const release = state.catalog.release.download_url;
    $("release-link").hidden = local || phone || !release;
    if (release) { $("release-link").href = release; $("release-link").rel = "noopener noreferrer"; }
    $("apply").hidden = !local;
    $("dashboard-link").hidden = !local || !state.saved;
    $("exit-plan").hidden = !(local && state.diagnostics && state.diagnostics.can_exit);
  }

  function download() {
    let plan;
    try { plan = currentPlan(); } catch (error) { status(error.message, true); return; }
    const url = URL.createObjectURL(new Blob([JSON.stringify(plan, null, 2) + "\n"], {type: "application/json"}));
    const link = el("a", {href: url, download: "jarvis-setup-plan.json"});
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status("Plan downloaded. It contains your answers only, no secrets.");
  }

  async function importPlan(file) {
    try {
      if (file.size > 16384) throw new Error("That file is too large to be a Jarvis plan");
      state.answers = api.importedAnswers(await file.text(), state.catalog);
      state.saved = false;
      renderPurpose();
      renderChoices();
      show(state.answers.install ? "plan" : "declined");
      status("Plan imported. Nothing was armed or turned on.");
    } catch (error) {
      status("Could not import that plan. Choose a valid Jarvis plan JSON file under 16 KB.", true);
    }
  }

  async function apply() {
    try {
      const result = await localJson("api/setup/apply", {answers: api.validateAnswers(state.answers, state.catalog)});
      state.saved = true;
      state.diagnostics = await localJson("api/setup/diagnostics");
      renderPlan();
      status("Saved to " + result.path + ". Nothing was armed. Use the launch command when you are ready.");
    } catch (error) {
      status("Not saved: " + error.message, true);
    }
  }

  async function exit() {
    try {
      await localJson("api/setup/exit", {});
      document.querySelectorAll("button, input, select").forEach((control) => { control.disabled = true; });
      const message = "Jarvis stopped. Nothing is running in the background. You can close this page.";
      if (state.step === "declined") $("exit-note").textContent = message; else status(message);
    } catch (error) {
      const message = "Could not stop Jarvis: " + error.message + ". Close the Jarvis window or press Ctrl+C in it.";
      if (state.step === "declined") $("exit-note").textContent = message; else status(message, true);
    }
  }

  /* Optional read-aloud: only on-device voices (localService), only after a click. No cloud speech, no microphone. */
  function speak() {
    const note = $("speak-note");
    const synth = window.speechSynthesis;
    if (synth.speaking) { synth.cancel(); note.textContent = ""; return; }
    const voices = synth.getVoices().filter((voice) => voice.localService);
    if (!voices.length) { note.textContent = "No on-device voice found. The text stays on screen."; return; }
    const section = document.querySelector('.step:not([hidden])');
    const parts = [section.querySelector("h1"), section.querySelector(".lead")].filter(Boolean).map((node) => node.textContent);
    const utterance = new SpeechSynthesisUtterance(parts.join(". "));
    const lang = (navigator.language || "en").slice(0, 2);
    utterance.voice = voices.find((voice) => voice.lang.startsWith(lang)) || voices[0];
    utterance.onend = () => { note.textContent = ""; };
    note.textContent = "Reading with an on-device voice. Click again to stop.";
    synth.speak(utterance);
  }

  function next() {
    if (state.step === "purpose" && state.answers.preset === "custom" && !state.answers.features.length) {
      $("custom-features").classList.add("warn");
      $("custom-list").querySelector("input").focus();
      return;
    }
    show(STEPS[Math.min(STEPS.length - 1, STEPS.indexOf(state.step) + 1)]);
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (action === "install-yes") { state.answers.install = true; show("purpose"); }
    else if (action === "install-no") { state.answers.install = false; $("exit-local").hidden = !(local && state.diagnostics && state.diagnostics.can_exit); show("declined"); }
    else if (action === "restart") { state.answers = api.defaultAnswers(state.catalog); renderPurpose(); renderChoices(); show("welcome"); }
    else if (action === "next") next();
    else if (action === "back") show(STEPS[Math.max(0, STEPS.indexOf(state.step) - 1)]);
    else if (action === "download") download();
    else if (action === "apply") apply();
    else if (action === "exit") exit();
    else if (action === "speak") speak();
    else if (action === "copy-setup") copyText($("setup-address").textContent, "Computer setup link copied. Save the plan separately to keep your choices.", "Select the computer setup address above and copy it.");
    else if (action === "copy") copyText($("launch-command").textContent, "Command copied.", "Select the command and copy it yourself.");
  });
  document.addEventListener("change", (event) => {
    const target = event.target;
    if (target.name === "preset") {
      state.answers.preset = target.value;
      const preset = state.catalog.presets.find((item) => item.id === target.value);
      state.answers.features = preset.features.slice();
      if (target.value === "creator" && state.answers.voice === "no_microphone" && state.answers.device.microphone) state.answers.voice = "offline_vosk";
      renderPurpose();
    } else if (target.name === "voice") {
      state.answers.voice = target.value;
      if (target.value === "offline_vosk") state.answers.device.microphone = true;
      renderChoices();
    } else if (target.id === "ai-tool") {
      state.answers.ai_tool = target.value;
      renderChoices();
    } else if (target.id === "device-os") state.answers.device.os = target.value;
    else if (target.id === "device-ram") state.answers.device.ram = target.value;
    else if (target.id === "camera-index") {
      const value = Number(target.value);
      state.answers.preferences.camera_index = Number.isInteger(value) && value >= 0 && value <= 9 ? value : 0;
      state.saved = false;
      renderPlan();
    } else if (target.id === "hand-cursor") {
      state.answers.preferences.hand_move_cursor = target.checked;
      state.saved = false;
      renderPlan();
    } else if (target.id === "import" && target.files.length) {
      importPlan(target.files[0]);
      target.value = "";
    }
  });
  function setBudget(raw, source) {
    const value = Number(raw);
    if (!Number.isFinite(value)) return;
    state.answers.budget_usd = Math.min(500, Math.max(0, Math.floor(value * 100 + 0.5) / 100));
    if (source !== "range") $("budget-range").value = String(Math.round(state.answers.budget_usd / 5) * 5);
    if (source !== "number") $("budget-number").value = String(state.answers.budget_usd);
  }
  $("budget-range").addEventListener("input", (event) => setBudget(event.target.value, "range"));
  $("budget-number").addEventListener("input", (event) => setBudget(event.target.value, "number"));

  async function start() {
    $("mode-badge").textContent = local ? "ON THIS COMPUTER" : "PLANNER ONLY";
    $("desktop-handoff").hidden = local;
    $("setup-address").textContent = new URL("setup.html", location.href).href;
    $("speak").hidden = !("speechSynthesis" in window && "SpeechSynthesisUtterance" in window);
    try {
      const response = await fetch("setup-catalog.json", {cache: "no-store"});
      if (!response.ok) throw new Error("catalog unavailable");
      state.catalog = await response.json();
    } catch (error) {
      $("mode-badge").textContent = "UNAVAILABLE";
      $("wizard").prepend(el("p", {className: "card bad", text: "The setup catalog could not load. Open this page through a web server or with Start-Jarvis.cmd."}));
      return;
    }
    state.answers = api.defaultAnswers(state.catalog);
    const platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    const mobilePlanner = api.isMobilePlanner(navigator);
    if (!mobilePlanner && /mac/i.test(platform)) state.answers.device.os = "macos";
    else if (!mobilePlanner && /linux|x11/i.test(platform)) state.answers.device.os = "linux";
    try {
      const response = await fetch("provider-catalog.json", {cache: "no-store"});
      if (response.ok) state.providers = api.readProviderCatalog(await response.json(), state.catalog);
    } catch (error) {
      state.providers = null;
    }
    if (local) {
      try {
        state.token = (await localJson("api/session")).token;
        state.diagnostics = await localJson("api/setup/diagnostics");
        if (["windows", "macos", "linux"].includes(state.diagnostics.os)) state.answers.device.os = state.diagnostics.os;
        $("device-detected").textContent = "Detected " + state.diagnostics.os + " with Python " + state.diagnostics.python + ".";
        const saved = (await localJson("api/setup/config")).answers;
        if (saved) { state.answers = saved; state.answers.install = true; state.saved = true; }
      } catch (error) {
        $("mode-badge").textContent = "LOCAL SESSION UNAVAILABLE";
      }
    }
    renderPurpose();
    renderChoices();
    show("welcome");
  }
  start();
}
