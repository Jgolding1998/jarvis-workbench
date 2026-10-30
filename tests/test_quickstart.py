import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from jarvis import quickstart

ROOT = Path(__file__).resolve().parents[1]
SETUP_JS = ROOT / "web" / "setup.js"
CATALOG = quickstart.load_catalog()
OWNED_TEXT = [ROOT / "web" / name for name in ("setup.html", "setup.js", "setup.css", "quickstart-catalog.json")] + \
    [ROOT / "website" / name for name in ("index.html", "site.css", "site.js", "privacy.html")] + \
    [ROOT / "jarvis" / "quickstart.py", ROOT / "scripts" / "build_site.py", ROOT / "tests" / "browser_quickstart.cjs"]
FULL = {"role": "office", "budget": 0, "existing": "none", "dictation": "keyboard", "reply": "none", "automation": "none"}


def plan(**changes):
    return quickstart.recommend({**FULL, **changes})


def broken(change):
    data = copy.deepcopy(CATALOG)
    change(data)
    return data


MALFORMED_CATALOGS = [
    lambda d: d.update(schema="other"),
    lambda d: d.update(extra=True),
    lambda d: d.pop("reply"),
    lambda d: d["roles"].append(dict(d["roles"][0])),
    lambda d: d["dictation"].append(dict(d["dictation"][0])),
    lambda d: d["roles"][0].update(features=["microphone"]),
    lambda d: d["roles"][0].update(features=["hand", "hand"]),
    lambda d: d["roles"][0].update(colour="blue"),
    lambda d: d["roles"][0]["suggest"].update(reply="elevenlabs"),
    lambda d: d["roles"][0]["suggest"].pop("automation"),
    lambda d: d["roles"][0].update(id="Office"),
    lambda d: d["existing"][0].update(zero="unknown"),
    lambda d: d["existing"][0]["zero_notes"].append("missing"),
    lambda d: d["dictation"][1].update(status="connected"),
    lambda d: d["dictation"][3].pop("url"),
    lambda d: d["dictation"][3].update(url="http://example.org/guide"),
    lambda d: d["reply"][2].update(step="Install the cloud voice."),
    lambda d: d["automation"][1].pop("note"),
    lambda d: d["automation"][0].update(command="run"),
    lambda d: d["providers"]["nvidia"].update(setup_url="http://build.nvidia.com/"),
    lambda d: d["providers"]["nvidia"].update(token="x"),
    lambda d: d["release"].update(status="live"),
    lambda d: d["release"].update(feedback="javascript:alert(1)"),
    lambda d: d["budget"].update(min=1),
    lambda d: d["budget"].update(max=100),
    lambda d: d["budget"].update(max=10000.5),
    lambda d: d["budget"].update(step=5),
    lambda d: d["budget"].update(slider_max=20000),
    lambda d: d["budget"].update(large_from=0),
    lambda d: d["budget"].update(currency="USD"),
    lambda d: d.pop("budget"),
    lambda d: d["providers"]["openai"].update(limit_url="http://example.org"),
    lambda d: d.update(defaults={**d["defaults"], "role": "admin"}),
    lambda d: d.update(defaults={"role": "office", "budget": 0, "existing": "none"}),
    lambda d: d.update(defaults={**d["defaults"], "budget": "0"}),
    lambda d: d["notes"].pop("large_budget"),
    lambda d: d["notes"].update(nvidia_trial="Fast " + chr(0x2014) + " and free"),
    lambda d: d["features"].update(voice={"name": "Voice", "note": "Voice.", "step": "Voice."}),
]


