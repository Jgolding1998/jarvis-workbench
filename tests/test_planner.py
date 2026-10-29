import copy
import itertools
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from jarvis import planner

CATALOG = planner.load_catalog()
SETUP_JS = planner.ROOT / "web" / "setup.js"
INTERPRETERS = {"python", r".\.venv\Scripts\python.exe", "./.venv/bin/python"}
ALLOWED_TOKENS = INTERPRETERS | {"-m", "jarvis.server", "--demo", "--camera", "--camera-index", "--allow-lock", "--allow-spreadsheet",
                  "--allow-hand-control", "--hand-model", planner.HAND_MODEL, "--voice-model", planner.VOICE_MODEL} | {str(n) for n in range(1, 10)}


def answers(**changes):
    data = planner.default_answers(CATALOG)
    for key, value in changes.items():
        if key in ("os", "camera", "microphone", "ram"):
            data["device"][key] = value
        elif key in ("camera_index", "hand_move_cursor"):
            data["preferences"][key] = value
        else:
            data[key] = value
    return data


def plan_for(**changes):
    return planner.build_plan(planner.validate_answers(answers(**changes), CATALOG), CATALOG)


def status(plan, feature):
    return next(row for row in plan["features"] if row["id"] == feature)


def combos():
    budgets = [0, 12.345, 500]
    for index, (preset, os_name, camera, mic, voice, tool, budget, ram) in enumerate(itertools.product(
            [p["id"] for p in CATALOG["presets"]], ["windows", "macos", "linux"], [True, False], [True, False],
            [v["id"] for v in CATALOG["voice_options"]], [t["id"] for t in CATALOG["ai_tools"]], budgets, ["lt4", "16plus", "unknown"])):
        if index % 11 and not (preset == "custom" and tool == "none"):
            continue
        features = next(p for p in CATALOG["presets"] if p["id"] == preset)["features"]
        if preset == "custom":
            features = ["hand_control", "boss_alert"]
        yield answers(preset=preset, features=list(features), os=os_name, camera=camera, microphone=mic, voice=voice,
                      ai_tool=tool, budget_usd=budget, ram=ram, camera_index=3 if camera else 0)


class CatalogTests(unittest.TestCase):
    def test_catalog_references_are_consistent(self):
        selectable = {f["id"] for f in CATALOG["features"] if f.get("selectable")}
        feature_ids = {f["id"] for f in CATALOG["features"]}
        for preset in CATALOG["presets"]:
            self.assertTrue(set(preset["features"]) <= selectable, preset["id"])
            self.assertTrue(preset["goal"])
        for task in CATALOG["tasks"]:
            self.assertTrue(set(task.get("when_features", [])) <= feature_ids, task["id"])
            self.assertIn(task["kind"], {"manual", "automatic", "pending"})
        for feature in CATALOG["features"]:
            self.assertTrue(set(feature.get("requires", {}).get("depends", [])) <= feature_ids)
        self.assertEqual([p["id"] for p in CATALOG["presets"]], ["developer", "office", "study", "creator", "desk_privacy", "custom"])
        self.assertEqual([t["id"] for t in CATALOG["ai_tools"]], ["none", "ollama", "openai_api", "anthropic_api", "chatgpt_subscription", "claude_subscription", "other"])
        self.assertEqual([v["id"] for v in CATALOG["voice_options"]], ["offline_vosk", "platform_voice", "elevenlabs_existing", "no_microphone"])

    def test_catalog_links_are_official_https_and_no_prices_are_invented(self):
        text = (planner.ROOT / "web" / "setup-catalog.json").read_text(encoding="utf-8")
        for url in re.findall(r'"url": "([^"]+)"', text):
            self.assertTrue(url == "release" or url.startswith("https://"), url)
        self.assertIsNone(re.search(r"\$\s*[1-9]", text), "catalog must not quote provider prices")
        self.assertNotRegex(text, "[\u2013\u2014]")

    def test_only_implemented_or_labeled_features(self):
        maturity = {f["id"]: f["maturity"] for f in CATALOG["features"]}
        self.assertEqual(maturity["hand_control"], "implemented")
        self.assertTrue(all(value in {"implemented", "preview"} for value in maturity.values()))
        for tool in CATALOG["ai_tools"]:
            if tool["id"] != "none":
                self.assertRegex(tool["guidance"], "no .*connector|does not connect")


