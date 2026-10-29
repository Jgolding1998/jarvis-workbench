"use strict";
/* Jarvis setup. The one-question-per-screen quickstart mirrors jarvis/quickstart.py (tests/test_quickstart.py).
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

  /* Step-by-step quickstart. Mirrors jarvis/quickstart.py; tests/test_quickstart.py checks both agree. */
  const QUICKSTART_SCHEMA = "jarvis-quickstart-catalog/1";
  const CHOICES_SCHEMA = "jarvis-quickstart-choices/2";
  const CHOICES_SCHEMAS = ["jarvis-quickstart-choices/1", CHOICES_SCHEMA];
  const REQUIRED_KEYS = ["role", "budget", "existing"];
  const OPTION_KEYS = ["dictation", "reply", "automation"];
  const CHOICE_KEYS = REQUIRED_KEYS.concat(OPTION_KEYS);
  const CHOICE_ERRORS = {
    shape: "Choose a purpose, a monthly budget and an AI option.",
    role: "Choose a purpose from the list.",
    budget: "Choose a whole-dollar monthly budget from $0 to $10,000.",
    existing: "Choose an AI option from the list.",
    dictation: "Choose a typing or voice option from the list.",
    reply: "Choose a reply option from the list.",
    automation: "Choose an automation option from the list.",
  };
  const STATUSES = ["builtin", "external", "manual", "later", "off"];
  const BUDGET_MAX = 10000;
  const findId = (items, id) => items.find((item) => item.id === id) || null;

  /* Older three-field choices gain the catalog defaults for dictation, reply and automation. Unknown fields are refused. */
  function validateChoices(data, catalog) {
    if (!isPlainObject(data) || !REQUIRED_KEYS.every((key) => own(data, key)) || Object.keys(data).some((key) => !CHOICE_KEYS.includes(key))) throw new Error(CHOICE_ERRORS.shape);
    const role = data.role;
    let budget = data.budget;
    const existing = data.existing;
    if (typeof role !== "string" || !findId(catalog.roles, role)) throw new Error(CHOICE_ERRORS.role);
    const limits = catalog.budget;
    if (typeof budget === "string" && /^(0|[1-9][0-9]{0,4})$/.test(budget)) budget = Number(budget);
    if (typeof budget !== "number" || !Number.isInteger(budget) || budget < limits.min || budget > limits.max) throw new Error(CHOICE_ERRORS.budget);
    if (typeof existing !== "string" || !findId(catalog.existing, existing)) throw new Error(CHOICE_ERRORS.existing);
    const result = {role, budget, existing};
    OPTION_KEYS.forEach((key) => {
      const value = own(data, key) ? data[key] : catalog.defaults[key];
      if (typeof value !== "string" || !findId(catalog[key], value)) throw new Error(CHOICE_ERRORS[key]);
      result[key] = value;
    });
    return result;
  }

  /* Structural check of quickstart-catalog.json. Mirrors validate_catalog() in jarvis/quickstart.py. */
  function checkCatalog(data) {
    const need = (condition, message) => { if (!condition) throw new Error("Quickstart catalog: " + message); };
    const text = (value) => typeof value === "string" && Array.from(value).length > 0 && Array.from(value).length <= 600 && !value.includes(String.fromCharCode(0x2014));
    const https = (value) => typeof value === "string" && value.length <= 300 && /^https:\/\/[^\s"'<>\\]+$/.test(value);
    const whole = (value) => typeof value === "number" && Number.isInteger(value);
    const keys = (value, required, extra) => isPlainObject(value) && required.every((key) => own(value, key)) && Object.keys(value).every((key) => required.includes(key) || (extra || []).includes(key));
    need(isPlainObject(data) && data.schema === QUICKSTART_SCHEMA, "unsupported schema");
    need(keys(data, ["schema", "defaults", "release", "roles", "budget", "existing", "dictation", "reply", "automation", "providers", "features", "notes", "budget_note", "steps"]), "unexpected or missing sections");
    const {providers, features, notes} = data;
    need([providers, features, notes].every((table) => isPlainObject(table) && Object.keys(table).length > 0), "tables missing");
    ["roles", "existing"].concat(OPTION_KEYS).forEach((name) => {
      const items = data[name];
      need(Array.isArray(items) && items.length > 0 && items.length <= 12, name + " list missing");
      need(items.every((item) => isPlainObject(item) && typeof item.id === "string" && /^[a-z][a-z_]{0,23}$/.test(item.id)), name + " ids invalid");
      need(new Set(items.map((item) => item.id)).size === items.length, "duplicate ids in " + name);
      need(items.every((item) => text(item.label) && Array.from(item.label).length <= 60 && text(item.hint)), name + " labels missing");
    });
    const budget = data.budget;
    const budgetKeys = ["min", "max", "step", "slider_max", "large_from"];
    need(keys(budget, budgetKeys) && budgetKeys.every((key) => whole(budget[key])), "budget range missing");
    need(budget.min === 0 && budget.step === 1 && budget.max === BUDGET_MAX, "budget must be whole dollars from 0 to 10000");
    need(budget.slider_max > 0 && budget.slider_max <= budget.max && budget.large_from > 0 && budget.large_from <= budget.max, "budget slider or large amount out of range");
    need(Object.keys(features).sort().join() === "camera,hand", "features table must list camera and hand");
    Object.values(features).forEach((feature) => need(keys(feature, ["name", "note", "step"]) && ["name", "note", "step"].every((key) => text(feature[key])), "feature text missing"));
    data.roles.forEach((role) => {
      need(keys(role, ["id", "label", "hint", "features", "title", "summary", "suggest"]) && text(role.title) && text(role.summary), "role fields invalid");
      need(Array.isArray(role.features) && role.features.length > 0 && new Set(role.features).size === role.features.length
        && role.features.every((name) => ["camera", "hand"].includes(name)), "role features unknown");
      need(keys(role.suggest, OPTION_KEYS) && OPTION_KEYS.every((key) => typeof role.suggest[key] === "string" && findId(data[key], role.suggest[key])), "role suggestion unknown");
    });
    data.existing.forEach((item) => {
      need(keys(item, ["id", "label", "hint", "zero", "paid", "zero_notes", "paid_notes"]), "existing fields invalid");
      need(typeof item.zero === "string" && own(providers, item.zero) && typeof item.paid === "string" && own(providers, item.paid), "existing provider unknown");
      ["zero_notes", "paid_notes"].forEach((key) => need(Array.isArray(item[key]) && item[key].every((note) => typeof note === "string" && own(notes, note)), "existing notes unknown"));
    });
    OPTION_KEYS.forEach((name) => data[name].forEach((item) => {
      need(keys(item, ["id", "label", "hint", "status", "name"], ["step", "note", "url"]) && STATUSES.includes(item.status) && text(item.name), name + " option invalid");
      need(["step", "note"].every((key) => !own(item, key) || text(item[key])) && (!own(item, "url") || https(item.url)), name + " option text invalid");
      /* Unbuilt tools never get install steps; manual setups always point to a guide. */
      need(item.status !== "later" || (own(item, "note") && !own(item, "step")), "later options need a note and no step");
      need(item.status !== "manual" || (own(item, "step") && own(item, "url")), "manual options need a step and a guide");
    }));
    Object.values(providers).forEach((provider) => {
      need(keys(provider, ["short", "name", "kind", "setup_url", "summary", "step"], ["model", "limit_url"]) && ["cloud", "local"].includes(provider.kind), "provider fields invalid");
      need(https(provider.setup_url) && ["short", "name", "summary", "step"].every((key) => text(provider[key])), "provider fields missing");
      need(!own(provider, "model") || text(provider.model), "provider model invalid");
      need(!own(provider, "limit_url") || https(provider.limit_url), "provider limit link invalid");
    });
    need(Object.values(notes).every(text) && own(notes, "large_budget"), "note text missing");
    need(text(data.budget_note), "budget note missing");
    const stepKeys = ["download", "start", "dashboard", "practice"];
    need(keys(data.steps, stepKeys) && stepKeys.every((key) => text(data.steps[key])), "steps missing");
    const release = data.release;
    const releaseKeys = ["windows_zip", "status", "source_zip", "source_repo", "agent_guide", "feedback"];
    need(keys(release, releaseKeys) && ["pending", "available"].includes(release.status), "release status unknown");
    need(releaseKeys.filter((key) => key !== "status").every((key) => https(release[key])), "release links must be https");
    need(keys(data.defaults, CHOICE_KEYS), "defaults missing");
    const canonical = validateChoices(data.defaults, data);
    need(CHOICE_KEYS.every((key) => canonical[key] === data.defaults[key]), "defaults are not canonical");
    return data;
  }

  function recommend(data, catalog) {
    const choices = validateChoices(data, catalog);
    const role = findId(catalog.roles, choices.role);
    const existing = findId(catalog.existing, choices.existing);
    const options = OPTION_KEYS.map((key) => findId(catalog[key], choices[key]));
    const paid = choices.budget > 0;
    const providerId = paid ? existing.paid : existing.zero;
    const provider = catalog.providers[providerId];
    const features = role.features.slice();
    const tradeoffs = existing[paid ? "paid_notes" : "zero_notes"].map((note) => catalog.notes[note])
      .concat(features.map((name) => catalog.features[name].note), options.filter((option) => own(option, "note")).map((option) => option.note));
    if (choices.budget >= catalog.budget.large_from) tradeoffs.push(catalog.notes.large_budget);
    tradeoffs.push(catalog.budget_note);
    const steps = [catalog.steps.download, catalog.steps.start, provider.step]
      .concat(features.map((name) => catalog.features[name].step), options.filter((option) => own(option, "step")).map((option) => option.step), [catalog.steps.dashboard]);
    if (features.includes("hand")) steps.push(catalog.steps.practice);
    const stack = [{part: "desk", name: features.map((name) => catalog.features[name].name).join(" and "), status: "builtin"},
      {part: "ai", name: provider.short + (own(provider, "model") ? " (" + provider.model + ")" : ""), status: "external"}];
    ["input", "reply", "automation"].forEach((part, index) => {
      const option = options[index];
      const row = {part, name: option.name, status: option.status};
      if (own(option, "url")) row.url = option.url;
      stack.push(row);
    });
    const plan = {choices, provider: providerId, features, title: role.title + " with " + provider.name,
      summary: role.summary + " " + provider.summary, stack, steps, tradeoffs, setup_url: provider.setup_url};
    if (own(provider, "model")) plan.model = provider.model;
    return plan;
  }

  /* Returns null when the address carries no choices; throws when it carries invalid or repeated ones. */
  function choicesFromQuery(search, catalog) {
    const params = new URLSearchParams(search);
    if (!CHOICE_KEYS.some((key) => params.has(key))) return null;
    const data = {};
    CHOICE_KEYS.forEach((key) => {
      if (params.getAll(key).length > 1) throw new Error(CHOICE_ERRORS.shape);
      if (params.has(key)) data[key] = params.get(key);
    });
    return validateChoices(data, catalog);
  }

  function choicesQuery(choices) {
    const params = new URLSearchParams();
    CHOICE_KEYS.forEach((key) => params.set(key, String(choices[key])));
    return "?" + params.toString();
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
    return {local: true, choices, credentials, install, budget: readBudget(data.budget)};
  }

  /* Local allowance reported by the service. Display only; the service enforces it. Anything malformed is dropped. */
  function readBudget(data) {
    const amount = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value < 1e6;
    const day = (value) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
    /* The service reports a broken ledger or config as unavailable. Its message is never shown. */
    if (isPlainObject(data) && data.kind === "local_allowance" && data.available === false) return {available: false};
    if (!isPlainObject(data) || data.kind !== "local_allowance" || typeof data.month !== "string" || !/^\d{4}-(0[1-9]|1[0-2])$/.test(data.month)) return null;
    if (!["limit_usd", "reserved_usd", "remaining_usd", "reserve_per_request_usd"].every((key) => amount(data[key]))) return null;
    if (!Number.isInteger(data.paid_requests) || data.paid_requests < 0) return null;
    return {available: true, month: data.month, limit: data.limit_usd, reserved: data.reserved_usd, remaining: data.remaining_usd, paidRequests: data.paid_requests,
      perRequest: data.reserve_per_request_usd, providerLimitRequired: data.provider_limit_required === true,
      pricingChecked: day(data.pricing_checked) ? data.pricing_checked : "", validUntil: day(data.valid_until) ? data.valid_until : ""};
  }

  function dollars(value) {
    return "$" + String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }
  function budgetLabel(value) {
    return value === 0 ? "$0, free options only" : dollars(value) + " a month";
  }

  /* A saved choices file is untrusted: at most 2 KiB, no duplicate keys, exact fields, fixed error text.
     Files from the earlier three-question page (schema /1) still load through the same migration. */
  const IMPORT_LIMIT = 2048;
  const IMPORT_ERROR = "Choose a Jarvis choices file saved from this setup page (under 2 KB).";
  function importedChoices(text, catalog) {
    try {
      if (typeof text !== "string" || new TextEncoder().encode(text).length > IMPORT_LIMIT) throw new Error();
      const data = parseImportJson(text);
      if (!isPlainObject(data) || Object.keys(data).length !== 2 || !CHOICES_SCHEMAS.includes(data.schema) || !own(data, "choices")) throw new Error();
      return validateChoices(data.choices, catalog);
    } catch (error) {
      throw new Error(IMPORT_ERROR);
    }
  }

  /* Optional read-aloud for AI replies on the local setup page. Uses only voices the browser marks as on-device
     (localService === true), never a remote voice and never the microphone. Speaks only when asked. */
  function createReader(synth, Utterance) {
    const usable = Boolean(synth) && typeof Utterance === "function" && ["speak", "cancel", "getVoices"].every((name) => typeof synth[name] === "function");
    function localVoice() {
      if (!usable) return null;
      let voices;
      try { voices = Array.from(synth.getVoices() || []); } catch (error) { return null; }
      const local = voices.filter((voice) => voice !== null && typeof voice === "object" && voice.localService === true);
      const english = (voice) => typeof voice.lang === "string" && /^en\b/i.test(voice.lang);
      return local.find((voice) => voice.default === true && english(voice)) || local.find(english) || local[0] || null;
    }
    function cancel() {
      if (!usable) return;
      try { synth.cancel(); } catch (error) { /* nothing is speaking */ }
    }
    function speak(text, done) {
      cancel();
      const voice = localVoice();
      if (!voice || typeof text !== "string" || !text.trim()) return false;
      const utterance = new Utterance(text.slice(0, 4000));
      utterance.voice = voice;
      if (typeof voice.lang === "string") utterance.lang = voice.lang;
      const finish = () => { if (done) done(); };
      utterance.onend = finish;
      utterance.onerror = finish;
      synth.speak(utterance);
      return true;
    }
    return {usable, hasVoice: () => Boolean(localVoice()), speak, cancel};
  }

  const api = {validateAnswers, buildPlan, defaultAnswers, readProviderCatalog, importedAnswers, isMobilePlanner, money,
    validateChoices, checkCatalog, recommend, choicesFromQuery, choicesQuery, isLoopbackPage, isSafeLaunchUrl, readQuickstartState, readBudget, budgetLabel, dollars,
    importedChoices, parseImportJson, createReader, IMPORT_LIMIT, IMPORT_ERROR, CHOICE_ERRORS, CHOICE_KEYS, OPTION_KEYS, QUICKSTART_SCHEMA, CHOICES_SCHEMA, CHOICES_SCHEMAS};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", () => boot(api));
})(typeof globalThis !== "undefined" ? globalThis : this);