class CatalogTests(unittest.TestCase):
    def test_questions_have_the_agreed_options(self):
        self.assertEqual([r["id"] for r in CATALOG["roles"]], ["developer", "office", "study", "creator", "handsfree"])
        self.assertEqual(CATALOG["budget"], {"min": 0, "max": 10000, "step": 1, "slider_max": 1000, "large_from": 101})
        self.assertEqual([e["id"] for e in CATALOG["existing"]], ["none", "chat_only", "nvidia", "openai", "anthropic", "ollama"])
        self.assertEqual([e["id"] for e in CATALOG["dictation"]], ["keyboard", "windows", "wispr", "vosk"])
        self.assertEqual([e["id"] for e in CATALOG["reply"]], ["none", "read_aloud", "cloud_voice"])
        self.assertEqual([e["id"] for e in CATALOG["automation"]], ["none", "agent_later", "calendar_email"])
        self.assertEqual(quickstart.default_choices(), FULL)

    def test_release_and_help_links(self):
        release = CATALOG["release"]
        self.assertEqual(release["windows_zip"], "https://github.com/Jgolding1998/jarvis-workbench/releases/latest/download/Jarvis-Windows.zip")
        self.assertIn(release["status"], ("pending", "available"))
        self.assertEqual(release["agent_guide"], "https://github.com/Jgolding1998/jarvis-workbench/blob/main/docs/INSTALL_WITH_AGENT.md")
        self.assertEqual(release["feedback"], "https://github.com/Jgolding1998/Jgolding1998/issues/new?template=feedback.yml")
        self.assertEqual(release["source_zip"], "https://github.com/Jgolding1998/jarvis-workbench/releases/latest/download/Get-Jarvis-Workbench.zip")

    def test_malformed_catalogs_are_rejected(self):
        for index, change in enumerate(MALFORMED_CATALOGS):
            with self.subTest(case=index), self.assertRaises(ValueError):
                quickstart.validate_catalog(broken(change))

    def test_duplicate_keys_in_the_catalog_file_are_rejected(self):
        text = (ROOT / "web" / "quickstart-catalog.json").read_text(encoding="utf-8").replace('"schema"', '"schema": "x", "schema"', 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                quickstart.load_catalog(path)

    def test_loaded_catalog_is_a_copy(self):
        first = quickstart.load_catalog()
        first["roles"].clear()
        self.assertTrue(quickstart.load_catalog()["roles"])

    def test_unbuilt_tools_are_honest(self):
        status = {item["id"]: item["status"] for name in ("dictation", "reply", "automation") for item in CATALOG[name]}
        self.assertEqual(status["wispr"], "external")
        self.assertEqual(status["vosk"], "manual")
        self.assertEqual(status["cloud_voice"], "later")
        self.assertEqual(status["agent_later"], "later")
        self.assertEqual(status["calendar_email"], "later")
        vosk = next(item for item in CATALOG["dictation"] if item["id"] == "vosk")
        self.assertIn("only a few words", vosk["note"])
        self.assertIn("download a small English Vosk model", vosk["step"])
        self.assertIn("no Wispr Flow connector", next(i for i in CATALOG["dictation"] if i["id"] == "wispr")["hint"])
        self.assertIn("no MCP bridge", next(i for i in CATALOG["automation"] if i["id"] == "agent_later")["note"])
        read = next(i for i in CATALOG["reply"] if i["id"] == "read_aloud")
        self.assertIn("never the microphone", read["note"])
        self.assertIn("not on this public page", read["note"])
        text = json.dumps(CATALOG).lower()
        for claim in ("whisper", "connected to", "is installed", "downloads the voice model", "guarantee", "always free", "unlimited"):
            self.assertNotIn(claim, text)


class ValidationTests(unittest.TestCase):
    def test_budget_is_any_whole_dollar_in_range(self):
        for value in (0, 1, 7, 99, 100, 101, 999, 1000, 1001, 9999, 10000, "0", "10000", "2500", 25.0):
            with self.subTest(value=value):
                self.assertEqual(quickstart.validate_choices({**FULL, "budget": value})["budget"], int(value))

    def test_old_three_field_choices_migrate_with_defaults(self):
        self.assertEqual(quickstart.validate_choices({"role": "study", "budget": "25", "existing": "ollama"}),
                         {"role": "study", "budget": 25, "existing": "ollama", "dictation": "keyboard", "reply": "none", "automation": "none"})
        self.assertEqual(quickstart.validate_choices({"role": "office", "budget": 0, "existing": "none", "reply": "read_aloud"})["reply"], "read_aloud")
        self.assertEqual(list(quickstart.validate_choices({"automation": "none", "existing": "none", "budget": 0, "role": "office"})), list(quickstart.CHOICE_KEYS))

    def test_each_error_has_fixed_text_and_never_echoes_input(self):
        private = "private-fixture-value"
        cases = [
            (None, quickstart.ERROR_SHAPE), ([], quickstart.ERROR_SHAPE), ("office", quickstart.ERROR_SHAPE),
            ({"role": "office", "budget": 0}, quickstart.ERROR_SHAPE),
            ({**FULL, "key": private}, quickstart.ERROR_SHAPE),
            ({**FULL, "__proto__": {"polluted": True}}, quickstart.ERROR_SHAPE),
            ({"budget": 0, "existing": "none", "dictation": "keyboard"}, quickstart.ERROR_SHAPE),
            ({**FULL, "role": private}, quickstart.ERROR_ROLE),
            ({**FULL, "role": ["office"]}, quickstart.ERROR_ROLE),
            ({**FULL, "budget": 10001}, quickstart.ERROR_BUDGET),
            ({**FULL, "budget": -1}, quickstart.ERROR_BUDGET),
            ({**FULL, "budget": "10001"}, quickstart.ERROR_BUDGET),
            ({**FULL, "budget": "100000"}, quickstart.ERROR_BUDGET),
            ({**FULL, "budget": " 5"}, quickstart.ERROR_BUDGET),
            ({**FULL, "budget": "1e3"}, quickstart.ERROR_BUDGET),
            ({**FULL, "budget": "٢"}, quickstart.ERROR_BUDGET),
            ({**FULL, "budget": float("nan")}, quickstart.ERROR_BUDGET),
            ({**FULL, "budget": True}, quickstart.ERROR_BUDGET),
            ({**FULL, "budget": 10.5}, quickstart.ERROR_BUDGET),
            ({**FULL, "budget": "010"}, quickstart.ERROR_BUDGET),
            ({**FULL, "budget": None}, quickstart.ERROR_BUDGET),
            ({**FULL, "existing": private}, quickstart.ERROR_EXISTING),
            ({**FULL, "existing": "chatgpt"}, quickstart.ERROR_EXISTING),
            ({**FULL, "dictation": "whisper"}, quickstart.ERROR_DICTATION),
            ({**FULL, "dictation": None}, quickstart.ERROR_DICTATION),
            ({**FULL, "reply": "elevenlabs"}, quickstart.ERROR_REPLY),
            ({**FULL, "automation": "mcp"}, quickstart.ERROR_AUTOMATION),
            ({**FULL, "automation": 1}, quickstart.ERROR_AUTOMATION),
        ]
        for data, message in cases:
            with self.subTest(data=data):
                with self.assertRaises(ValueError) as caught:
                    quickstart.validate_choices(data)
                self.assertEqual(str(caught.exception), message)
                self.assertNotIn(private, str(caught.exception))


class RecommendationTests(unittest.TestCase):
    def test_every_combination_has_the_contracted_shape(self):
        combos = list(quickstart.all_choices())
        self.assertEqual(len(combos), 5 * 7 * 6 * 4 * 3 * 3)
        keys = {"choices", "provider", "features", "title", "summary", "stack", "steps", "tradeoffs", "setup_url"}
        for choices in combos:
            result = quickstart.recommend(choices)
            self.assertLessEqual(keys, set(result))
            self.assertLessEqual(set(result), keys | {"model"})
            self.assertEqual(result["choices"], choices)
            self.assertTrue(result["features"])
            self.assertLessEqual(set(result["features"]), {"camera", "hand"})
            self.assertEqual([row["part"] for row in result["stack"]], ["desk", "ai", "input", "reply", "automation"])
            self.assertTrue(all(row["status"] in quickstart.STATUSES for row in result["stack"]))
            self.assertTrue(result["setup_url"].startswith("https://"))
            self.assertEqual(result["tradeoffs"][-1], CATALOG["budget_note"])
            self.assertEqual(result["steps"][:2], [CATALOG["steps"]["download"], CATALOG["steps"]["start"]])

    def test_zero_budget_rules(self):
        self.assertEqual(plan(existing="ollama")["provider"], "ollama")
        for existing in ("none", "chat_only", "nvidia", "openai", "anthropic"):
            self.assertEqual(plan(existing=existing)["provider"], "nvidia", existing)
        for existing, name in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
            text = " ".join(plan(existing=existing)["tradeoffs"])
            self.assertIn("At $0 Jarvis does not send paid " + name + " requests", text)
            self.assertIn("eligible API credits", text)

    def test_paid_budget_rules(self):
        for budget in (1, 100, 101, 5000, 10000):
            self.assertEqual(plan(budget=budget, existing="openai")["provider"], "openai")
            self.assertEqual(plan(budget=budget, existing="anthropic")["provider"], "anthropic")
            self.assertEqual(plan(budget=budget, existing="chat_only")["provider"], "nvidia")
            self.assertEqual(plan(budget=budget, existing="ollama")["provider"], "ollama")
            self.assertIn("only a hard spend limit", " ".join(plan(budget=budget, existing="openai")["tradeoffs"]))
        self.assertNotIn(CATALOG["notes"]["large_budget"], plan(budget=100, existing="openai")["tradeoffs"])
        self.assertIn(CATALOG["notes"]["large_budget"], plan(budget=101, existing="openai")["tradeoffs"])

    def test_purpose_selects_existing_features(self):
        self.assertEqual(plan(role="developer")["features"], ["hand"])
        self.assertEqual(plan(role="handsfree")["features"], ["hand"])
        self.assertEqual(plan(role="office")["features"], ["camera"])
        self.assertEqual(plan(role="developer")["steps"][-1], CATALOG["steps"]["practice"])
        self.assertEqual(plan(role="office")["steps"][-1], CATALOG["steps"]["dashboard"])
        self.assertEqual(plan()["title"], "Office desk with NVIDIA trial access")
        suggestions = {role["id"]: role["suggest"] for role in CATALOG["roles"]}
        self.assertEqual(suggestions["handsfree"], {"dictation": "windows", "reply": "read_aloud", "automation": "none"})
        for suggest in suggestions.values():
            # Purpose only ever suggests options that work today.
            for key, value in suggest.items():
                self.assertIn(next(i for i in CATALOG[key] if i["id"] == value)["status"], ("builtin", "external", "off"))

    def test_voice_and_automation_steps_stay_honest(self):
        self.assertEqual(plan()["steps"], plan(dictation="keyboard", automation="calendar_email")["steps"])
        vosk = plan(dictation="vosk")
        self.assertEqual(vosk["stack"][2], {"part": "input", "name": "Offline voice commands (Vosk)", "status": "manual",
                                            "url": "https://github.com/Jgolding1998/jarvis-workbench/blob/main/docs/BUILD_YOUR_OWN.md#4-add-offline-voice"})
        self.assertIn("manual setup", " ".join(vosk["steps"]))
        self.assertIn("Windows key + H", " ".join(plan(dictation="windows")["steps"]))
        for later in (dict(reply="cloud_voice"), dict(automation="agent_later"), dict(automation="calendar_email")):
            result = plan(**later)
            self.assertEqual(result["steps"], plan()["steps"], later)
            self.assertIn("later", [row["status"] for row in result["stack"]])
        self.assertIn("tick Read replies aloud", " ".join(plan(reply="read_aloud")["steps"]))
        self.assertEqual(plan(existing="ollama")["stack"][1]["name"], "Ollama (gemma3:4b)")


def run_node(test, script, payload):
    result = subprocess.run(["node", "-e", script, str(SETUP_JS)], input=json.dumps(payload), capture_output=True, text=True, timeout=120)
    test.assertEqual(result.returncode, 0, result.stderr)
    return json.loads(result.stdout)


STDIN = "const api=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const input=JSON.parse(s);"


@unittest.skipUnless(shutil.which("node"), "Node.js not installed; JavaScript parity check skipped")
class JavaScriptParityTests(unittest.TestCase):
    def test_every_combination_matches(self):
        cases = list(quickstart.all_choices())
        plans = run_node(self, STDIN + "process.stdout.write(JSON.stringify(input.cases.map(c=>api.recommend(c,input.catalog))));});",
                         {"catalog": CATALOG, "cases": cases})
        self.assertEqual(len(plans), len(cases))
        for choices, js_plan in zip(cases, plans):
            self.assertEqual(js_plan, quickstart.recommend(choices), choices)

    def test_validation_and_migration_match(self):
        cases = [None, [], {"role": "office", "budget": 0}, {**FULL, "x": 1}, {**FULL, "role": "admin"}, {**FULL, "budget": True},
                 {**FULL, "budget": 10.5}, {**FULL, "budget": "010"}, {**FULL, "budget": 10001}, {**FULL, "budget": -1}, {**FULL, "budget": "10000"},
                 {**FULL, "budget": "100000"}, {**FULL, "budget": "1e2"}, {**FULL, "existing": "gpt"}, {**FULL, "dictation": "whisper"},
                 {**FULL, "reply": "elevenlabs"}, {**FULL, "automation": "mcp"}, {"role": "study", "budget": "25", "existing": "ollama"},
                 {"role": "office", "budget": 5000, "existing": "openai", "reply": "read_aloud"}, {"budget": 0, "existing": "none", "reply": "none"},
                 {**FULL, "budget": 1000}, {**FULL, "dictation": None}]
        script = STDIN + ("process.stdout.write(JSON.stringify(input.cases.map(c=>{try{return {ok:api.validateChoices(c,input.catalog)}}"
                          "catch(e){return {error:e.message}}})));});")
        for data, js in zip(cases, run_node(self, script, {"catalog": CATALOG, "cases": cases})):
            try:
                expected = {"ok": quickstart.validate_choices(data)}
            except ValueError as error:
                expected = {"error": str(error)}
            self.assertEqual(js, expected, data)

    def test_catalog_checks_match(self):
        catalogs = [CATALOG] + [broken(change) for change in MALFORMED_CATALOGS]
        script = STDIN + "process.stdout.write(JSON.stringify(input.map(c=>{try{api.checkCatalog(c);return 'ok'}catch(e){return e.message}})));});"
        for index, (data, js) in enumerate(zip(catalogs, run_node(self, script, catalogs))):
            try:
                quickstart.validate_catalog(data)
                expected = "ok"
            except ValueError as error:
                expected = str(error)
            self.assertEqual(js, expected, index)
        self.assertEqual(run_node(self, script, [CATALOG])[0], "ok")

    def test_prototype_and_duplicate_keys_are_rejected(self):
        script = STDIN + ("const out=input.texts.map(t=>{try{api.validateChoices(api.parseImportJson(t),input.catalog);return 'accepted'}catch(e){return e.message||'parse'}});"
                          "out.push(({}).polluted===undefined);process.stdout.write(JSON.stringify(out));});")
        texts = ['{"__proto__": {"polluted": true}, "role": "office", "budget": 0, "existing": "none"}',
                 '{"role": "office", "role": "study", "budget": 0, "existing": "none"}']
        self.assertEqual(run_node(self, script, {"catalog": CATALOG, "texts": texts}), [quickstart.ERROR_SHAPE, "parse", True])

    def test_imported_files_accept_both_schemas(self):
        script = STDIN + ("process.stdout.write(JSON.stringify(input.texts.map(t=>{try{return api.importedChoices(t,input.catalog)}catch(e){return e.message}})));});")
        texts = [json.dumps({"schema": "jarvis-quickstart-choices/1", "choices": {"role": "developer", "budget": 50, "existing": "openai"}}),
                 json.dumps({"schema": "jarvis-quickstart-choices/2", "choices": {**FULL, "budget": 2500}}),
                 json.dumps({"schema": "jarvis-quickstart-choices/3", "choices": FULL}),
                 json.dumps({"schema": "jarvis-quickstart-choices/2", "choices": FULL, "note": "x"}),
                 json.dumps({"schema": "jarvis-quickstart-choices/2", "choices": {**FULL, "budget": 1}}) + " " * 2100]
        result = run_node(self, script, {"catalog": CATALOG, "texts": texts})
        self.assertEqual(result[0], quickstart.validate_choices({"role": "developer", "budget": 50, "existing": "openai"}))
        self.assertEqual(result[1], {**FULL, "budget": 2500})
        self.assertEqual(result[2:], ["Choose a Jarvis choices file saved from this setup page (under 2 KB)."] * 3)

    def test_allowance_reader_keeps_only_valid_service_data(self):
        good = {"month": "2026-10", "limit_usd": 25, "reserved_usd": 0.12, "remaining_usd": 24.88, "paid_requests": 12,
                "reserve_per_request_usd": 0.01, "kind": "local_allowance", "provider_limit_required": True,
                "pricing_checked": "2026-10-28", "valid_until": "2026-11-27"}
        cases = [good, None, {**good, "kind": "invoice"}, {**good, "month": "2026-13"}, {**good, "limit_usd": -1},
                 {**good, "paid_requests": 1.5}, {**good, "reserved_usd": "0.1"}, {**good, "pricing_checked": "soon"},
                 {"available": False, "kind": "local_allowance", "limit_usd": 25, "message": "private-canary"},
                 {"available": False, "kind": "other", "message": "private-canary"}, {**good, "limit_usd": 10000, "remaining_usd": 9999.88}]
        result = run_node(self, STDIN + "process.stdout.write(JSON.stringify(input.map(api.readBudget)));});", cases)
        self.assertEqual(result[8], {"available": False})
        self.assertIsNone(result[9])
        self.assertEqual(result[0]["limit"], 25)
        self.assertEqual(result[1:7], [None] * 6)
        self.assertEqual(result[7]["pricingChecked"], "")
        self.assertEqual(result[10]["limit"], 10000)

    def test_link_state_and_launch_guards(self):
        script = STDIN + ("const catalog=input.catalog;const q=t=>{try{return api.choicesFromQuery(t,catalog)}catch(e){return e.message}};"
                          "process.stdout.write(JSON.stringify({"
                          "query:[q(''),q('?role=study&budget=25&existing=ollama'),q('?role=x&budget=0&existing=none'),q('?role=office'),"
                          "q('?role=office&role=study&budget=0&existing=none'),q(api.choicesQuery(api.validateChoices({role:'creator',budget:9999,existing:'nvidia',reply:'read_aloud'},catalog))),"
                          "q('?role=office&budget=0&existing=none&reply=elevenlabs'),q('?utm=1')],"
                          "labels:[0,1,1000,10000].map(api.budgetLabel),"
                          "launch:['http://127.0.0.1:8765/','http://127.0.0.1:0/','http://127.0.0.1:8765/x','https://127.0.0.1:8765/','http://127.0.0.1.evil:80/','http://evil/',5].map(api.isSafeLaunchUrl),"
                          "loop:[[{protocol:'http:',hostname:'127.0.0.1'},'auto'],[{protocol:'http:',hostname:'127.0.0.1'},'hosted'],[{protocol:'http:',hostname:'localhost'},'auto']].map(a=>api.isLoopbackPage(a[0],a[1])),"
                          "state:[api.readQuickstartState({local:false},catalog),api.readQuickstartState({local:true,choices:{role:'bad'},credentials:{nvidia:'yes',openai:true},install:{state:'weird'}},catalog),"
                          "api.readQuickstartState({local:true,choices:{role:'office',budget:10,existing:'none'},install:{state:'ready',message:'x'},read_aloud_allowed:true},catalog)]}));});")
        result = run_node(self, script, {"catalog": CATALOG})
        self.assertEqual(result["query"][:4], [None, quickstart.validate_choices({"role": "study", "budget": 25, "existing": "ollama"}), quickstart.ERROR_ROLE, quickstart.ERROR_SHAPE])
        self.assertEqual(result["query"][4], quickstart.ERROR_SHAPE)
        self.assertEqual(result["query"][5], quickstart.validate_choices({"role": "creator", "budget": 9999, "existing": "nvidia", "reply": "read_aloud"}))
        self.assertEqual(result["query"][6:], [quickstart.ERROR_REPLY, None])
        self.assertEqual(result["labels"], ["$0, free options only", "$1 a month", "$1,000 a month", "$10,000 a month"])
        self.assertEqual(result["launch"], [True, False, False, False, False, False, False])
        self.assertEqual(result["loop"], [True, False, False])
        self.assertIsNone(result["state"][0])
        self.assertEqual(result["state"][1], {"local": True, "choices": None, "credentials": {"nvidia": False, "openai": True, "anthropic": False}, "install": "idle", "included": False, "budget": None, "readAloud": False})
        self.assertEqual(result["state"][2]["choices"], quickstart.validate_choices({"role": "office", "budget": 10, "existing": "none"}))
        self.assertIs(result["state"][2]["readAloud"], True)
        self.assertIs(result["state"][2]["included"], False)

    def test_reader_uses_only_on_device_voices_and_can_cancel(self):
        script = STDIN + r"""
const log=[];function U(t){this.text=t;}
const make=(voices)=>({getVoices:()=>voices,speak:(u)=>log.push(['speak',u.text,u.voice&&u.voice.name,u.lang]),cancel:()=>log.push(['cancel'])});
const remote={name:'Remote',lang:'en-US',localService:false,default:true}, local={name:'Local',lang:'en-GB',localService:true}, other={name:'Other',lang:'fr-FR',localService:true,default:true};
const out={};
const r=api.createReader(make([remote,other,local]),U);
out.speak=r.speak('hello ' + 'x'.repeat(5000));out.first=log.slice();log.length=0;
r.cancel();out.cancel=log.slice();log.length=0;
out.remoteOnly=api.createReader(make([remote]),U).speak('hi');out.remoteLog=log.slice();log.length=0;
out.empty=api.createReader(make([local]),U).speak('   ');
out.none=[api.createReader(null,U).speak('hi'),api.createReader(make([local]),undefined).speak('hi'),api.createReader({speak(){}},U).speak('hi')];
log.length=0;
out.throws=api.createReader({getVoices(){throw new Error('x')},speak(){log.push('bad')},cancel(){throw new Error('y')}},U).speak('hi');
out.after=log.slice();
process.stdout.write(JSON.stringify(out));});"""
        out = run_node(self, script, {})
        self.assertTrue(out["speak"])
        self.assertEqual(out["first"][0], ["cancel"])
        self.assertEqual(out["first"][1][0], "speak")
        self.assertEqual(out["first"][1][2:], ["Local", "en-GB"])
        self.assertEqual(len(out["first"][1][1]), 4000)
        self.assertEqual(out["cancel"], [["cancel"]])
        self.assertFalse(out["remoteOnly"])
        self.assertEqual(out["remoteLog"], [["cancel"]])
        self.assertFalse(out["empty"])
        self.assertEqual(out["none"], [False, False, False])
        self.assertFalse(out["throws"])
        self.assertEqual(out["after"], [])

    def test_chat_limits_match_the_local_provider_client(self):
        from jarvis import provider_client
        script = STDIN + r"""
const long='a'.repeat(api.REPLY_LIMIT);
process.stdout.write(JSON.stringify({limits:[api.PROMPT_LIMIT,api.REPLY_LIMIT,api.SPEAK_LIMIT],
  ok:[api.chatReply('hi'),api.chatReply(long)===long,api.chatReply('x'.repeat(5000)).length],
  bad:[api.chatReply(long+'a'),api.chatReply('   '),api.chatReply(''),api.chatReply(5),api.chatReply(null),api.chatReply(['hi'])],
  emoji:[api.chatReply(input.emoji)===input.emoji,api.chatReply(input.emoji+'\u{1F600}'),
    api.speechText(input.emoji)==='\u{1F600}'.repeat(4000),api.speechText('a'+input.emoji)==='a'+'\u{1F600}'.repeat(3999),
    /[\uD800-\uDBFF]$/.test(api.speechText('a'+input.emoji))]}));});"""
        emoji = "\U0001F600" * provider_client.MAX_OUTPUT_CHARS
        self.assertLessEqual(len(emoji.encode("utf-8")), provider_client.MAX_OUTPUT_BYTES)
        out = run_node(self, script, {"emoji": emoji})
        self.assertEqual(out["limits"], [provider_client.MAX_PROMPT_CHARS, provider_client.MAX_OUTPUT_CHARS, 4000])
        self.assertEqual(out["ok"], ["hi", True, 5000])
        self.assertEqual(out["bad"], [None] * 6)
        self.assertEqual(out["emoji"], [True, None, True, True, False])
        html = (ROOT / "web" / "setup.html").read_text(encoding="utf-8")
        self.assertIn('<textarea id="prompt" rows="3" maxlength="%d"' % provider_client.MAX_PROMPT_CHARS, html)
        source = SETUP_JS.read_text(encoding="utf-8")
        self.assertNotIn("result.reply.slice(", source)
        self.assertIn("new Utterance(speechText(text))", source)


class StaticSafetyTests(unittest.TestCase):
    def test_setup_script_only_fetches_contracted_same_origin_paths(self):
        source = SETUP_JS.read_text(encoding="utf-8")
        targets = set(re.findall(r'localJson\("([^"]+)"', source)) | set(re.findall(r'fetch\("([^"]+)"', source))
        allowed = {"/api/session", "/api/quickstart", "/api/quickstart/save", "/api/quickstart/key", "/api/quickstart/key/delete",
                   "/api/quickstart/test", "/api/quickstart/chat", "/api/quickstart/install", "/api/quickstart/launch", "/api/setup/exit",
                   "quickstart-catalog.json"}
        self.assertEqual(targets, allowed)
        for pattern in (r"localStorage", r"sessionStorage", r"indexedDB", r"console\.", r"innerHTML", r"eval\(", r"new Function", r"document\.cookie",
                        r"getUserMedia", r"SpeechRecognition", r"sendBeacon", r"XMLHttpRequest", r"WebSocket"):
            self.assertNotRegex(source, pattern)
        self.assertNotRegex(source, r'fetch\(\s*["\'`]https?:')

    def test_read_aloud_is_local_only_and_opt_in(self):
        source = SETUP_JS.read_text(encoding="utf-8")
        self.assertEqual(source.count("api.createReader("), 1)
        self.assertIn('if (!state.local) return;\n    if (!state.server || state.server.readAloud !== true) { refuseReadAloud(); return; }\n'
                      '    if (!$("speak").checked || state.choices.reply !== "read_aloud") return;', source)
        self.assertIn("readAloud: data.read_aloud_allowed === true", source)
        self.assertIn("voice.localService === true", source)
        for event in ('"pagehide", stopSpeaking', '"visibilitychange"', 'event.key === "Escape"'):
            self.assertIn(event, source)
        html = (ROOT / "web" / "setup.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'<input id="speak" type="checkbox">')
        self.assertNotRegex(html, r'id="speak"[^>]*checked')

    def test_public_pages_have_no_key_field_or_external_assets(self):
        for path in (ROOT / "web" / "setup.html", ROOT / "website" / "index.html", ROOT / "website" / "privacy.html"):
            html = path.read_text(encoding="utf-8")
            self.assertNotIn('type="password"', html, path.name)
            self.assertIn('http-equiv="Content-Security-Policy"', html, path.name)
            self.assertNotRegex(html, r'<script[^>]*>\s*[^<\s]', path.name)
        setup = (ROOT / "web" / "setup.html").read_text(encoding="utf-8") + (ROOT / "web" / "setup.css").read_text(encoding="utf-8")
        self.assertNotRegex(setup, r'(src|href)="https?:|url\(\s*["\']?https?:|@import|@font-face')
        self.assertNotIn('class="orb"', setup)

    def test_owned_files_have_no_em_dashes(self):
        for path in OWNED_TEXT:
            self.assertNotIn(chr(0x2014), path.read_text(encoding="utf-8"), path.name)

    def test_landing_page_keeps_hand_guide_and_practice_and_links_help(self):
        html = (ROOT / "website" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('class="gesture-card"'), 4)
        for marker in ('id="g-hand-open"', 'id="pad-stage"', 'href="setup.html"', CATALOG["release"]["feedback"], CATALOG["release"]["agent_guide"]):
            self.assertIn(marker, html)

    def test_hosted_build_carries_the_quickstart_catalog(self):
        spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as directory:
            output = builder.build(Path(directory))
            self.assertEqual(json.loads((output / "quickstart-catalog.json").read_text(encoding="utf-8")), CATALOG)
            hosted = (output / "setup.html").read_text(encoding="utf-8")
            self.assertIn('name="jarvis-mode" content="hosted"', hosted)
            self.assertNotIn('type="password"', hosted)


@unittest.skipUnless(shutil.which("node"), "Node.js not installed; browser fixtures skipped")
class BrowserFixtureTests(unittest.TestCase):
    def test_headless_browser_fixtures(self):
        result = subprocess.run(["node", str(ROOT / "tests" / "browser_quickstart.cjs")], capture_output=True, text=True, timeout=600)
        if result.stdout.startswith("SKIP"):
            self.skipTest(result.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-4000:])
        report = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertEqual(report["failures"], [])
        self.assertGreater(report["passed"], 300)


if __name__ == "__main__":
    unittest.main()