class ValidationTests(unittest.TestCase):
    def test_nesting_limit_ignores_brackets_and_escaped_quotes_in_strings(self):
        data = {"text": "[" * 100 + '\\"' + "]" * 100}
        self.assertEqual(planner.parse_json(json.dumps(data).encode("utf-8")), data)
        with self.assertRaisesRegex(ValueError, "nested"):
            planner.parse_json("[" * 33 + "0" + "]" * 33)

    def reject(self, data):
        with self.assertRaises((ValueError, TypeError)):
            planner.validate_answers(data, CATALOG)

    def test_defaults_are_valid_and_zero_cost(self):
        plan = plan_for()
        self.assertEqual(plan["cost"]["jarvis_usd_per_month"], 0)
        self.assertEqual(plan["answers"]["budget_usd"], 0)

    def test_rejects_unknown_and_prototype_keys(self):
        for key in ("__proto__", "constructor", "api_key", "prototype"):
            data = answers()
            data[key] = {"polluted": True}
            self.reject(data)
            nested = answers()
            nested["device"][key] = True
            self.reject(nested)
        self.reject({k: v for k, v in answers().items() if k != "voice"})

    def test_rejects_bad_enums_and_types(self):
        self.reject(answers(preset="admin"))
        self.reject(answers(ai_tool="gpt-5"))
        self.reject(answers(voice="cloud"))
        self.reject(answers(os="android"))
        self.reject(answers(ram="64"))
        self.reject(answers(features=["presence_guard", "presence_guard"]))
        self.reject(answers(features=["dashboard"]))
        self.reject(answers(features="presence_guard"))
        self.reject(answers(camera="yes"))
        self.reject(answers(install="false"))
        self.reject(answers(camera_index=10))
        self.reject(answers(camera_index=True))
        self.reject(answers(hand_move_cursor=1))
        self.reject(answers(schema="jarvis-setup-answers/2"))

    def test_budget_must_be_finite_and_in_range(self):
        for budget in (-1, 500.01, float("inf"), float("nan"), True, "10", None):
            self.reject(answers(budget_usd=budget))
        self.assertEqual(planner.validate_answers(answers(budget_usd=12.345), CATALOG)["budget_usd"], 12.35)
        self.assertEqual(planner.validate_answers(answers(budget_usd=500), CATALOG)["budget_usd"], 500)
        with self.assertRaises(ValueError):
            planner.parse_json('{"budget_usd": NaN}')
        with self.assertRaises(ValueError):
            planner.parse_json('{"budget_usd": Infinity}')

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ValueError):
            planner.parse_json('{"install": true, "install": false}')

    def test_huge_integer_budget_is_a_controlled_validation_error(self):
        for budget in (10 ** 400, -(10 ** 400)):
            with self.subTest(negative=budget < 0), self.assertRaises(ValueError):
                planner.validate_answers(answers(budget_usd=budget), CATALOG)

    def test_deep_json_is_a_controlled_parse_error_within_request_limit(self):
        nested = "[" * 3000 + "0" + "]" * 3000
        self.assertLess(len(nested), 8192)
        with self.assertRaisesRegex(ValueError, "nested|unsupported"):
            planner.parse_json(nested)