/* Fixed, input-free messages. Server error text is never shown. */
const MESSAGES = {
  catalog: "Setup could not load. Refresh the page to try again.",
  local: "Jarvis could not reach its setup service on this computer. Close this page and open the Jarvis start file again.",
  query: "That link had choices this page does not recognize, so the questions start from the beginning.",
  save: "Your answers were not saved on this computer. Choose See my plan to try again.",
  saveBusy: "Wait for the install to finish before changing your answers.",
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
  noVoice: "No voice installed on this computer was found, so the reply stays as text.",
  stopped: "Stopped reading aloud.",
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
  savedJson: "Choices saved. The file holds your six answers only, nothing secret.",
  meterNote: "Jarvis reserves $0.01 per paid attempt and stops when this installation's allowance is used. Actual provider charges may differ. Use a provider hard spend limit too.",
  meterFree: "NVIDIA trial access and local Ollama chat do not draw on this allowance. NVIDIA trial use still depends on NVIDIA's own limits and eligibility.",
  budgetUnavailable: "Paid AI paused: local allowance information is unavailable.",
  budgetUnavailableNote: "Offline features, NVIDIA trial access and local Ollama chat still work. Restart Jarvis; if this stays, reinstall the latest version.",
  budgetPaused: "Paid AI paused: this installation's local allowance is used up, past its review date or unavailable. Offline features and free providers still work.",
  meterZero: "At $0 Jarvis sends no paid OpenAI or Anthropic requests.",
  importBad: "Choose a Jarvis choices file saved from this setup page (under 2 KB).",
  importSave: "The saved choices were not stored on this computer. Try again.",
  imported: "Saved choices loaded. Nothing was switched on.",
};

