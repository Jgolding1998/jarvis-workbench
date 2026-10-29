"""Deterministic setup planner. No network, no provider calls, no secrets.

The same rules are implemented in web/setup.js for the static hosted wizard.
tests/test_planner.py checks that both produce identical plans.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import platform
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "web" / "setup-catalog.json"
CONFIG_NAME = "setup-config.json"
ANSWERS_SCHEMA = "jarvis-setup-answers/1"
PLAN_SCHEMA = "jarvis-setup-plan/1"
CONFIG_SCHEMA = "jarvis-config/1"
VOICE_MODEL = "models/voice-en"
HAND_MODEL = "models/hand_landmarker.task"
from .hand_devices import MODEL_SHA256
ANSWER_KEYS = {"schema", "install", "preset", "features", "budget_usd", "ai_tool", "voice", "device", "preferences"}
DEVICE_KEYS = {"os", "camera", "microphone", "ram"}
PREFERENCE_KEYS = {"camera_index", "hand_move_cursor"}
CAMERA_FEATURES = ("presence_guard", "privacy_lock", "boss_alert", "hand_control")


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def parse_json(text):
    """Parse untrusted JSON, rejecting duplicate keys and non-finite numbers."""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8-sig")
    depth, quoted, escaped = 0, False, False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("JSON is too deeply nested")
        elif character in "]}":
            depth -= 1
    def constant(name):
        raise ValueError("Non-finite number")
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=constant)
    except (RecursionError, OverflowError):
        raise ValueError("JSON is too deeply nested or contains an unsupported number") from None


def load_catalog(path=CATALOG_PATH):
    catalog = parse_json(Path(path).read_text(encoding="utf-8"))
    if catalog.get("schema") != "jarvis-setup-catalog/1":
        raise ValueError("Unsupported catalog")
    return catalog


def _ids(items):
    return [item["id"] for item in items]


def _exact(value, keys, name):
    if type(value) is not dict or set(value) != keys:
        raise ValueError(name + " has missing or unexpected fields")


def _choice(value, allowed, name):
    if type(value) is not str or value not in allowed:
        raise ValueError("Unknown " + name)
    return value


def validate_answers(data, catalog):
    """Return normalized answers or raise ValueError. Unknown keys are rejected."""
    _exact(data, ANSWER_KEYS, "Answers")
    if data["schema"] != ANSWERS_SCHEMA:
        raise ValueError("Unsupported answers schema")
    if type(data["install"]) is not bool:
        raise ValueError("Install must be true or false")
    preset = _choice(data["preset"], _ids(catalog["presets"]), "preset")
    selectable = [item["id"] for item in catalog["features"] if item.get("selectable")]
    features = data["features"]
    if type(features) is not list or len(features) > len(selectable) or len(set(map(str, features))) != len(features):
        raise ValueError("Features must be a short list without repeats")
    for feature in features:
        _choice(feature, selectable, "feature")
    budget = data["budget_usd"]
    low, high = catalog["budget"]["min"], catalog["budget"]["max"]
    if type(budget) not in (int, float) or budget < low or budget > high or not math.isfinite(budget):
        raise ValueError("Budget must be a number from 0 to 500")
    ai_tool = _choice(data["ai_tool"], _ids(catalog["ai_tools"]), "AI tool")
    voice = _choice(data["voice"], _ids(catalog["voice_options"]), "voice option")
    device = data["device"]
    _exact(device, DEVICE_KEYS, "Device")
    if type(device["camera"]) is not bool or type(device["microphone"]) is not bool:
        raise ValueError("Camera and microphone must be true or false")
    preferences = data["preferences"]
    _exact(preferences, PREFERENCE_KEYS, "Preferences")
    index = preferences["camera_index"]
    if type(index) is not int or not 0 <= index <= 9:
        raise ValueError("Camera index must be 0 to 9")
    if type(preferences["hand_move_cursor"]) is not bool:
        raise ValueError("Cursor preference must be true or false")
    order = {name: position for position, name in enumerate(selectable)}
    return {
        "schema": ANSWERS_SCHEMA, "install": data["install"], "preset": preset,
        "features": sorted(features, key=order.__getitem__),
        "budget_usd": _cents(budget),
        "ai_tool": ai_tool, "voice": voice,
        "device": {"os": _choice(device["os"], _ids(catalog["os"]), "operating system"), "camera": device["camera"],
                   "microphone": device["microphone"], "ram": _choice(device["ram"], _ids(catalog["ram"]), "memory size")},
        "preferences": {"camera_index": index, "hand_move_cursor": preferences["hand_move_cursor"]},
    }


def default_answers(catalog):
    return {"schema": ANSWERS_SCHEMA, "install": True, "preset": "desk_privacy",
            "features": list(next(p for p in catalog["presets"] if p["id"] == "desk_privacy")["features"]),
            "budget_usd": 0, "ai_tool": "none", "voice": "no_microphone",
            "device": {"os": "windows", "camera": True, "microphone": False, "ram": "unknown"},
            "preferences": {"camera_index": 0, "hand_move_cursor": False}}


def _cents(value):
    value = math.floor(value * 100 + 0.5) / 100
    return int(value) if value == int(value) else value


def _money(value):
    return "$" + (str(int(value)) if value == int(value) else "%.2f" % value)


def _blockers(feature, answers):
    needs, device, reasons = feature.get("requires", {}), answers["device"], []
    if "os" in needs and device["os"] not in needs["os"]:
        reasons.append("Needs Windows")
    if needs.get("camera") and not device["camera"]:
        reasons.append("Needs a camera")
    if needs.get("microphone") and not device["microphone"]:
        reasons.append("Needs a microphone")
    if "voice" in needs and answers["voice"] not in needs["voice"]:
        reasons.append("Choose offline voice to enable")
    return reasons


def build_plan(answers, catalog):
    """Build a plan from validated answers. Pure and deterministic."""
    if not answers["install"]:
        return {"schema": PLAN_SCHEMA, "install": False, "answers": answers, "features": [], "tasks": [],
                "launch_command": None, "cost": None, "notes": ["Nothing was installed or changed. You can close this page."]}
    features = {item["id"]: item for item in catalog["features"]}
    wanted = set(answers["features"])
    for item in catalog["features"]:
        if item.get("always") or (item["id"] == "offline_voice" and answers["voice"] == "offline_vosk"):
            wanted.add(item["id"])
    for name in list(wanted):
        wanted.update(features[name].get("requires", {}).get("depends", []))
    included, rows = set(), []
    for item in catalog["features"]:
        name = item["id"]
        reasons = _blockers(item, answers)
        for dependency in item.get("requires", {}).get("depends", []):
            if _blockers(features[dependency], answers):
                reasons.append("Needs " + features[dependency]["name"])
        notes = []
        if name not in wanted:
            status = "not_selected"
        elif reasons:
            status = "unavailable"
        else:
            status = "included"
            included.add(name)
            if answers["device"]["ram"] in item.get("ram_caution", []):
                notes.append("May run slowly with less than 4 GB of memory. Try the simulator first.")
            platform_note = item.get("platform_note")
            if platform_note and answers["device"]["os"] not in platform_note["os"]:
                notes.append(platform_note["text"])
            if name not in answers["features"] and name not in ("dashboard", "simulator", "offline_voice"):
                notes.append("Added because another selected feature needs it.")
        rows.append({"id": name, "name": item["name"], "maturity": item["maturity"], "status": status,
                     "reasons": reasons if status == "unavailable" else [], "notes": notes})
    optional_tools = bool(included.intersection(CAMERA_FEATURES) or "offline_voice" in included)
    interpreter = (r".\.venv\Scripts\python.exe" if answers["device"]["os"] == "windows" else "./.venv/bin/python") if optional_tools else "python"
    command = [interpreter, "-m", "jarvis.server"]
    if any(name in included for name in ("presence_guard", "privacy_lock", "boss_alert")):
        command.append("--camera")
    if any(name in included for name in CAMERA_FEATURES):
        if answers["preferences"]["camera_index"]:
            command += ["--camera-index", str(answers["preferences"]["camera_index"])]
        if "privacy_lock" in included:
            command.append("--allow-lock")
        if "boss_alert" in included:
            command.append("--allow-spreadsheet")
        if "hand_control" in included:
            command += ["--allow-hand-control", "--hand-model", HAND_MODEL]
    else:
        command.append("--demo")
    if "offline_voice" in included:
        command += ["--voice-model", VOICE_MODEL]
    launch = " ".join(command)
    tasks = []
    for task in catalog["tasks"]:
        if "when_features" in task and not any(name in included for name in task["when_features"]):
            continue
        tasks.append({"id": task["id"], "kind": task["kind"], "title": task["title"], "detail": task["detail"],
                      "url": catalog["release"]["download_url"] if task.get("url") == "release" else task.get("url"),
                      "command": launch if task.get("command") == "launch" else task.get("command"),
                      "check": list(task.get("check", []))})
    if "hand_control" in included and answers["preferences"]["hand_move_cursor"]:
        tasks.append({"id": "cursor_permission", "kind": "manual", "title": "Enable real cursor control after practice",
                      "detail": "On your Windows computer, stop hand control, select Move the real cursor, apply hand settings, then start watch me again. Saving this plan never enables movement.",
                      "url": None, "command": None, "check": []})
    budget = answers["budget_usd"]
    tool = next(item for item in catalog["ai_tools"] if item["id"] == answers["ai_tool"])
    voice = next(item for item in catalog["voice_options"] if item["id"] == answers["voice"])
    cost = ["Jarvis costs $0 per month. Desktop features run on the computer where you install Jarvis.",
            "Your ceiling is " + _money(budget) + " per month. It is a limit for planning, not a target and not a price guarantee. No purchase is recommended."]
    if tool["kind"] == "api":
        cost.append("API usage is billed per use by the provider. With a $0 ceiling, leave it out; Jarvis does not need it." if budget == 0 else
                    "If you use this API on its own, set a spending limit in the provider console at or below " + _money(budget) + ". Jarvis cannot see or cap that spend.")
    elif tool["kind"] == "subscription":
        cost.append("Your subscription is billed by its provider and is not needed by Jarvis. Some plans or programs include credits or other benefits; check the official page for your current eligibility.")
    elif tool["kind"] == "local":
        cost.append("Ollama is free software. It uses your own hardware and electricity.")
    if voice["id"] == "elevenlabs_existing":
        cost.append("ElevenLabs is billed by its provider. Jarvis does not use it, so it adds nothing to this plan.")
    guidance = [{"topic": "ai_tool", "name": tool["name"], "kind": tool["kind"], "text": tool["guidance"], "url": tool["url"],
                 "connector": False},
                {"topic": "voice", "name": voice["name"], "kind": voice["kind"], "text": voice["guidance"], "url": voice["url"],
                 "connector": voice["kind"] == "implemented"}]
    if answers["device"]["ram"] in tool.get("ram_caution", []):
        guidance[0]["text"] += " Larger local models need a lot of memory; compare the model size with your RAM before downloading."
    return {"schema": PLAN_SCHEMA, "install": True, "answers": answers, "features": rows, "tasks": tasks,
            "launch_command": launch, "guidance": guidance,
            "cost": {"jarvis_usd_per_month": 0, "ceiling_usd_per_month": budget, "notes": cost},
            "notes": ["Importing or saving a plan never arms the camera, microphone, mouse, or Windows actions."]}


def diagnostics(root=ROOT, finder=importlib.util.find_spec):
    """Report local readiness without importing optional packages or touching devices."""
    packages = {}
    for name in ("cv2", "vosk", "sounddevice", "mediapipe"):
        try:
            packages[name] = finder(name) is not None
        except (ImportError, ValueError):
            packages[name] = False
    hand_model = Path(root) / HAND_MODEL
    try:
        hand_ready = hand_model.is_file() and hand_model.stat().st_size <= 12 * 1024 * 1024 and hashlib.sha256(hand_model.read_bytes()).hexdigest() == MODEL_SHA256
    except OSError:
        hand_ready = False
    model = Path(root) / VOICE_MODEL
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else "other")
    return {"python": platform.python_version(), "python_ok": sys.version_info >= (3, 11), "os": system,
            "packages": packages, "hand_model": hand_ready, "voice_model": (model / "conf" / "model.conf").is_file() and (model / "am").is_dir(),
            "config_saved": (Path(root) / "runtime" / CONFIG_NAME).is_file()}


def save_config(answers, runtime_dir):
    """Atomically write the minimal non-secret configuration. Returns the path."""
    runtime_dir = Path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    target = runtime_dir / CONFIG_NAME
    body = json.dumps({"schema": CONFIG_SCHEMA, "answers": answers}, indent=2, sort_keys=True).encode()
    handle, temporary = tempfile.mkstemp(prefix=".setup-", suffix=".tmp", dir=runtime_dir)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def load_config(runtime_dir, catalog):
    """Return saved answers, or None when absent or invalid. Never arms anything."""
    path = Path(runtime_dir) / CONFIG_NAME
    try:
        if path.stat().st_size > 16384:
            return None
        data = parse_json(path.read_text(encoding="utf-8"))
        _exact(data, {"schema", "answers"}, "Config")
        if data["schema"] != CONFIG_SCHEMA:
            return None
        return validate_answers(data["answers"], catalog)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return None
