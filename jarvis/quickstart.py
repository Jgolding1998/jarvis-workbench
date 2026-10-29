"""One-question-per-screen setup recommendation.

Reads web/quickstart-catalog.json, the single source shared with web/setup.js.
Pure and deterministic: no network calls, no account creation, no secrets.

Choices are {role, budget, existing, dictation, reply, automation}. Older saved choices
with only role, budget and existing are migrated by filling the newer fields from the
catalog defaults. Unknown fields are always rejected.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

from .planner import parse_json

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "web" / "quickstart-catalog.json"
CATALOG_SCHEMA = "jarvis-quickstart-catalog/1"
REQUIRED_KEYS = ("role", "budget", "existing")
OPTION_KEYS = ("dictation", "reply", "automation")
CHOICE_KEYS = REQUIRED_KEYS + OPTION_KEYS
FEATURES = ("camera", "hand")
PROVIDER_KINDS = ("cloud", "local")
RELEASE_STATES = ("pending", "available")
STATUSES = ("builtin", "external", "manual", "later", "off")
BUDGET_MAX = 10000
REPRESENTATIVE_BUDGETS = (0, 1, 10, 100, 101, 1000, 10000)

ERROR_SHAPE = "Choose a purpose, a monthly budget and an AI option."
ERROR_ROLE = "Choose a purpose from the list."
ERROR_BUDGET = "Choose a whole-dollar monthly budget from $0 to $10,000."
ERROR_EXISTING = "Choose an AI option from the list."
ERROR_DICTATION = "Choose a typing or voice option from the list."
ERROR_REPLY = "Choose a reply option from the list."
ERROR_AUTOMATION = "Choose an automation option from the list."
OPTION_ERRORS = {"dictation": ERROR_DICTATION, "reply": ERROR_REPLY, "automation": ERROR_AUTOMATION}

TOP_KEYS = ("schema", "defaults", "release", "roles", "budget", "existing", "dictation", "reply", "automation",
            "providers", "features", "notes", "budget_note", "steps")
RELEASE_KEYS = ("windows_zip", "status", "source_zip", "source_repo", "agent_guide", "feedback")
BUDGET_KEYS = ("min", "max", "step", "slider_max", "large_from")
ROLE_KEYS = ("id", "label", "hint", "features", "title", "summary", "suggest")
EXISTING_KEYS = ("id", "label", "hint", "zero", "paid", "zero_notes", "paid_notes")
OPTION_REQUIRED = ("id", "label", "hint", "status", "name")
OPTION_EXTRA = ("step", "note", "url")
PROVIDER_REQUIRED = ("short", "name", "kind", "setup_url", "summary", "step")
PROVIDER_EXTRA = ("model", "limit_url")
STEP_KEYS = ("download", "start", "dashboard", "practice")
_ID = re.compile(r"[a-z][a-z_]{0,23}")

_cache: dict | None = None


def load_catalog(path: Path | None = None) -> dict:
    """Return a validated copy of the catalog. The default file is read once. Duplicate keys are rejected."""
    global _cache
    if path is None and _cache is not None:
        return copy.deepcopy(_cache)
    data = parse_json(Path(path or CATALOG_PATH).read_bytes())
    validate_catalog(data)
    if path is None:
        _cache = data
    return copy.deepcopy(data)


def _https(value) -> bool:
    return isinstance(value, str) and value.startswith("https://") and len(value) <= 300 and not any(c.isspace() or c in "\"'<>\\" for c in value)


def _text(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 600 and chr(0x2014) not in value


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _keys(value, required, extra=()) -> bool:
    return isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(extra)


def validate_catalog(data) -> None:
    """Raise ValueError if the catalog is malformed or internally inconsistent. Mirrors checkCatalog() in web/setup.js."""
    def need(condition, message):
        if not condition:
            raise ValueError("Quickstart catalog: " + message)

    need(isinstance(data, dict) and data.get("schema") == CATALOG_SCHEMA, "unsupported schema")
    need(_keys(data, TOP_KEYS), "unexpected or missing sections")
    providers, features, notes = data["providers"], data["features"], data["notes"]
    need(all(isinstance(x, dict) and x for x in (providers, features, notes)), "tables missing")
    for name in ("roles", "existing") + OPTION_KEYS:
        items = data[name]
        need(isinstance(items, list) and 0 < len(items) <= 12, name + " list missing")
        need(all(isinstance(item, dict) and isinstance(item.get("id"), str) and _ID.fullmatch(item["id"]) for item in items), name + " ids invalid")
        need(len({item["id"] for item in items}) == len(items), "duplicate ids in " + name)
        need(all(_text(item.get("label")) and len(item["label"]) <= 60 and _text(item.get("hint")) for item in items), name + " labels missing")
    budget = data["budget"]
    need(_keys(budget, BUDGET_KEYS) and all(_whole(budget[k]) for k in BUDGET_KEYS), "budget range missing")
    need(budget["min"] == 0 and budget["step"] == 1 and budget["max"] == BUDGET_MAX, "budget must be whole dollars from 0 to 10000")
    need(0 < budget["slider_max"] <= budget["max"] and 0 < budget["large_from"] <= budget["max"], "budget slider or large amount out of range")
    need(sorted(features) == sorted(FEATURES), "features table must list camera and hand")
    for feature in features.values():
        need(_keys(feature, ("name", "note", "step")) and all(_text(feature[k]) for k in ("name", "note", "step")), "feature text missing")
    for role in data["roles"]:
        need(_keys(role, ROLE_KEYS) and _text(role["title"]) and _text(role["summary"]), "role fields invalid")
        need(isinstance(role["features"], list) and role["features"] and len(set(role["features"])) == len(role["features"])
             and all(f in FEATURES for f in role["features"]), "role features unknown")
        suggest = role["suggest"]
        need(_keys(suggest, OPTION_KEYS) and all(_find(data[k], suggest[k]) is not None for k in OPTION_KEYS), "role suggestion unknown")
    for item in data["existing"]:
        need(_keys(item, EXISTING_KEYS), "existing fields invalid")
        need(item["zero"] in providers and item["paid"] in providers, "existing provider unknown")
        for key in ("zero_notes", "paid_notes"):
            need(isinstance(item[key], list) and all(isinstance(n, str) and n in notes for n in item[key]), "existing notes unknown")
    for name in OPTION_KEYS:
        for item in data[name]:
            need(_keys(item, OPTION_REQUIRED, OPTION_EXTRA) and item["status"] in STATUSES and _text(item["name"]), name + " option invalid")
            need(all(_text(item[k]) for k in ("step", "note") if k in item) and ("url" not in item or _https(item["url"])), name + " option text invalid")
            # Unbuilt tools never get install steps; manual setups always point to a guide.
            need(item["status"] != "later" or ("note" in item and "step" not in item), "later options need a note and no step")
            need(item["status"] != "manual" or ("step" in item and "url" in item), "manual options need a step and a guide")
    for provider in providers.values():
        need(_keys(provider, PROVIDER_REQUIRED, PROVIDER_EXTRA) and provider["kind"] in PROVIDER_KINDS, "provider fields invalid")
        need(_https(provider["setup_url"]) and all(_text(provider[k]) for k in ("short", "name", "summary", "step")), "provider fields missing")
        need("model" not in provider or _text(provider["model"]), "provider model invalid")
        need("limit_url" not in provider or _https(provider["limit_url"]), "provider limit link invalid")
    need(all(isinstance(k, str) and _text(text) for k, text in notes.items()) and "large_budget" in notes, "note text missing")
    need(_text(data["budget_note"]), "budget note missing")
    need(_keys(data["steps"], STEP_KEYS) and all(_text(data["steps"][k]) for k in STEP_KEYS), "steps missing")
    release = data["release"]
    need(_keys(release, RELEASE_KEYS) and release["status"] in RELEASE_STATES, "release status unknown")
    need(all(_https(release[k]) for k in RELEASE_KEYS if k != "status"), "release links must be https")
    defaults = data["defaults"]
    need(_keys(defaults, CHOICE_KEYS), "defaults missing")
    need(validate_choices(defaults, data) == defaults, "defaults are not canonical")


def _find(items, wanted):
    return next((item for item in items if item["id"] == wanted), None)


def validate_choices(data, catalog: dict | None = None) -> dict:
    """Return canonical choices or raise ValueError with fixed, input-free text.

    Older three-field choices gain the catalog defaults for dictation, reply and automation.
    """
    catalog = catalog if catalog is not None else load_catalog()
    if not isinstance(data, dict) or not all(key in data for key in REQUIRED_KEYS) or any(key not in CHOICE_KEYS for key in data):
        raise ValueError(ERROR_SHAPE)
    role, existing = data["role"], data["existing"]
    if not isinstance(role, str) or _find(catalog["roles"], role) is None:
        raise ValueError(ERROR_ROLE)
    budget = _budget(data["budget"], catalog["budget"])
    if not isinstance(existing, str) or _find(catalog["existing"], existing) is None:
        raise ValueError(ERROR_EXISTING)
    result = {"role": role, "budget": budget, "existing": existing}
    for key in OPTION_KEYS:
        value = data[key] if key in data else catalog["defaults"][key]
        if not isinstance(value, str) or _find(catalog[key], value) is None:
            raise ValueError(OPTION_ERRORS[key])
        result[key] = value
    return result


def _budget(value, limits) -> int:
    """Whole dollars within the catalog range. Accepts canonical digit strings from form fields and links."""
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 5 and (value == "0" or not value.startswith("0")):
        value = int(value)
    elif isinstance(value, float) and value.is_integer():
        value = int(value)
    if not _whole(value) or not limits["min"] <= value <= limits["max"]:
        raise ValueError(ERROR_BUDGET)
    return value


def default_choices(catalog: dict | None = None) -> dict:
    catalog = catalog if catalog is not None else load_catalog()
    return dict(catalog["defaults"])


def recommend(choices, catalog: dict | None = None) -> dict:
    """Deterministic plan for validated choices. Mirrors recommend() in web/setup.js."""
    catalog = catalog if catalog is not None else load_catalog()
    choices = validate_choices(choices, catalog)
    role = _find(catalog["roles"], choices["role"])
    existing = _find(catalog["existing"], choices["existing"])
    options = [_find(catalog[key], choices[key]) for key in OPTION_KEYS]
    paid = choices["budget"] > 0
    provider_id = existing["paid"] if paid else existing["zero"]
    provider = catalog["providers"][provider_id]
    features = list(role["features"])
    tradeoffs = [catalog["notes"][note] for note in existing["paid_notes" if paid else "zero_notes"]]
    tradeoffs += [catalog["features"][name]["note"] for name in features]
    tradeoffs += [option["note"] for option in options if "note" in option]
    if choices["budget"] >= catalog["budget"]["large_from"]:
        tradeoffs.append(catalog["notes"]["large_budget"])
    tradeoffs.append(catalog["budget_note"])
    steps = [catalog["steps"]["download"], catalog["steps"]["start"], provider["step"]]
    steps += [catalog["features"][name]["step"] for name in features]
    steps += [option["step"] for option in options if "step" in option]
    steps.append(catalog["steps"]["dashboard"])
    if "hand" in features:
        steps.append(catalog["steps"]["practice"])
    ai_name = provider["short"] + (" (" + provider["model"] + ")" if "model" in provider else "")
    stack = [{"part": "desk", "name": " and ".join(catalog["features"][name]["name"] for name in features), "status": "builtin"},
             {"part": "ai", "name": ai_name, "status": "external"}]
    for part, option in zip(("input", "reply", "automation"), options):
        row = {"part": part, "name": option["name"], "status": option["status"]}
        if "url" in option:
            row["url"] = option["url"]
        stack.append(row)
    plan = {
        "choices": choices,
        "provider": provider_id,
        "features": features,
        "title": role["title"] + " with " + provider["name"],
        "summary": role["summary"] + " " + provider["summary"],
        "stack": stack,
        "steps": steps,
        "tradeoffs": tradeoffs,
        "setup_url": provider["setup_url"],
    }
    if "model" in provider:
        plan["model"] = provider["model"]
    return plan


def all_choices(catalog: dict | None = None, budgets=REPRESENTATIVE_BUDGETS):
    """Every option combination at representative budgets, in catalog order."""
    catalog = catalog if catalog is not None else load_catalog()
    for role in catalog["roles"]:
        for budget in budgets:
            for existing in catalog["existing"]:
                for dictation in catalog["dictation"]:
                    for reply in catalog["reply"]:
                        for automation in catalog["automation"]:
                            yield {"role": role["id"], "budget": budget, "existing": existing["id"],
                                   "dictation": dictation["id"], "reply": reply["id"], "automation": automation["id"]}