class PlanTests(unittest.TestCase):
    def test_no_camera_means_demo_and_honest_reasons(self):
        plan = plan_for(camera=False)
        self.assertEqual(status(plan, "presence_guard")["status"], "unavailable")
        self.assertIn("Needs a camera", status(plan, "presence_guard")["reasons"])
        self.assertEqual(plan["launch_command"], "python -m jarvis.server --demo")
        self.assertNotIn("camera_packages", [t["id"] for t in plan["tasks"]])

    def test_windows_only_actions_blocked_on_macos(self):
        plan = plan_for(os="macos", features=["presence_guard", "privacy_lock", "boss_alert"])
        self.assertEqual(status(plan, "presence_guard")["status"], "included")
        self.assertEqual(status(plan, "privacy_lock")["reasons"], ["Needs Windows"])
        self.assertEqual(plan["launch_command"], "./.venv/bin/python -m jarvis.server --camera")

    def test_guided_voice_is_only_offered_on_the_verified_platform(self):
        plan = plan_for(os="macos", microphone=True, voice="offline_vosk")
        self.assertEqual(status(plan, "offline_voice")["status"], "unavailable")
        self.assertIn("Needs Windows", status(plan, "offline_voice")["reasons"])
        self.assertNotIn("--voice-model", plan["launch_command"])

    def test_budget_is_a_ceiling_not_an_upsell(self):
        for preset in [p["id"] for p in CATALOG["presets"]]:
            features = next(p for p in CATALOG["presets"] if p["id"] == preset)["features"]
            low = plan_for(preset=preset, features=list(features), budget_usd=0, ai_tool="openai_api")
            high = plan_for(preset=preset, features=list(features), budget_usd=500, ai_tool="openai_api")
            self.assertEqual(low["features"], high["features"])
            self.assertEqual(low["launch_command"], high["launch_command"])
            self.assertEqual(low["tasks"], high["tasks"])
            self.assertEqual(high["cost"]["jarvis_usd_per_month"], 0)
            self.assertIn("No purchase is recommended.", " ".join(high["cost"]["notes"]))

    def test_paid_tools_are_guidance_only(self):
        for tool in ("openai_api", "anthropic_api", "chatgpt_subscription", "claude_subscription", "ollama", "other"):
            plan = plan_for(ai_tool=tool, budget_usd=50)
            self.assertFalse(plan["guidance"][0]["connector"])
            self.assertNotIn("api_key", json.dumps(plan).lower())
        self.assertIn("leave it out", " ".join(plan_for(ai_tool="anthropic_api")["cost"]["notes"]))
        self.assertFalse(plan_for(voice="elevenlabs_existing")["guidance"][1]["connector"])

    def test_subscription_credits_are_not_denied(self):
        for tool in ("chatgpt_subscription", "claude_subscription"):
            text = " ".join(plan_for(ai_tool=tool)["cost"]["notes"]).lower()
            self.assertIn("credits or other benefits", text)
            self.assertNotRegex(text, r"no (free )?credits|not eligible|no benefits")

    def test_offline_voice_needs_microphone(self):
        self.assertEqual(status(plan_for(voice="offline_vosk", microphone=False), "offline_voice")["reasons"], ["Needs a microphone"])
        plan = plan_for(voice="offline_vosk", microphone=True)
        self.assertEqual(status(plan, "offline_voice")["status"], "included")
        self.assertTrue(plan["launch_command"].endswith("--voice-model models/voice-en"))
        self.assertTrue({"voice_packages", "voice_model"} <= {t["id"] for t in plan["tasks"]})

    def test_hand_control_has_real_adapter_tasks_and_hand_only_launch(self):
        plan = plan_for(preset="creator", features=["hand_control"])
        self.assertEqual(status(plan, "hand_control")["maturity"], "implemented")
        self.assertIn("--allow-hand-control", plan["launch_command"])
        self.assertIn("--hand-model models/hand_landmarker.task", plan["launch_command"])
        self.assertNotIn("--camera ", plan["launch_command"] + " ")
        self.assertFalse([t for t in plan["tasks"] if t["kind"] == "pending"])
        self.assertTrue({"hand_packages", "hand_model"} <= {task["id"] for task in plan["tasks"]})
        linux = plan_for(preset="creator", features=["hand_control"], os="linux")
        self.assertNotIn("--allow-hand-control", linux["launch_command"])
        self.assertEqual(status(linux, "hand_control")["status"], "unavailable")
        self.assertIn("Needs Windows", status(linux, "hand_control")["reasons"])

    def test_dependencies_are_added_and_explained(self):
        plan = plan_for(preset="custom", features=["boss_alert"])
        self.assertEqual(status(plan, "presence_guard")["status"], "included")
        self.assertIn("Added because another selected feature needs it.", status(plan, "presence_guard")["notes"])
        self.assertIn("--allow-spreadsheet", plan["launch_command"])

    def test_declined_install_plan_is_empty(self):
        plan = plan_for(install=False)
        self.assertFalse(plan["install"])
        self.assertIsNone(plan["launch_command"])
        self.assertEqual(plan["tasks"], [])

    def test_launch_command_only_uses_allowlisted_tokens(self):
        for data in combos():
            plan = planner.build_plan(planner.validate_answers(data, CATALOG), CATALOG)
            self.assertTrue(set(plan["launch_command"].split()) <= ALLOWED_TOKENS, plan["launch_command"])
            for task in plan["tasks"]:
                if task["command"]:
                    self.assertTrue(task["command"].startswith(tuple(prefix + " -m " for prefix in INTERPRETERS)), task["command"])
                if task["url"]:
                    self.assertTrue(task["url"].startswith("https://"))

    def test_optional_features_use_the_installer_environment_on_each_os(self):
        for system, interpreter in (("windows", r".\.venv\Scripts\python.exe"), ("macos", "./.venv/bin/python"), ("linux", "./.venv/bin/python")):
            with self.subTest(system=system):
                camera = plan_for(preset="custom", features=["presence_guard"], os=system)
                voice = plan_for(preset="custom", features=[], os=system, camera=False, microphone=True, voice="offline_vosk")
                simulator = plan_for(preset="custom", features=[], os=system, camera=False, microphone=False)
                self.assertEqual(camera["launch_command"].split()[0], interpreter)
                self.assertEqual(voice["launch_command"].split()[0], interpreter if system == "windows" else "python")
                self.assertEqual("--voice-model" in voice["launch_command"], system == "windows")
                self.assertEqual(simulator["launch_command"].split()[0], "python")

    def test_optional_install_steps_use_the_local_menu(self):
        plan = plan_for(preset="custom", features=["hand_control", "presence_guard"], microphone=True, voice="offline_vosk")
        for task in plan["tasks"]:
            if task["id"] in {"camera_packages", "hand_packages", "voice_packages"}:
                self.assertEqual(task["command"], "python -m jarvis.install_tools")
                self.assertIn("Install-Optional-Tools.cmd", task["detail"])
        self.assertFalse(any("-m pip" in (task["command"] or "") for task in plan["tasks"]))

    def test_generated_environment_interpreter_runs_the_launcher(self):
        system = "windows" if os.name == "nt" else "linux"
        interpreter = plan_for(preset="custom", features=["presence_guard"], os=system)["launch_command"].split()[0]
        with tempfile.TemporaryDirectory(prefix="jarvis setup ") as directory:
            root = Path(directory)
            created = subprocess.run([sys.executable, "-m", "venv", "--copies", "--without-pip", str(root / ".venv")],
                                     capture_output=True, text=True, timeout=30)
            self.assertEqual(created.returncode, 0, created.stderr)
            shutil.copytree(planner.ROOT / "jarvis", root / "jarvis", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            (root / "web").mkdir()
            shutil.copy2(planner.ROOT / "web" / "setup-catalog.json", root / "web" / "setup-catalog.json")
            command = ["cmd", "/d", "/s", "/c", interpreter + " -m jarvis.launcher --check"] if os.name == "nt" else [interpreter, "-m", "jarvis.launcher", "--check"]
            launched = subprocess.run(command, cwd=root,
                                      capture_output=True, text=True, timeout=15)
            self.assertEqual(launched.returncode, 0, launched.stderr)
            self.assertIn("prerequisites look ready", launched.stdout)

    def test_plan_is_deterministic_and_serializable(self):
        data = answers(voice="offline_vosk", microphone=True, budget_usd=42.5)
        first = planner.build_plan(planner.validate_answers(copy.deepcopy(data), CATALOG), CATALOG)
        second = planner.build_plan(planner.validate_answers(copy.deepcopy(data), CATALOG), CATALOG)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        reparsed = planner.parse_json(json.dumps(first))
        self.assertEqual(planner.validate_answers(reparsed["answers"], CATALOG), first["answers"])


class ConfigTests(unittest.TestCase):
    def test_extreme_saved_input_is_ignored_without_modifying_the_file(self):
        giant = json.dumps({"schema": planner.CONFIG_SCHEMA, "answers": answers(budget_usd=10 ** 400)})
        nested = '{"schema":"' + planner.CONFIG_SCHEMA + '","answers":' + "[" * 3000 + "0" + "]" * 3000 + "}"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / planner.CONFIG_NAME
            for content in (giant, nested):
                with self.subTest(kind="budget" if content == giant else "nested"):
                    self.assertLess(len(content), 16384)
                    path.write_text(content, encoding="utf-8")
                    self.assertIsNone(planner.load_config(directory, CATALOG))
                    self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_atomic_save_writes_minimal_config_only(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = planner.validate_answers(answers(budget_usd=20), CATALOG)
            path = planner.save_config(saved, directory)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["setup-config.json"])
            data = json.loads(path.read_text())
            self.assertEqual(set(data), {"schema", "answers"})
            self.assertNotRegex(path.read_text().lower(), "token|password|secret|key\"")
            self.assertEqual(planner.load_config(directory, CATALOG), saved)

    def test_invalid_or_tampered_config_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / planner.CONFIG_NAME
            for body in ("not json", '{"schema":"jarvis-config/1","answers":{"__proto__":{}}}', '{"schema":"other","answers":{}}', "x" * 20000):
                target.write_text(body)
                self.assertIsNone(planner.load_config(directory, CATALOG))
            self.assertIsNone(planner.load_config(Path(directory) / "missing", CATALOG))

    def test_failed_write_leaves_no_partial_file(self):
        with tempfile.TemporaryDirectory() as directory, unittest.mock.patch("jarvis.planner.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                planner.save_config(planner.validate_answers(answers(), CATALOG), directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_diagnostics_never_imports_optional_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            seen = []
            report = planner.diagnostics(directory, finder=lambda name: seen.append(name) or None)
            self.assertEqual(seen, ["cv2", "vosk", "sounddevice", "mediapipe"])
            self.assertFalse(report["voice_model"])
            model = Path(directory) / "models" / "voice-en"
            (model / "conf").mkdir(parents=True)
            (model / "am").mkdir()
            (model / "conf" / "model.conf").write_text("")
            report = planner.diagnostics(directory, finder=lambda name: object())
            self.assertTrue(report["voice_model"])
            self.assertTrue(all(report["packages"].values()))
            self.assertTrue(report["python_ok"])


@unittest.skipUnless(shutil.which("node"), "Node.js not installed; JavaScript parity check skipped")
class JavaScriptParityTests(unittest.TestCase):
    def run_node(self, script, payload):
        result = subprocess.run(["node", "-e", script, str(SETUP_JS)], input=json.dumps(payload), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_static_wizard_builds_identical_plans(self):
        cases = list(combos())
        self.assertGreater(len(cases), 100)
        script = ("const api=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
                  "const {catalog,cases}=JSON.parse(s);process.stdout.write(JSON.stringify(cases.map(a=>api.buildPlan(api.validateAnswers(a,catalog),catalog))));});")
        plans = self.run_node(script, {"catalog": CATALOG, "cases": cases})
        for data, js_plan in zip(cases, plans):
            self.assertEqual(js_plan, planner.build_plan(planner.validate_answers(data, CATALOG), CATALOG))

    def test_static_wizard_rejects_the_same_inputs(self):
        bad = [answers(preset="admin"), answers(budget_usd=501), answers(budget_usd=-0.5), answers(camera_index=10),
               answers(features=["presence_guard", "presence_guard"]), answers(ai_tool="gpt"), {**answers(), "api_key": "x"},
               answers(camera="true"), answers(features=["dashboard"])]
        proto = '{"__proto__": {"polluted": true}, ' + json.dumps(answers())[1:]
        script = ("const api=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
                  "const {catalog,cases,proto}=JSON.parse(s);const all=cases.concat([JSON.parse(proto)]);"
                  "const out=all.map(a=>{try{api.validateAnswers(a,catalog);return 'accepted';}catch(e){return 'rejected';}});"
                  "out.push(({}).polluted===undefined?'clean':'polluted');process.stdout.write(JSON.stringify(out));});")
        result = self.run_node(script, {"catalog": CATALOG, "cases": bad, "proto": proto})
        self.assertEqual(result, ["rejected"] * (len(bad) + 1) + ["clean"])

    def test_provider_catalog_reader_drops_unsafe_entries(self):
        data = {"checked_on": "2026-10-01", "budget_note": "Ceiling only.", "providers": [
            {"id": "openai-api", "name": "OpenAI", "description": "d", "credit_note": "Eligible plans may include credits.",
             "billing": "usage-based", "monthly_software_usd": None, "source_url": "https://example.org/pricing", "credits_url": "https://example.org/credits"},
            {"id": "anthropic-api", "source_url": "javascript:alert(1)", "setup_url": "http://insecure.example", "description": "x"},
            {"id": "unknown", "description": "y"}, "string", {"id": "ollama-local", "description": 5, "monthly_software_usd": "free"},
            {"id": "elevenlabs", "example_rate": {"model": "m", "usd_per_1000_characters": -1}}]}
        script = ("const api=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
                  "const {catalog,data}=JSON.parse(s);process.stdout.write(JSON.stringify(api.readProviderCatalog(data,catalog)));});")
        result = self.run_node(script, {"catalog": CATALOG, "data": data})
        self.assertEqual([e["id"] for e in result["entries"]], ["openai-api", "anthropic-api", "ollama-local", "elevenlabs"])
        self.assertEqual([link["label"] for link in result["entries"][0]["links"]], ["Pricing or source", "Credits and eligibility"])
        self.assertEqual(result["entries"][0]["creditNote"], "Eligible plans may include credits.")
        self.assertEqual(result["entries"][1]["links"], [])
        self.assertEqual(result["entries"][2]["description"], "")
        self.assertEqual(result["entries"][3]["exampleRate"], "")
        self.assertEqual(result["budgetNote"], "Ceiling only.")

    def test_provider_catalog_entries_shape_is_supported(self):
        data = {"schema": "jarvis-provider-catalog/1", "checked": "2026-10-28", "entries": [
            {"id": "claude_subscription", "name": "Claude", "summary": "s", "benefits": "Some plans include API credits.", "url": "https://example.org"},
            {"id": "openai_api", "url": "data:text/html,x"}, {"id": "__proto__", "summary": "x"}]}
        script = ("const api=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
                  "const {catalog,data}=JSON.parse(s);process.stdout.write(JSON.stringify(api.readProviderCatalog(data,catalog)));});")
        result = self.run_node(script, {"catalog": CATALOG, "data": data})
        self.assertEqual([e["id"] for e in result["entries"]], ["claude_subscription", "openai_api"])
        self.assertEqual(result["entries"][0]["creditNote"], "Some plans include API credits.")
        self.assertEqual(result["entries"][1]["links"], [])
        self.assertEqual(result["checked"], "2026-10-28")

    def test_supplied_provider_catalog_maps_every_paid_choice(self):
        path = planner.ROOT / "web" / "provider-catalog.json"
        if not path.exists():
            self.skipTest("Root has not supplied a provider catalog yet")
        script = ("const api=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
                  "const {catalog,data}=JSON.parse(s);process.stdout.write(JSON.stringify(api.readProviderCatalog(data,catalog)));});")
        result = self.run_node(script, {"catalog": CATALOG, "data": json.loads(path.read_text(encoding="utf-8"))})
        found = {entry["id"] for entry in result["entries"]}
        for item in CATALOG["ai_tools"] + CATALOG["voice_options"]:
            if item.get("provider"):
                self.assertTrue({item["id"], item["provider"]} & found, item["id"] + " has no provider catalog entry")
        for entry in result["entries"]:
            self.assertTrue(all(link["url"].startswith("https://") for link in entry["links"]))


class StaticModeTests(unittest.TestCase):
    def test_static_wizard_only_fetches_relative_paths(self):
        source = SETUP_JS.read_text(encoding="utf-8")
        calls = re.findall(r"fetch\(\s*([^,)]+)", source)
        self.assertTrue(calls)
        for call in calls:
            self.assertNotRegex(call, "https?:|//|localhost|127\\.0\\.0\\.1", call)
        self.assertNotIn("localhost", source)
        self.assertNotRegex(source, r"innerHTML|eval\(|new Function")
        self.assertIn('forced !== "hosted"', source)

    def test_setup_page_is_self_contained(self):
        html = (planner.ROOT / "web" / "setup.html").read_text(encoding="utf-8")
        self.assertIn('href="setup.css"', html)
        self.assertIn('src="setup.js"', html)
        self.assertNotRegex(html, r'(src|href)="(https?:)?//')
        self.assertNotRegex(html, r'type="password"|api[ _-]?key"')


if __name__ == "__main__":
    unittest.main()
