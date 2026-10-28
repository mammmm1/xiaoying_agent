"""Generate actual run reports and assertions; never fabricates live traces."""
import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from content_planner.agent import run_agent
from content_planner.cli import load_env
from content_planner.common import read_json, write_json
from content_planner.models import DeepSeekModel, MockModel, ModelError
from content_planner.store import Store


def check_report(name, req, report, max_steps=12, expected="completed", plan_count=None):
    allowed = ("incomplete", "needs_input") if name == "no_assets" else (expected,)
    checks = {"expected_status": report["status"] in allowed,
              "bounded": report["model_calls"] <= max_steps and report["tool_calls"] <= max_steps,
              "no_false_completion": report["status"] != "completed" or report["saved"]}
    events = report["trace"]
    if report["saved"]:
        est = report["plan"]["estimate"]
        checks.update({"within_budget": est["total_cost"] <= req["budget"],
                       "within_deadline": est["within_deadline"],
                       "sum_matches": sum(p["cost"] for p in est["breakdown"]) == est["total_cost"]})
    if name == "replan":
        estimates = [t["observation"]["estimate"] for t in events if t.get("tool") == "estimate_plan" and t["observation"]["ok"]]
        checks["observed_replanning"] = len(estimates) >= 2 and not estimates[0]["feasible"] and estimates[-1]["feasible"]
    if name == "tool_failure":
        checks["failure_observed"] = any(not t.get("observation", {}).get("ok", True) for t in events)
        checks["retrieval_preserved"] = sum(t.get("tool") == "search_assets" and t["observation"]["ok"] for t in events) == 1
    if name == "save_failure":
        checks["actual_save_failure"] = any(t.get("tool") == "save_plan" and not t["observation"]["ok"] for t in events)
        checks["not_saved"] = not report["saved"]
    if name in ("over_budget", "deadline_impossible"):
        estimate = report.get("latest_estimate") or {}
        gap = "budget_gap" if name == "over_budget" else "deadline_gap_minutes"
        checks["observed_constraint_gap"] = estimate.get(gap, 0) > 0
        checks["not_saved"] = not report["saved"]
    if name == "no_assets":
        searches = [t["observation"] for t in events if t.get("tool") == "search_assets" and t["observation"]["ok"]]
        checks["empty_material_observed"] = bool(searches) and all(o["count"] == 0 for o in searches)
        checks["no_fabricated_script_or_save"] = not report["saved"] and not any(t.get("tool") in ("generate_script", "save_plan") for t in events)
        checks["explicit_material_gap"] = any(word in report["summary"] for word in ("素材", "资料", "material", "asset"))
    if name == "duplicate":
        checks["one_saved_plan"] = plan_count == 1
        checks["no_new_model_call"] = report["model_calls"] == 0
    if name == "step_limit":
        checks["explicit_step_limit"] = report.get("error_code") == "STEP_LIMIT"
    if name == "resume":
        checks["script_reused"] = sum(s["tool"] == "generate_script" and s["result"]["ok"] for s in report["steps"]) == 1
    return checks


def evaluate(provider, out_dir):
    load_env(ROOT / ".env")
    make_model = DeepSeekModel if provider == "deepseek" else MockModel
    # Validate real API configuration before producing any evidence files.
    make_model()
    cases = [
        ("normal", {}, "completed"),
        ("replan", {}, "completed"),
        ("missing_budget", {}, "needs_input"),
        ("missing_deadline", {}, "needs_input"),
        ("tool_failure", {"generate_script": 1}, "completed"),
        ("save_failure", {"save_plan": 30}, "incomplete"),
        ("over_budget", {}, "incomplete"),
        ("deadline_replan", {}, "completed"),
        ("deadline_impossible", {}, "incomplete"),
        ("no_assets", {}, "incomplete"),
    ]
    rows = []
    temp_root = ROOT / "runtime" / "eval-temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temp_root) as folder:
        store = Store(Path(folder) / "evaluation.sqlite")
        try:
            def record(name, req, failures=None, max_steps=12, expected="completed"):
                report = run_agent(req, make_model(), store, failures=failures, max_steps=max_steps)
                count = store.db.execute("SELECT count(*) FROM plans WHERE request_id=?", (req["request_id"],)).fetchone()[0] if name == "duplicate" else None
                checks = check_report(name, req, report, max_steps, expected, count)
                write_json(out_dir / f"{name}.json", report)
                rows.append({"case": name, "report": f"{name}.json", "status": report["status"],
                             "checks": checks, "passed": all(checks.values())})
                return report

            for name, failures, expected in cases:
                record(name, read_json(ROOT / "examples" / "inputs" / f"{name}.json"), failures, expected=expected)
            record("duplicate", read_json(ROOT / "examples/inputs/normal.json"))
            req = {**read_json(ROOT / "examples/inputs/replan.json"), "request_id": "resume-001"}
            record("step_limit", req, max_steps=2, expected="incomplete")
            record("resume", req)
        finally:
            store.close()
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "provider": provider,
               "mock": provider == "mock", "cases": rows, "passed": sum(r["passed"] for r in rows), "total": len(rows),
               "note": "离线流程证据，不证明真实模型效果" if provider == "mock" else "真实模型运行；请人工复核决策和 Trace"}
    write_json(out_dir / "summary.json", summary)
    print(f"{provider}: {summary['passed']}/{summary['total']} scenarios passed; reports: {out_dir}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["mock", "deepseek"], default="mock")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    try:
        raise SystemExit(evaluate(args.provider, args.output_dir or ROOT / "examples" / ("offline" if args.provider == "mock" else "live")))
    except ModelError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
