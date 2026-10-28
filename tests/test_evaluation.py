import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate import check_report


class EvaluationTests(unittest.TestCase):
    def test_empty_material_can_request_input_but_must_have_evidence(self):
        report = {"status": "needs_input", "saved": False, "model_calls": 2, "tool_calls": 2,
                  "summary": "没有素材，请补充资料", "trace": [
                      {"tool": "search_assets", "observation": {"ok": True, "count": 0}},
                      {"tool": "finish", "observation": {"ok": True}}]}
        self.assertTrue(all(check_report("no_assets", {}, report, expected="incomplete").values()))
        report["trace"] = []
        self.assertFalse(all(check_report("no_assets", {}, report, expected="incomplete").values()))

    def test_network_failure_cannot_pass_negative_business_cases(self):
        report = {"status": "incomplete", "saved": False, "model_calls": 1, "tool_calls": 0,
                  "summary": "网络失败", "trace": [{"event": "model_error"}], "error_code": "MODEL_ERROR"}
        for name in ("save_failure", "over_budget", "deadline_impossible", "no_assets", "step_limit"):
            with self.subTest(name=name):
                self.assertFalse(all(check_report(name, {}, report, expected="incomplete").values()))


if __name__ == "__main__":
    unittest.main()
