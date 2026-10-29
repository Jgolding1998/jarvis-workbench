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


def plan(role="office", budget=0, existing="none"):
    return quickstart.recommend({"role": role, "budget": budget, "existing": existing})


class CatalogTests(unittest.TestCase):
    def test_three_dropdowns_have_exactly_the_agreed_options(self):
        self.assertEqual([r["id"] for r in CATALOG["roles"]], ["developer", "office", "study", "creator"])
        self.assertEqual(CATALOG["budget"], {"min": 0, "max": 100, "step": 1})
        self.assertEqual([e["id"] for e in CATALOG["existing"]], ["none", "nvidia", "openai", "anthropic", "ollama"])
        self.assertEqual(quickstart.default_choices(), {"role": "office", "budget": 0, "existing": "none"})

    def test_release_and_help_links(self):
        release = CATALOG["release"]
        self.assertEqual(release["windows_zip"], "https://github.com/Jgolding1998/jarvis-workbench/releases/latest/download/Jarvis-Windows.zip")
        self.assertIn(release["status"], ("pending", "available"))
        self.assertEqual(release["agent_guide"], "https://github.com/Jgolding1998/jarvis-workbench/blob/main/docs/INSTALL_WITH_AGENT.md")
        self.assertEqual(release["feedback"], "https://github.com/Jgolding1998/Jgolding1998/issues/new?template=feedback.yml")
        self.assertNotEqual(release["source_zip"], release["windows_zip"])
        self.assertEqual(release["source_zip"], "https://github.com/Jgolding1998/jarvis-workbench/releases/latest/download/Get-Jarvis-Workbench.zip")

    def test_malformed_catalogs_are_rejected(self):
        def broken(change):
            data = copy.deepcopy(CATALOG)
            change(data)
            return data
        cases = [
            lambda d: d.update(schema="other"),
            lambda d: d["roles"].append(dict(d["roles"][0])),
            lambda d: d["roles"][0].update(features=["microphone"]),
            lambda d: d["existing"][0].update(zero="unknown"),
            lambda d: d["existing"][0]["zero_notes"].append("missing"),
            lambda d: d["providers"]["nvidia"].update(setup_url="http://build.nvidia.com/"),
            lambda d: d["release"].update(status="live"),
            lambda d: d["release"].update(feedback="javascript:alert(1)"),
            lambda d: d["budget"].update(min=1),
            lambda d: d["budget"].update(max=10.5),
            lambda d: d["budget"].update(step=5),
            lambda d: d.pop("budget"),
            lambda d: d["providers"]["openai"].update(limit_url="http://example.org"),
            lambda d: d.update(defaults={"role": "admin", "budget": 0, "existing": "none"}),
            lambda d: d["notes"].update(nvidia_trial="Fast " + chr(0x2014) + " and free"),
        ]
        for index, change in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                quickstart.validate_catalog(broken(change))

    def test_loaded_catalog_is_a_copy(self):
        first = quickstart.load_catalog()
        first["roles"].clear()
        self.assertTrue(quickstart.load_catalog()["roles"])