const STEPS = ["role", "budget", "existing", "dictation", "reply", "automation"];
const SUGGESTED = ["dictation", "reply", "automation"];
const PARTS = {desk: "Desk", ai: "AI chat", input: "Talk or type", reply: "Replies", automation: "Automation"};
const STATUS_LABELS = {builtin: "In Jarvis", external: "Outside Jarvis", manual: "Manual setup", later: "Not built in", off: "Off"};

function boot(api) {
  const $ = (id) => document.getElementById(id);
  const meta = document.querySelector('meta[name="jarvis-mode"]');
  const forced = meta ? meta.content : "auto";
  const RESULT = STEPS.length;
  const state = {catalog: null, choices: null, step: 0, token: "", local: false, server: null, phone: false, busy: false, polls: 0,
    touched: new Set(), reader: null, speaking: false};
  const money = (value) => "$" + value.toFixed(2);
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const control = (index) => (STEPS[index] === "budget" ? $("budget") : $(STEPS[index]));

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
  function stepError(message) {
    $("step-error").textContent = message;
    $("step-error").hidden = !message;
    STEPS.concat(["budget-exact"]).forEach((id) => $(id).removeAttribute("aria-invalid"));
    if (message && state.step < RESULT) (STEPS[state.step] === "budget" ? $("budget-exact") : $(STEPS[state.step])).setAttribute("aria-invalid", "true");
  }
  function notice(message) {
    $("notice").textContent = message;
    $("notice").hidden = !message;
  }

  async function localJson(path, body) {
    if (!api.isLoopbackPage(location, forced) || (!state.local && path !== "/api/session" && path !== "/api/quickstart")) throw new Error("not local");
    const options = body === undefined ? {cache: "no-store"} : {method: "POST", cache: "no-store", headers: {"Content-Type": "application/json", "X-Jarvis-Token": state.token}, body: JSON.stringify(body)};
    const response = await fetch(path, options);
    if (!response.ok) {
      /* Only one error code is understood; any other body, including its text, is ignored. */
      const failure = new Error("request failed");
      try {
        const answer = await response.json();
        failure.budget = response.status === 400 && answer !== null && typeof answer === "object" && answer.code === "budget_unavailable";
      } catch (error) { failure.budget = false; }
      throw failure;
    }
    return response.json();
  }
  const closed = () => document.body.classList.contains("closed");
  const installing = () => Boolean(state.local && state.server && state.server.install === "running");

  /* One local request at a time: the question controls and the triggering button stay disabled until it settles. */
  function lockControls() {
    const locked = closed() || state.busy;
    STEPS.concat(["budget-exact", "next", "back"]).forEach((id) => { $(id).disabled = locked; });
    $("change").disabled = locked || installing();
  }
  async function act(button, work) {
    if (state.busy) return;
    state.busy = true;
    if (button) button.disabled = true;
    lockControls();
    try { await work(); } finally {
      state.busy = false;
      if (button && !closed()) button.disabled = false;
      if (state.step === RESULT && state.choices) render();
      lockControls();
    }
  }

  function fillSelect(id, items) {
    $(id).replaceChildren(...items.map((item) => el("option", {value: item.id, text: item.label})));
  }
  function entry(key, id) { return state.catalog[key === "role" ? "roles" : key].find((item) => item.id === id); }
  function provider(plan) { return state.catalog.providers[plan.provider]; }

  /* ---------- questions ---------- */
  function showHint(key) {
    const chosen = entry(key, $(key).value);
    $(key + "-hint").textContent = chosen ? chosen.hint : "";
  }
  function showBudget(value) {
    const limits = state.catalog.budget;
    const slider = Math.min(value, limits.slider_max);
    $("budget").value = String(slider);
    $("budget").style.setProperty("--fill", (slider / limits.slider_max * 100) + "%");
    $("budget").setAttribute("aria-valuetext", api.budgetLabel(slider));
    $("budget-value").textContent = value === 0 ? "$0, free only" : api.dollars(value) + " a month";
  }
  function fillStep(key) {
    if (key === "budget") {
      $("budget-exact").value = String(state.choices.budget);
      showBudget(state.choices.budget);
      return;
    }
    $(key).value = state.choices[key];
    showHint(key);
  }
  /* Reads the current question into state.choices, or throws the fixed validation message. */
  function readStep(key) {
    const value = key === "budget" ? $("budget-exact").value.trim() : $(key).value;
    const next = api.validateChoices(Object.assign({}, state.choices, {[key]: value}), state.catalog);
    if (key === "role") {
      /* Purpose suggests supported typing and reply choices the person has not picked yet. */
      const suggest = entry("role", next.role).suggest;
      SUGGESTED.forEach((name) => { if (!state.touched.has(name)) next[name] = suggest[name]; });
    }
    state.choices = next;
  }

  function show(index, how) {
    state.step = index;
    const result = index === RESULT;
    $("wizard").hidden = result;
    $("result").hidden = !result;
    document.querySelectorAll("#wizard .step").forEach((section) => { section.hidden = section.dataset.step !== STEPS[index]; });
    stepError("");
    if (result) render();
    else {
      fillStep(STEPS[index]);
      $("progress").textContent = "Question " + (index + 1) + " of " + STEPS.length;
      $("progress-fill").style.setProperty("--done", ((index + 1) / STEPS.length * 100) + "%");
      $("back").hidden = index === 0;
      $("next").textContent = index === RESULT - 1 ? "See my plan" : "Next";
    }
    document.title = result ? "Your Jarvis plan" : "Set up Jarvis: question " + (index + 1) + " of " + STEPS.length;
    const hash = "#" + (result ? "plan" : STEPS[index]);
    if (how.push) history.pushState({jarvisStep: index}, "", hash);
    else if (how.replace) history.replaceState({jarvisStep: index}, "", hash);
    lockControls();
    const focusTitle = () => {
      if (state.step !== index) return;
      (result ? $("plan-title") : $(STEPS[index] + "-title")).focus({preventScroll: true});
      window.scrollTo(0, 0);
    };
    if (how.focus) {
      if (how.pop) window.setTimeout(focusTitle, 0);
      else focusTitle();
    }
  }

  async function next() {
    if (state.busy || closed()) return;
    try { readStep(STEPS[state.step]); } catch (error) {
      stepError(error.message);
      (STEPS[state.step] === "budget" ? $("budget-exact") : $(STEPS[state.step])).focus();
      return;
    }
    if (state.step < RESULT - 1) { show(state.step + 1, {push: true, focus: true}); return; }
    await finish({push: true});
  }

  /* Hosted: show the plan. Local: the service saves the answers first; on failure the last question stays open to retry. */
  async function finish(how) {
    const same = state.local && state.server.choices && api.CHOICE_KEYS.every((key) => state.server.choices[key] === state.choices[key]);
    if (state.local && !same) {
      if (installing()) {
        if (how.pop) history.replaceState({jarvisStep: state.step}, "", "#" + STEPS[state.step]);
        stepError(MESSAGES.saveBusy);
        return;
      }
      let saved = false;
      await act(null, async () => { saved = await commit(state.choices); });
      if (!saved) {
        if (how.pop) history.replaceState({jarvisStep: state.step}, "", "#" + STEPS[state.step]);
        stepError(MESSAGES.save);
        $(STEPS[state.step] + "-title").focus();
        return;
      }
    }
    show(RESULT, {push: how.push, focus: true});
  }

  async function commit(choices) {
    try {
      const server = applyServer(await localJson("/api/quickstart/save", {choices}));
      state.choices = server.choices || choices;
      return true;
    } catch (error) {
      return false;
    }
  }

  /* ---------- plan ---------- */
  function render() {
    const catalog = state.catalog;
    const plan = api.recommend(state.choices, catalog);
    $("plan-title").textContent = plan.title;
    $("plan-summary").textContent = plan.summary;
    $("plan-stack").replaceChildren(...plan.stack.map((row) => {
      const name = [el("span", {text: row.name})];
      if (row.url) name.push(el("a", {href: row.url, target: "_blank", rel: "noopener noreferrer", className: "guide", text: "Guide"}));
      return el("li", {className: "row-" + row.status}, [el("span", {className: "part", text: PARTS[row.part]}), el("span", {className: "name"}, name),
        el("span", {className: "tag", text: STATUS_LABELS[row.status]})]);
    }));
    $("plan-steps").replaceChildren(...plan.steps.map((text, index) => {
      const done = state.local && index < 2;
      return el("li", {className: done ? "done" : ""}, done ? [el("span", {text}), el("span", {className: "done-tag", text: "Done"})] : [el("span", {text})]);
    }));
    $("plan-tradeoffs").replaceChildren(...plan.tradeoffs.map((text) => el("li", {text})));
    $("budget-line").textContent = "Monthly budget for paid AI: " + api.budgetLabel(plan.choices.budget) + ".";
    $("provider-link").href = plan.setup_url;
    $("provider-link").textContent = provider(plan).short + " setup page";
    $("handoff-link").textContent = handoffLink(plan.choices);
    renderAllowance(plan);
    if (state.local) renderLocal(plan);
  }

  function renderAllowance(plan) {
    const info = provider(plan);
    $("limit-help").hidden = !info.limit_url;
    if (info.limit_url) {
      $("limit-link").href = info.limit_url;
      $("limit-link").textContent = "Set a hard spend limit with " + info.short;
    }
    const budget = state.local && state.server ? state.server.budget : null;
    $("budget-meter").hidden = !budget;
    $("budget-paused").hidden = !budget || budget.available;
    $("budget-paused").textContent = MESSAGES.budgetUnavailable;
    if (!budget) return;
    $("meter-bar").hidden = !budget.available;
    $("meter-month").hidden = !budget.available;
    $("budget-meter").classList.toggle("paused", !budget.available);
    if (!budget.available) {
      $("meter-text").textContent = MESSAGES.budgetUnavailable;
      $("meter-note").textContent = MESSAGES.budgetUnavailableNote;
      return;
    }
    $("meter-text").textContent = money(budget.reserved) + " reserved of " + money(budget.limit) + ", " + money(budget.remaining) + " left, "
      + budget.paidRequests + " paid attempt" + (budget.paidRequests === 1 ? "" : "s");
    $("meter-month").textContent = budget.month;
    $("meter-bar").max = Math.max(budget.limit, 0.01);
    $("meter-bar").value = Math.min(budget.reserved, Math.max(budget.limit, 0.01));
    const extra = info.kind === "local" || plan.provider === "nvidia" ? MESSAGES.meterFree : budget.limit === 0 ? MESSAGES.meterZero : "";
    const review = budget.validUntil ? " Pricing assumptions " + (budget.pricingChecked ? "checked " + budget.pricingChecked + ", " : "") + "due for review " + budget.validUntil + "; update Jarvis after that date." : "";
    $("meter-note").textContent = MESSAGES.meterNote + (extra ? " " + extra : "") + review;
  }

  function handoffLink(choices) {
    const url = new URL(location.href);
    url.search = api.choicesQuery(choices);
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
      $("key-label").textContent = "Paste your " + info.short + " API key (optional)";
      $("key-form").hidden = saved;
      $("key-saved").hidden = !saved;
      $("key-saved-text").textContent = info.short + " key saved on this computer.";
    }
    $("ai-box").hidden = !(saved || info.kind === "local");
    const speak = plan.choices.reply === "read_aloud";
    $("speak-row").hidden = !speak;
    if (!speak && $("speak").checked) { $("speak").checked = false; stopSpeaking(); }
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

  /* ---------- read aloud: local page only, explicit opt-in, cancelled on Stop, Escape, leaving or hiding the page ---------- */
  function stopSpeaking() {
    if (state.reader) state.reader.cancel();
    state.speaking = false;
    $("stop-speaking").hidden = true;
  }
  function readAloud(text) {
    if (!state.local || !$("speak").checked || state.choices.reply !== "read_aloud") return;
    if (!state.reader) state.reader = api.createReader(window.speechSynthesis, window.SpeechSynthesisUtterance);
    const started = state.reader.speak(text, () => { state.speaking = false; $("stop-speaking").hidden = true; });
    state.speaking = started;
    $("stop-speaking").hidden = !started;
    if (!started) status(MESSAGES.noVoice);
  }
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && state.speaking) { stopSpeaking(); status(MESSAGES.stopped); } });
  window.addEventListener("pagehide", stopSpeaking);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") stopSpeaking(); });

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
    });
  }

  /* Paid attempts reserve allowance even when they fail, so re-read the service state afterwards. */
  async function refresh() {
    try { applyServer(await localJson("/api/quickstart")); } catch (error) { /* keep the last known state */ }
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
    if (state.step === RESULT) render();
    lockControls();
    pollInstall();
  }

  const actions = {
    async "key-delete"() {
      const plan = api.recommend(state.choices, state.catalog);
      try {
        applyServer(await localJson("/api/quickstart/key/delete", {provider: plan.provider}));
        status(MESSAGES.keyDeleted);
      } catch (error) { status(MESSAGES.keyDelete, true); }
    },
    async test() {
      if (needConsent()) return;
      const plan = api.recommend(state.choices, state.catalog);
      try {
        await localJson("/api/quickstart/test", {provider: plan.provider, consent: true});
        status(MESSAGES.tested);
      } catch (error) { status(error.budget ? MESSAGES.budgetPaused : MESSAGES.test, true); }
      await refresh();
    },
    async chat() {
      if (needConsent()) return;
      const prompt = $("prompt").value.trim();
      if (!prompt) { status(MESSAGES.promptEmpty, true); $("prompt").focus(); return; }
      stopSpeaking();
      const plan = api.recommend(state.choices, state.catalog);
      try {
        const result = await localJson("/api/quickstart/chat", {provider: plan.provider, prompt, consent: true});
        if (typeof result.reply !== "string") throw new Error("bad reply");
        const reply = result.reply.slice(0, 4000);
        $("chat-reply").textContent = reply;
        $("chat-reply").hidden = false;
        status("");
        readAloud(reply);
      } catch (error) {
        $("chat-reply").hidden = true;
        status(error.budget ? MESSAGES.budgetPaused : MESSAGES.chat, true);
      }
      await refresh();
    },
    async install() {
      try {
        applyServer(await localJson("/api/quickstart/install", {consent: true}));
        status("");
      } catch (error) { status(MESSAGES.install, true); }
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
        stopSpeaking();
        document.body.classList.add("closed");
        document.querySelectorAll("button, input, select, textarea").forEach((node) => { node.disabled = true; });
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
    if (!button || !state.choices) return;
    if (button.dataset.action === "stop-speaking") { stopSpeaking(); status(MESSAGES.stopped); return; }
    if (button.dataset.action === "change") {
      if (state.busy || installing()) return;
      stopSpeaking();
      status("");
      show(0, {push: true, focus: true});
      return;
    }
    if (!own(actions, button.dataset.action)) return;
    act(button, () => actions[button.dataset.action](button));
  });
  $("wizard").addEventListener("submit", (event) => { event.preventDefault(); next(); });
  $("back").addEventListener("click", () => { if (state.step > 0 && !state.busy) history.back(); });
  $("wizard").addEventListener("change", (event) => {
    const id = event.target.id;
    if (SUGGESTED.includes(id)) state.touched.add(id);
    if (STEPS.includes(id) && id !== "budget") {
      showHint(id);
      /* Keep answers current so browser Back and Forward never drop a choice made before pressing Next. */
      try { readStep(id); } catch (error) { /* Next shows the fixed message */ }
    }
  });
  /* The slider covers everyday amounts; the exact field takes any whole dollar up to the catalog maximum. */
  $("wizard").addEventListener("input", (event) => {
    if (event.target.id === "budget") {
      $("budget-exact").value = $("budget").value;
      showBudget(Number($("budget").value));
      stepError("");
    } else if (event.target.id === "budget-exact") {
      const value = $("budget-exact").value.trim();
      if (/^(0|[1-9][0-9]{0,4})$/.test(value) && Number(value) <= state.catalog.budget.max) { showBudget(Number(value)); stepError(""); }
    }
    if (event.target.id === "budget" || event.target.id === "budget-exact") {
      try { readStep("budget"); } catch (error) { /* Next shows the fixed message */ }
    }
  });
  $("speak").addEventListener("change", () => { if (!$("speak").checked) stopSpeaking(); });
  window.addEventListener("popstate", (event) => {
    const index = event.state && Number.isInteger(event.state.jarvisStep) ? event.state.jarvisStep : 0;
    if (!state.choices || closed() || index < 0 || index > RESULT) return;
    stopSpeaking();
    if (state.busy) { history.pushState({jarvisStep: state.step}, "", "#" + (state.step === RESULT ? "plan" : STEPS[state.step])); return; }
    if (index === RESULT) {
      /* Browser Forward to the plan re-checks every answer and, locally, saves them first. */
      try { api.validateChoices(state.choices, state.catalog); } catch (error) { show(0, {replace: true, focus: true}); return; }
      finish({pop: true});
      return;
    }
    show(index, {focus: true, pop: true});
  });
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
      if (await commit(next)) {
        SUGGESTED.forEach((key) => state.touched.add(key));
        status(MESSAGES.imported);
      } else status(MESSAGES.importSave, true);
    });
  });

  function links(release) {
    $("feedback").href = release.feedback;
    $("source-repo").href = release.source_repo;
    $("source-zip").href = release.source_zip;
    document.querySelectorAll(".agent-guide").forEach((link) => { link.href = release.agent_guide; });
    const ready = release.status === "available";
    $("download").hidden = !ready;
    $("download-pending").hidden = ready;
    if (ready) $("download").href = release.windows_zip;
    $("download-note").textContent = ready ? "Unzip it, then open Start-Jarvis-Windows.cmd. Setup continues on your computer." : "The first Windows package is still being prepared. Until then, the source ZIP below runs with Python 3.11 or newer through Start-Jarvis.cmd.";
  }

  async function start() {
    try {
      const response = await fetch("quickstart-catalog.json", {cache: "no-store"});
      if (!response.ok) throw new Error("catalog unavailable");
      state.catalog = api.checkCatalog(api.parseImportJson(await response.text()));
      fillSelect("role", state.catalog.roles);
      ["existing", "dictation", "reply", "automation"].forEach((key) => fillSelect(key, state.catalog[key]));
      $("budget").max = String(state.catalog.budget.slider_max);
      $("budget-exact").max = String(state.catalog.budget.max);
      state.choices = api.validateChoices(state.catalog.defaults, state.catalog);
      links(state.catalog.release);
    } catch (error) {
      $("mode-badge").textContent = "UNAVAILABLE";
      notice(MESSAGES.catalog);
      return;
    }
    let ready = false;
    if (api.isLoopbackPage(location, forced)) {
      try {
        const session = await localJson("/api/session");
        if (!session || typeof session.token !== "string" || !session.token) throw new Error("no session");
        state.token = session.token;
        const server = api.readQuickstartState(await localJson("/api/quickstart"), state.catalog);
        if (!server) throw new Error("not local");
        state.local = true;
        state.server = server;
        if (server.choices) { state.choices = server.choices; ready = true; }
      } catch (error) {
        state.local = false;
        state.token = "";
        notice(MESSAGES.local);
      }
    } else {
      try {
        const linked = api.choicesFromQuery(location.search, state.catalog);
        if (linked) { state.choices = linked; ready = true; }
      } catch (error) {
        notice(MESSAGES.query);
      }
    }
    if (ready) SUGGESTED.forEach((key) => state.touched.add(key));
    state.phone = !state.local && api.isMobilePlanner(navigator);
    $("mode-badge").textContent = state.local ? "ON THIS COMPUTER" : state.phone ? "PHONE PLANNER" : "PLANNER";
    $("local").hidden = !state.local;
    $("get").hidden = state.local || state.phone;
    $("phone").hidden = !state.phone;
    document.querySelectorAll(".local-only").forEach((node) => { node.hidden = !state.local; });
    $("privacy-link").hidden = state.local;
    show(ready ? RESULT : 0, {replace: true});
    if (state.local && state.server.install === "running") pollInstall();
  }
  start();
}
