"""Regression coverage for the actual E01/E02 failures and E03 guard."""
import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from content_planner.agent import run_agent
from content_planner.common import read_json
from content_planner.models import MockModel
from content_planner.store import Store
from content_planner.tools import Tools


class DataValidationTests(unittest.TestCase):
    def setUp(self):
        temp = ROOT / "runtime" / "test-temp"
        temp.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=temp)
        self.addCleanup(self.folder.cleanup)
        self.data = Path(self.folder.name)
        for name in ("assets.json", "rates.json"):
            shutil.copyfile(ROOT / "data" / name, self.data / name)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.req = read_json(ROOT / "examples/inputs/replan.json")
        self.state = self.store.begin(self.req)
        self.tools = Tools(self.req, self.state, self.store, data_dir=self.data)

    def call(self, name, **args):
        return self.tools.call(name, {**args, "reason": "资料异常回归"})

    def prepare(self):
        self.assertTrue(self.call("search_assets", query="字幕")["ok"])
        self.assertTrue(self.call("generate_script", asset_ids=["A01"], format="tutorial")["ok"])

    def fixture(self, case, name):
        shutil.copyfile(ROOT / "data/debugging" / case / name, self.data / name)

    def test_e01_invalid_rate_is_observed_and_script_is_preserved(self):
        self.fixture("E01", "rates.json")
        result = run_agent(self.req, MockModel(), self.store, data_dir=self.data)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["saved"])
        failed = [s for s in result["steps"] if not s["result"]["ok"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["tool"], "estimate_plan")
        self.assertEqual(failed[0]["result"]["error"]["code"], "INVALID_RATES")
        self.assertFalse(failed[0]["result"]["error"]["retryable"])
        self.assertIn("production.standard.cost", result["summary"])
        self.assertEqual(sum(s["tool"] == "generate_script" and s["result"]["ok"] for s in result["steps"]), 1)

    def test_e02_missing_facts_is_observed_without_partial_script(self):
        self.fixture("E02", "assets.json")
        result = run_agent(self.req, MockModel(), self.store, data_dir=self.data)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["saved"])
        self.assertIn("A01", result["summary"])
        self.assertIn("facts", result["summary"])
        failed = [s for s in result["steps"] if not s["result"]["ok"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["result"]["error"]["code"], "INVALID_ASSET_DATA")
        saved_state = self.store.begin(self.req)
        self.assertTrue(saved_state["retrieved"])
        self.assertEqual(saved_state["scripts"], {})

    def test_rate_rejects_text_boolean_negative_and_nonfinite_amounts(self):
        self.prepare()
        for field in ("cost", "minutes"):
            for value in ("5", True, -1, float("nan"), float("inf"), None, 10 ** 400):
                with self.subTest(field=field, kind=type(value).__name__):
                    self.tools.rates = read_json(self.data / "rates.json")
                    self.tools.rates["production"]["standard"][field] = value
                    result = self.call("estimate_plan", tier="standard")
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"]["code"], "INVALID_RATES")
                    self.assertFalse(result["error"]["retryable"])

    def test_missing_rate_section_returns_data_error(self):
        self.prepare()
        for name in ("research", "script", "save", "production", "currency", "version"):
            with self.subTest(section=name):
                self.tools.rates = read_json(self.data / "rates.json")
                del self.tools.rates[name]
                result = self.call("estimate_plan", tier="standard")
                self.assertEqual(result["error"]["code"], "INVALID_RATES")
        self.tools.rates = read_json(self.data / "rates.json")
        del self.tools.rates["production"]["standard"]
        self.assertEqual(self.call("estimate_plan", tier="standard")["error"]["code"], "INVALID_RATES")

    def test_duration_and_cost_overflow_are_clear_errors(self):
        self.prepare()
        self.tools.rates["production"]["standard"]["minutes"] = 1e308
        self.assertEqual(self.call("estimate_plan", tier="standard")["error"]["code"], "INVALID_RATES")
        self.tools.rates = read_json(self.data / "rates.json")
        for part in (self.tools.rates["research"], self.tools.rates["script"], self.tools.rates["production"]["standard"]):
            part["cost"] = 1e308
        self.assertEqual(self.call("estimate_plan", tier="standard")["error"]["code"], "INVALID_RATES")

    def test_save_revalidates_rates_and_never_commits_bad_data(self):
        self.prepare()
        estimate = self.call("estimate_plan", tier="standard")["estimate"]
        self.tools.rates["production"]["standard"]["cost"] = "5"
        result = self.call("save_plan", estimate_id=estimate["estimate_id"])
        self.assertEqual(result["error"]["code"], "INVALID_RATES")
        self.assertIsNone(self.store.saved(self.req["request_id"]))

    def test_string_or_empty_facts_cannot_be_silently_joined(self):
        self.call("search_assets", query="字幕")
        for facts in ("支持字幕", [], [1], [""], None):
            with self.subTest(facts=repr(facts)):
                self.state["retrieved"]["A01"]["facts"] = facts
                result = self.call("generate_script", asset_ids=["A01"], format="tutorial")
                self.assertEqual(result["error"]["code"], "INVALID_ASSET_DATA")
                self.assertEqual(self.state["scripts"], {})

    def test_valid_asset_can_be_selected_after_bad_asset_is_observed(self):
        self.call("search_assets", query="字幕")
        self.state["retrieved"]["A01"].pop("facts")
        self.assertFalse(self.call("generate_script", asset_ids=["A01"], format="tutorial")["ok"])
        result = self.call("generate_script", asset_ids=["A03"], format="tutorial")
        self.assertTrue(result["ok"])
        self.assertEqual(result["script"]["asset_ids"], ["A03"])

    def test_invalid_metadata_or_duplicate_ids_is_rejected(self):
        original = copy.deepcopy(self.tools.assets)
        variants = [{}, ["not an asset"], [*original, copy.deepcopy(original[0])]]
        bad = copy.deepcopy(original)
        bad[0]["tags"] = "字幕"
        variants.append(bad)
        for assets in variants:
            self.tools.assets = assets
            result = self.call("search_assets", query="字幕")
            self.assertEqual(result["error"]["code"], "INVALID_ASSET_DATA")
        self.assertEqual(self.state["retrieved"], {})

    def test_e03_new_request_id_allows_new_budget_and_preserves_first_plan(self):
        first = run_agent(self.req, MockModel(), self.store, data_dir=self.data)
        self.assertTrue(first["saved"])
        changed = {**self.req, "budget": 11}
        conflict = run_agent(changed, MockModel(), self.store, data_dir=self.data)
        self.assertEqual(conflict["error_code"], "STATE_CONFLICT")
        changed["request_id"] = "new-budget-request"
        second = run_agent(changed, MockModel(), self.store, data_dir=self.data)
        self.assertTrue(second["saved"])
        self.assertNotEqual(first["plan"]["plan_id"], second["plan"]["plan_id"])
        self.assertEqual(self.store.saved(self.req["request_id"]), first["plan"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM plans").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