class ValidationTests(unittest.TestCase):
    def test_budget_is_any_whole_dollar_in_range(self):
        for value in (0, 1, 7, 37, 99, 100, "0", "100", 25.0):
            with self.subTest(value=value):
                self.assertEqual(quickstart.validate_choices({"role": "office", "budget": value, "existing": "none"})["budget"], int(value))

    def test_canonical_choices(self):
        self.assertEqual(quickstart.validate_choices({"role": "study", "budget": "25", "existing": "ollama"}),
                         {"role": "study", "budget": 25, "existing": "ollama"})
        self.assertEqual(quickstart.validate_choices({"existing": "none", "budget": 0, "role": "office"}),
                         {"role": "office", "budget": 0, "existing": "none"})

    def test_each_error_has_fixed_text_and_never_echoes_input(self):
        private = "private-fixture-value"
        cases = [
            (None, quickstart.ERROR_SHAPE), ([], quickstart.ERROR_SHAPE), ("office", quickstart.ERROR_SHAPE),
            ({"role": "office", "budget": 0}, quickstart.ERROR_SHAPE),
            ({"role": "office", "budget": 0, "existing": "none", "key": private}, quickstart.ERROR_SHAPE),
            ({"role": private, "budget": 0, "existing": "none"}, quickstart.ERROR_ROLE),
            ({"role": ["office"], "budget": 0, "existing": "none"}, quickstart.ERROR_ROLE),
            ({"role": "office", "budget": 101, "existing": "none"}, quickstart.ERROR_BUDGET),
            ({"role": "office", "budget": -1, "existing": "none"}, quickstart.ERROR_BUDGET),
            ({"role": "office", "budget": "101", "existing": "none"}, quickstart.ERROR_BUDGET),
            ({"role": "office", "budget": " 5", "existing": "none"}, quickstart.ERROR_BUDGET),
            ({"role": "office", "budget": float("nan"), "existing": "none"}, quickstart.ERROR_BUDGET),
            ({"role": "office", "budget": True, "existing": "none"}, quickstart.ERROR_BUDGET),
            ({"role": "office", "budget": 10.5, "existing": "none"}, quickstart.ERROR_BUDGET),
            ({"role": "office", "budget": "010", "existing": "none"}, quickstart.ERROR_BUDGET),
            ({"role": "office", "budget": None, "existing": "none"}, quickstart.ERROR_BUDGET),
            ({"role": "office", "budget": 0, "existing": private}, quickstart.ERROR_EXISTING),
            ({"role": "office", "budget": 0, "existing": "chatgpt"}, quickstart.ERROR_EXISTING),
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
        self.assertEqual(len(combos), 4 * 6 * 5)
        self.assertEqual(sorted({c["budget"] for c in combos}), [0, 1, 10, 25, 50, 100])
        for choices in combos:
            with self.subTest(choices=choices):
                result = quickstart.recommend(choices)
                self.assertLessEqual({"choices", "provider", "features", "title", "summary", "tradeoffs", "setup_url", "steps"}, set(result))
                self.assertLessEqual(set(result), {"choices", "provider", "model", "features", "title", "summary", "tradeoffs", "setup_url", "steps"})
                self.assertEqual(result["choices"], choices)
                self.assertTrue(result["features"])
                self.assertLessEqual(set(result["features"]), {"camera", "hand"})
                self.assertTrue(result["setup_url"].startswith("https://"))
                self.assertTrue(all(isinstance(item, str) and item for item in result["tradeoffs"] + result["steps"]))
                self.assertEqual(result["tradeoffs"][-1], CATALOG["budget_note"])
                self.assertEqual(result, quickstart.recommend(dict(choices)))

    def test_zero_budget_rules(self):
        self.assertEqual(plan(existing="ollama")["provider"], "ollama")
        for existing in ("none", "nvidia", "openai", "anthropic"):
            self.assertEqual(plan(existing=existing)["provider"], "nvidia", existing)
        joined = " ".join(plan()["tradeoffs"])
        self.assertIn("Rate limits, queues and eligibility", joined)
        self.assertIn("core offline", joined)
        for existing, name in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
            text = " ".join(plan(existing=existing)["tradeoffs"])
            self.assertIn(name + " API requests need available API credits or billing", text)
            self.assertIn("cannot count on future usage", text)
            self.assertIn("At $0 Jarvis does not send paid " + name + " requests", text)
            self.assertIn("NVIDIA trial access or Ollama", text)
            self.assertIn("eligible API credits", text)
            self.assertIn("instead of assuming", text)

    def test_paid_budget_rules(self):
        for budget in (1, 10, 25, 50, 100):
            self.assertEqual(plan(budget=budget, existing="openai")["provider"], "openai")
            self.assertEqual(plan(budget=budget, existing="anthropic")["provider"], "anthropic")
            self.assertEqual(plan(budget=budget, existing="nvidia")["provider"], "nvidia")
            self.assertEqual(plan(budget=budget, existing="ollama")["provider"], "ollama")
            self.assertEqual(plan(budget=budget, existing="none")["provider"], "nvidia")
            self.assertIn("only a hard spend limit", " ".join(plan(budget=budget, existing="openai")["tradeoffs"]))

    def test_local_model_explains_hardware_without_calling_it_slow(self):
        result = plan(existing="ollama")
        self.assertEqual(result["model"], "gemma3:4b")
        text = " ".join(result["tradeoffs"])
        self.assertIn("processor and RAM", text)
        self.assertIn("can feel quick", text)
        self.assertNotIn("model", plan())

    def test_features_and_steps_follow_role(self):
        self.assertEqual(plan(role="developer")["features"], ["hand"])
        self.assertEqual(plan(role="office")["features"], ["camera"])
        self.assertEqual(plan(role="developer")["steps"][-1], CATALOG["steps"]["practice"])
        self.assertEqual(plan(role="office")["steps"][-1], CATALOG["steps"]["dashboard"])
        self.assertEqual(plan()["title"], "Office desk with NVIDIA trial access")

    def test_no_pricing_guarantees_or_free_equals_slow(self):
        text = json.dumps(CATALOG).lower()
        for phrase in ("guarantee", "always free", "unlimited", "free is slow", "free means slow"):
            self.assertNotIn(phrase, text)


@unittest.skipUnless(shutil.which("node"), "Node.js not installed; JavaScript parity check skipped")
class JavaScriptParityTests(unittest.TestCase):
    def run_node(self, script, payload):
        result = subprocess.run(["node", "-e", script, str(SETUP_JS)], input=json.dumps(payload), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_every_combination_matches(self):
        cases = list(quickstart.all_choices())
        script = ("const api=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
                  "const {catalog,cases}=JSON.parse(s);process.stdout.write(JSON.stringify(cases.map(c=>api.recommend(c,catalog))));});")
        plans = self.run_node(script, {"catalog": CATALOG, "cases": cases})
        for choices, js_plan in zip(cases, plans):
            self.assertEqual(js_plan, quickstart.recommend(choices), choices)

    def test_errors_match(self):
        cases = [None, [], {"role": "office", "budget": 0}, {"role": "office", "budget": 0, "existing": "none", "x": 1},
                 {"role": "admin", "budget": 0, "existing": "none"}, {"role": "office", "budget": 5, "existing": "none"},
                 {"role": "office", "budget": True, "existing": "none"}, {"role": "office", "budget": 10.5, "existing": "none"},
                 {"role": "office", "budget": "010", "existing": "none"}, {"role": "office", "budget": 0, "existing": "gpt"},
                 {"role": "office", "budget": 101, "existing": "none"}, {"role": "office", "budget": -1, "existing": "none"},
                 {"role": "office", "budget": 1, "existing": "none"}, {"role": "office", "budget": 100, "existing": "openai"},
                 {"role": "office", "budget": "37", "existing": "none"}, {"role": "office", "budget": "1e2", "existing": "none"},
                 {"role": "office", "budget": "50", "existing": "anthropic"}]
        script = ("const api=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
                  "const {catalog,cases}=JSON.parse(s);process.stdout.write(JSON.stringify(cases.map(c=>{try{return {ok:api.validateChoices(c,catalog)}}"
                  "catch(e){return {error:e.message}}})));});")
        results = self.run_node(script, {"catalog": CATALOG, "cases": cases})
        for data, js in zip(cases, results):
            try:
                expected = {"ok": quickstart.validate_choices(data)}
            except ValueError as error:
                expected = {"error": str(error)}
            self.assertEqual(js, expected, data)

    def test_prototype_keys_are_rejected(self):
        script = ("const api=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
                  "const {catalog,text}=JSON.parse(s);let r;try{api.validateChoices(JSON.parse(text),catalog);r='accepted'}catch(e){r=e.message}"
                  "process.stdout.write(JSON.stringify([r,({}).polluted===undefined]));});")
        text = '{"__proto__": {"polluted": true}, "role": "office", "budget": 0}'
        self.assertEqual(self.run_node(script, {"catalog": CATALOG, "text": text}), [quickstart.ERROR_SHAPE, True])

    def test_allowance_reader_keeps_only_valid_service_data(self):
        good = {"month": "2026-10", "limit_usd": 25, "reserved_usd": 0.12, "remaining_usd": 24.88, "paid_requests": 12,
                "reserve_per_request_usd": 0.01, "kind": "local_allowance", "provider_limit_required": True,
                "pricing_checked": "2026-10-28", "valid_until": "2026-11-27"}
        cases = [good, None, {**good, "kind": "invoice"}, {**good, "month": "2026-13"}, {**good, "limit_usd": -1},
                 {**good, "paid_requests": 1.5}, {**good, "reserved_usd": "0.1"}, {**good, "pricing_checked": "soon"},
                 {"available": False, "kind": "local_allowance", "limit_usd": 25, "message": "private-canary"},
                 {"available": False, "kind": "other", "message": "private-canary"}]
        script = ("const api=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>"
                  "process.stdout.write(JSON.stringify(JSON.parse(s).map(api.readBudget))));")
        result = self.run_node(script, cases)
        self.assertEqual(result[8], {"available": False})
        self.assertIsNone(result[9])
        self.assertEqual(result[0], {"available": True, "month": "2026-10", "limit": 25, "reserved": 0.12, "remaining": 24.88, "paidRequests": 12, "perRequest": 0.01,
                                     "providerLimitRequired": True, "pricingChecked": "2026-10-28", "validUntil": "2026-11-27"})
        self.assertEqual(result[1:7], [None] * 6)
        self.assertEqual(result[7]["pricingChecked"], "")

    def test_link_state_and_launch_guards(self):
        script = ("const api=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const {catalog}=JSON.parse(s);"
                  "const q=t=>{try{return api.choicesFromQuery(t,catalog)}catch(e){return e.message}};"
                  "process.stdout.write(JSON.stringify({"
                  "query:[q(''),q('?role=study&budget=25&existing=ollama'),q('?role=x&budget=0&existing=none'),q('?role=office')],"
                  "launch:['http://127.0.0.1:8765/','http://127.0.0.1:0/','http://127.0.0.1:8765/x','https://127.0.0.1:8765/','http://127.0.0.1.evil:80/','http://evil/',5].map(api.isSafeLaunchUrl),"
                  "loop:[[{protocol:'http:',hostname:'127.0.0.1'},'auto'],[{protocol:'http:',hostname:'127.0.0.1'},'hosted'],[{protocol:'http:',hostname:'localhost'},'auto'],[{protocol:'https:',hostname:'127.0.0.1'},'auto']].map(a=>api.isLoopbackPage(a[0],a[1])),"
                  "state:[api.readQuickstartState({local:false},catalog),api.readQuickstartState({local:true,choices:{role:'bad'},credentials:{nvidia:'yes',openai:true},install:{state:'weird'}},catalog),"
                  "api.readQuickstartState({local:true,choices:{role:'office',budget:10,existing:'none'},install:{state:'ready',message:'x'}},catalog)]}));});")
        result = self.run_node(script, {"catalog": CATALOG})
        self.assertEqual(result["query"], [None, {"role": "study", "budget": 25, "existing": "ollama"}, quickstart.ERROR_ROLE, quickstart.ERROR_SHAPE])
        self.assertEqual(result["launch"], [True, False, False, False, False, False, False])
        self.assertEqual(result["loop"], [True, False, False, False])
        self.assertIsNone(result["state"][0])
        self.assertEqual(result["state"][1], {"local": True, "choices": None, "credentials": {"nvidia": False, "openai": True, "anthropic": False}, "install": "idle", "budget": None})
        self.assertEqual(result["state"][2]["choices"], {"role": "office", "budget": 10, "existing": "none"})
        self.assertEqual(result["state"][2]["install"], "ready")


class StaticSafetyTests(unittest.TestCase):
    def test_setup_script_only_fetches_contracted_same_origin_paths(self):
        source = SETUP_JS.read_text(encoding="utf-8")
        targets = set(re.findall(r'localJson\("([^"]+)"', source)) | set(re.findall(r'fetch\("([^"]+)"', source))
        allowed = {"/api/session", "/api/quickstart", "/api/quickstart/save", "/api/quickstart/key", "/api/quickstart/key/delete",
                   "/api/quickstart/test", "/api/quickstart/chat", "/api/quickstart/install", "/api/quickstart/launch", "/api/setup/exit",
                   "quickstart-catalog.json"}
        self.assertLessEqual(targets, allowed)
        self.assertGreaterEqual(targets, allowed)
        for pattern in (r"localStorage", r"sessionStorage", r"console\.", r"innerHTML", r"eval\(", r"new Function", r"document\.cookie"):
            self.assertNotRegex(source, pattern)
        self.assertNotRegex(source, r'fetch\(\s*["\'`]https?:')

    def test_public_pages_have_no_key_field(self):
        for path in (ROOT / "web" / "setup.html", ROOT / "website" / "index.html", ROOT / "website" / "privacy.html"):
            html = path.read_text(encoding="utf-8")
            self.assertNotIn('type="password"', html, path.name)
            self.assertIn('http-equiv="Content-Security-Policy"', html, path.name)
            self.assertNotRegex(html, r'<script[^>]*>\s*[^<\s]', path.name)

    def test_owned_files_have_no_em_dashes(self):
        for path in OWNED_TEXT:
            self.assertNotIn(chr(0x2014), path.read_text(encoding="utf-8"), path.name)

    def test_landing_page_keeps_hand_guide_and_practice_and_links_help(self):
        html = (ROOT / "website" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('class="gesture-card"'), 4)
        for marker in ('id="g-hand-open"', 'id="pad-stage"', 'href="setup.html"', CATALOG["release"]["feedback"], CATALOG["release"]["agent_guide"]):
            self.assertIn(marker, html)
        for removed in ('id="needs"', 'class="faq', 'budget-receipt'):
            self.assertNotIn(removed, html)

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
        result = subprocess.run(["node", str(ROOT / "tests" / "browser_quickstart.cjs")], capture_output=True, text=True, timeout=300)
        if result.stdout.startswith("SKIP"):
            self.skipTest(result.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-4000:])
        report = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertEqual(report["failures"], [])
        self.assertGreater(report["passed"], 90)


if __name__ == "__main__":
    unittest.main()
