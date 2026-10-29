"""Three-choice setup recommendation.

Reads web/quickstart-catalog.json, the single source shared with web/setup.js.
Pure and deterministic: no network calls, no account creation, no secrets.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "web" / "quickstart-catalog.json"
CATALOG_SCHEMA = "jarvis-quickstart-catalog/1"
CHOICE_KEYS = ("role", "budget", "existing")
FEATURES = ("camera", "hand")
PROVIDER_KINDS = ("cloud", "local")
RELEASE_STATES = ("pending", "available")
REPRESENTATIVE_BUDGETS = (0, 1, 10, 25, 50, 100)

ERROR_SHAPE = "Choose a role, a monthly budget and an existing tool."
ERROR_ROLE = "Choose a role from the list."
ERROR_BUDGET = "Choose a whole-dollar monthly budget from 0 to 100."
ERROR_EXISTING = "Choose an existing tool from the list."

_cache: dict | None = None


def load_catalog(path: Path | None = None) -> dict:
    """Return a validated copy of the catalog. The default file is read once."""
    global _cache
    if path is None and _cache is not None:
        return copy.deepcopy(_cache)
    data = json.loads(Path(path or CATALOG_PATH).read_text(encoding="utf-8"))
    validate_catalog(data)
    if path is None:
        _cache = data
    return copy.deepcopy(data)


def _https(value) -> bool:
    return isinstance(value, str) and value.startswith("https://") and not any(c.isspace() or c in "\"'<>\\" for c in value)


def _text(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 600 and chr(0x2014) not in value


def validate_catalog(data) -> None:
    """Raise ValueError if the catalog is malformed or internally inconsistent."""
    def need(condition, message):
        if not condition:
            raise ValueError("Quickstart catalog: " + message)

    need(isinstance(data, dict) and data.get("schema") == CATALOG_SCHEMA, "unsupported schema")
    roles, budget, existing = data.get("roles"), data.get("budget"), data.get("existing")
    providers, features, notes = data.get("providers"), data.get("features"), data.get("notes")
    need(all(isinstance(x, list) and x for x in (roles, existing)), "choice lists missing")
    need(all(isinstance(x, dict) for x in (providers, features, notes)), "tables missing")
    for items in (roles, existing):
        ids = [item.get("id") if isinstance(item, dict) else None for item in items]
        need(len(set(map(repr, ids))) == len(ids), "duplicate ids")
        need(all(isinstance(item, dict) and _text(item.get("label")) for item in items), "labels missing")
    need(isinstance(budget, dict) and sorted(budget) == ["max", "min", "step"], "budget range missing")
    need(budget.get("min") == 0 and budget.get("step") == 1 and _whole(budget.get("max")) and 0 < budget["max"] <= 1000, "budget range must be whole dollars from 0")
    for role in roles:
        need(isinstance(role["id"], str) and _text(role.get("title")) and _text(role.get("summary")), "role text missing")
        need(isinstance(role.get("features"), list) and role["features"] and all(f in features and f in FEATURES for f in role["features"]), "role features unknown")
    for item in existing:
        need(isinstance(item["id"], str), "existing ids must be text")
        need(item.get("zero") in providers and item.get("paid") in providers, "existing provider unknown")
        for key in ("zero_notes", "paid_notes"):
            need(isinstance(item.get(key), list) and all(n in notes for n in item[key]), "existing notes unknown")
    for provider in providers.values():
        need(isinstance(provider, dict) and provider.get("kind") in PROVIDER_KINDS, "provider kind unknown")
        need(_https(provider.get("setup_url")) and all(_text(provider.get(k)) for k in ("short", "name", "summary", "step")), "provider fields missing")
        need("model" not in provider or _text(provider["model"]), "provider model invalid")
        need("limit_url" not in provider or _https(provider["limit_url"]), "provider limit link invalid")
    for feature in features.values():
        need(isinstance(feature, dict) and all(_text(feature.get(k)) for k in ("name", "note", "step")), "feature text missing")
    need(all(_text(text) for text in notes.values()) and _text(data.get("budget_note")), "note text missing")
    need(isinstance(data.get("steps"), dict) and all(_text(data["steps"].get(k)) for k in ("download", "start", "dashboard", "practice")), "steps missing")
    release = data.get("release")
    need(isinstance(release, dict) and release.get("status") in RELEASE_STATES, "release status unknown")
    need(all(_https(release.get(k)) for k in ("windows_zip", "source_zip", "source_repo", "agent_guide", "feedback")), "release links must be https")
    defaults = data.get("defaults")
    need(isinstance(defaults, dict) and sorted(defaults) == sorted(CHOICE_KEYS), "defaults missing")
    validate_choices(defaults, data)


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _find(items, wanted):
    return next((item for item in items if item["id"] == wanted), None)


def validate_choices(data, catalog: dict | None = None) -> dict:
    """Return canonical choices or raise ValueError with fixed, input-free text."""
    catalog = catalog if catalog is not None else load_catalog()
    if not isinstance(data, dict) or len(data) != len(CHOICE_KEYS) or any(key not in data for key in CHOICE_KEYS):
        raise ValueError(ERROR_SHAPE)
    role, budget, existing = data["role"], data["budget"], data["existing"]
    if not isinstance(role, str) or _find(catalog["roles"], role) is None:
        raise ValueError(ERROR_ROLE)
    budget = _budget(budget, catalog["budget"])
    if not isinstance(existing, str) or _find(catalog["existing"], existing) is None:
        raise ValueError(ERROR_EXISTING)
    return {"role": role, "budget": budget, "existing": existing}


def _budget(value, limits) -> int:
    """Whole dollars within the catalog range. Accepts canonical digit strings from form fields."""
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 4 and (value == "0" or not value.startswith("0")):
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
    paid = choices["budget"] > 0
    provider_id = existing["paid"] if paid else existing["zero"]
    provider = catalog["providers"][provider_id]
    features = list(role["features"])
    tradeoffs = [catalog["notes"][note] for note in existing["paid_notes" if paid else "zero_notes"]]
    tradeoffs += [catalog["features"][name]["note"] for name in features]
    tradeoffs.append(catalog["budget_note"])
    steps = [catalog["steps"]["download"], catalog["steps"]["start"], provider["step"]]
    steps += [catalog["features"][name]["step"] for name in features]
    steps.append(catalog["steps"]["dashboard"])
    if "hand" in features:
        steps.append(catalog["steps"]["practice"])
    plan = {
        "choices": choices,
        "provider": provider_id,
        "features": features,
        "title": role["title"] + " with " + provider["name"],
        "summary": role["summary"] + " " + provider["summary"],
        "tradeoffs": tradeoffs,
        "setup_url": provider["setup_url"],
        "steps": steps,
    }
    if "model" in provider:
        plan["model"] = provider["model"]
    return plan


def all_choices(catalog: dict | None = None, budgets=REPRESENTATIVE_BUDGETS):
    """Every role and existing tool at representative budgets, in catalog order."""
    catalog = catalog if catalog is not None else load_catalog()
    for role in catalog["roles"]:
        for budget in budgets:
            for existing in catalog["existing"]:
                yield {"role": role["id"], "budget": budget, "existing": existing["id"]}
