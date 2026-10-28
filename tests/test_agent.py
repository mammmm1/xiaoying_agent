import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from content_planner.agent import run_agent
from content_planner.common import canonical, read_json, validate_request
from content_planner.models import MockModel, ModelError
from content_planner.store import Store
from content_planner.tools import Tools, ROOT

TEST_TMP = ROOT / "runtime" / "test-temp"
TEST_TMP.mkdir(parents=True, exist_ok=True)


def request(budget=10, **changes):
    return {"request_id": "test-001", "theme": "字幕剪辑产品教程", "budget": budget,
            "start_at": "2026-11-01T09:00:00+08:00", "deadline": "2026-11-01T13:00:00+08:00", **changes}


class ScriptedModel:
    """Adversarial model outputs for boundary tests; not decision evidence."""
    mode, name = "mock", "adversarial-test-double"

    def __init__(self, actions):
        self.actions = iter(actions)

    def decide(self, messages, tools):
        name, args = next(self.actions)
        return {"role": "assistant", "content": "", "tool_calls": [{"id": "test-call", "type": "function",
                "function": {"name": name, "arguments": canonical(args)}}]}, {}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def run_case(self, req=None, **kwargs):
        return run_agent(req or request(), MockModel(), self.store, **kwargs)

    def test_normal_complete_and_plan_contents(self):
        result = self.run_case(request(20))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["plan"]["estimate"]["total_cost"], 12)
        self.assertEqual(result["plan"]["estimate"]["total_minutes"], 106)
        self.assertTrue(result["plan"]["script"]["scenes"])
        self.assertEqual(result["plan"]["tasks"][2]["status"], "planned")
        self.assertIn("模拟", result["evidence_note"])

    def test_replanning_uses_observation_and_preserves_script(self):
        result = self.run_case()
        estimates = [t["observation"]["estimate"] for t in result["trace"] if t.get("tool") == "estimate_plan"]
        self.assertEqual([e["total_cost"] for e in estimates], [12, 8])
        self.assertFalse(estimates[0]["feasible"])
        self.assertTrue(estimates[1]["feasible"])
        self.assertEqual(sum(t.get("tool") == "generate_script" for t in result["trace"]), 1)
        self.assertEqual(result["trace"][2]["next_action"], "estimate_plan")

    def test_missing_budget_and_deadline_no_api_call(self):
        for key in ("budget", "deadline"):
            req = request()
            del req[key]
            result = self.run_case(req)
            self.assertEqual(result["status"], "needs_input")
            self.assertEqual(result["model_calls"], 0)
            self.assertFalse(result["saved"])

    def test_invalid_money_and_dates(self):
        for value in (True, -1, float("nan"), float("inf"), "10"):
            self.assertTrue(validate_request(request(value)))
        self.assertTrue(validate_request(request(start_at="2026-11-01T09:00:00")))
        self.assertTrue(validate_request(request(deadline="2026-10-01T09:00:00+08:00")))

    def test_tool_failure_retry_retains_retrieval(self):
        result = self.run_case(failures={"generate_script": 1})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(sum(t.get("tool") == "search_assets" for t in result["trace"]), 1)
        script_calls = [t for t in result["trace"] if t.get("tool") == "generate_script"]
        self.assertFalse(script_calls[0]["observation"]["ok"])
        self.assertTrue(script_calls[1]["observation"]["ok"])
        self.assertEqual(result["steps"][1]["status"], "failed")

    def test_save_failure_never_reported_as_saved(self):
        result = self.run_case(failures={"save_plan": 2})
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["saved"])
        self.assertIsNone(result["plan"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM plans").fetchone()[0], 0)

    def test_real_storage_error_is_observable(self):
        with patch.object(self.store, "save", side_effect=sqlite3.OperationalError("disk full")):
            result = self.run_case()
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["saved"])
        errors = [t["observation"]["error"]["code"] for t in result["trace"] if not t["observation"]["ok"]]
        self.assertIn("STORAGE_ERROR", errors)

    def test_over_budget_stops_and_reports_gap(self):
        result = self.run_case(request(4))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["latest_estimate"]["budget_gap"], 1)
        self.assertFalse(result["saved"])

    def test_deadline_replanning(self):
        result = self.run_case(request(20, deadline="2026-11-01T09:45:00+08:00"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["plan"]["estimate"]["tier"], "basic")
        self.assertEqual(result["plan"]["estimate"]["finish_at"], "2026-11-01T09:36:00+08:00")

    def test_impossible_deadline(self):
        result = self.run_case(request(20, deadline="2026-11-01T09:30:00+08:00"))
        self.assertFalse(result["saved"])
        self.assertEqual(result["latest_estimate"]["deadline_gap_minutes"], 6)

    def test_no_assets_stops(self):
        result = self.run_case(request(theme="量子计算芯片实验"))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["saved"])

    def test_repeated_run_has_one_plan_and_no_second_model_call(self):
        first, second = self.run_case(), self.run_case()
        self.assertEqual(first["plan"]["plan_id"], second["plan"]["plan_id"])
        self.assertEqual(second["model_calls"], 0)
        self.assertTrue(second["replayed"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM plans").fetchone()[0], 1)

    def test_duplicate_across_database_reopen(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            path = Path(folder) / "db.sqlite"
            with_store = Store(path)
            first = run_agent(request(), MockModel(), with_store)
            with_store.close()
            reopened = Store(path)
            try:
                second = run_agent(request(), MockModel(), reopened)
                self.assertEqual(first["plan"], second["plan"])
                self.assertEqual(second["model_calls"], 0)
            finally:
                reopened.close()

    def test_same_id_changed_input_rejected_without_returning_other_plan(self):
        self.run_case()
        result = self.run_case(request(11))
        self.assertEqual(result["error_code"], "STATE_CONFLICT")
        self.assertIsNone(result["plan"])

    def test_step_limit_and_resume_reuses_script(self):
        first = self.run_case(max_steps=2)
        self.assertEqual(first["error_code"], "STEP_LIMIT")
        self.assertLessEqual(first["tool_calls"], 2)
        second = self.run_case()
        self.assertEqual(second["status"], "completed")
        self.assertEqual(sum(t["tool"] == "generate_script" for t in second["steps"]), 1)
        self.assertEqual(second["trace"][0]["tool"], "estimate_plan")

    def test_failed_save_resume_does_not_regenerate_script(self):
        first = self.run_case(failures={"save_plan": 2})
        second = self.run_case()
        self.assertFalse(first["saved"])
        self.assertTrue(second["saved"])
        self.assertEqual(second["trace"][0]["tool"], "save_plan")
        self.assertEqual(sum(t["tool"] == "generate_script" for t in second["steps"]), 1)

    def test_model_cannot_claim_complete_without_save(self):
        fake = ScriptedModel([("finish", {"status": "completed", "summary": "已完成", "reason": "伪造成功"})])
        result = run_agent(request(), fake, self.store, max_steps=1)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["trace"][0]["observation"]["error"]["code"], "NOT_SAVED")

    def test_model_api_failure_is_not_silently_mocked(self):
        fake = MockModel()
        fake.mode = "deepseek"
        with patch.object(fake, "decide", side_effect=ModelError("API HTTP 401")):
            result = run_agent(request(), fake, self.store)
        self.assertEqual(result["error_code"], "MODEL_ERROR")
        self.assertFalse(result["mock"])
        self.assertFalse(result["saved"])

    def test_mock_and_real_evidence_are_not_mixed(self):
        self.run_case()
        fake = MockModel()
        fake.mode = "deepseek"
        result = run_agent(request(), fake, self.store)
        self.assertEqual(result["error_code"], "PROVENANCE_CONFLICT")
        self.assertFalse(result["saved"])


class ToolBoundaries(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.req = request()
        self.state = self.store.begin(self.req)
        self.tools = Tools(self.req, self.state, self.store)

    def call(self, name, **args):
        return self.tools.call(name, {**args, "reason": "测试边界"})

    def prepare(self):
        self.call("search_assets", query="字幕")
        self.call("generate_script", asset_ids=["A01"], format="tutorial")

    def test_save_blocks_over_budget_even_if_model_tries(self):
        self.prepare()
        estimate = self.call("estimate_plan", tier="premium")["estimate"]
        result = self.call("save_plan", estimate_id=estimate["estimate_id"])
        self.assertEqual(result["error"]["code"], "CONSTRAINT_VIOLATION")
        self.assertIsNone(self.store.saved(self.req["request_id"]))

    def test_stale_estimate_cannot_save_changed_script(self):
        self.prepare()
        estimate = self.call("estimate_plan", tier="basic")["estimate"]
        self.call("generate_script", asset_ids=["A03"], format="story")
        result = self.call("save_plan", estimate_id=estimate["estimate_id"])
        self.assertEqual(result["error"]["code"], "STALE_ESTIMATE")

    def test_unknown_tool_and_forged_cost_rejected(self):
        self.assertEqual(self.call("publish")["error"]["code"], "UNKNOWN_TOOL")
        result = self.call("estimate_plan", tier="basic", total_cost=0)
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENTS")

    def test_unretrieved_asset_rejected(self):
        result = self.call("generate_script", asset_ids=["A99"], format="tutorial")
        self.assertEqual(result["error"]["code"], "DEPENDENCY_MISSING")

    def test_same_script_is_cached(self):
        self.prepare()
        result = self.call("generate_script", asset_ids=["A01"], format="tutorial")
        self.assertTrue(result["cached"])

    def test_save_is_idempotent_at_storage_boundary(self):
        self.prepare()
        estimate = self.call("estimate_plan", tier="standard")["estimate"]
        first = self.call("save_plan", estimate_id=estimate["estimate_id"])
        second = self.call("save_plan", estimate_id=estimate["estimate_id"])
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM plans").fetchone()[0], 1)

    def test_data_change_rejects_checkpoint(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            assets = read_json(ROOT / "data/assets.json")
            assets[0]["facts"] = ["更新后的合成事实"]
            Path(folder, "assets.json").write_text(canonical(assets), encoding="utf-8")
            Path(folder, "rates.json").write_bytes((ROOT / "data/rates.json").read_bytes())
            with self.assertRaisesRegex(ValueError, "DATA_CHANGED"):
                Tools(self.req, self.state, self.store, data_dir=folder)


if __name__ == "__main__":
    unittest.main()
