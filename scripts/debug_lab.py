"""Reproduce isolated abnormal cases for candidate diagnosis. No API calls."""
import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from content_planner.agent import run_agent
from content_planner.common import read_json, write_json
from content_planner.models import MockModel
from content_planner.store import Store

TITLES = {"E01": "外部费率表接入", "E02": "素材库更新后生成脚本", "E03": "相同请求编号修改预算后再次执行"}


class RecordingMock(MockModel):
    def __init__(self):
        super().__init__()
        self.actions = []

    def decide(self, messages, tools):
        message, metadata = super().decide(messages, tools)
        for call in message.get("tool_calls", []):
            self.actions.append({"tool": call["function"]["name"],
                                 "arguments": json.loads(call["function"]["arguments"])})
        return message, metadata


def relative_frame(frame):
    path = Path(frame.filename)
    try:
        name = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        name = path.name
    return {"file": name, "line": frame.lineno, "function": frame.name}


def reproduce(case, output_dir):
    session = ROOT / "runtime" / "debugging" / (case + "-" + uuid4().hex[:8])
    data = session / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name in ("assets.json", "rates.json"):
        override = ROOT / "data" / "debugging" / case / name
        source = override if override.exists() else ROOT / "data" / name
        (data / name).write_bytes(source.read_bytes())
    req = read_json(ROOT / "examples/inputs/replan.json")
    req["request_id"] = "debug-" + case
    model = RecordingMock()
    store = Store(session / "state.sqlite")
    record = {"case": case, "title": TITLES[case], "generated_at": datetime.now(timezone.utc).isoformat(),
              "evidence_type": "offline_debugging_exercise", "mock": True,
              "purpose": "供候选人定位分析的异常演练；不计入正式验收通过率，不证明真实模型效果。",
              "request": dict(req), "diagnosis_status": "ai_analysis_completed"}
    try:
        if case == "E03":
            record["first_run"] = run_agent(req, model, store, data_dir=data)
            req = {**req, "budget": 11}
            record["request"] = req
        record["actual_report"] = run_agent(req, model, store, data_dir=data)
        record["outcome"] = record["actual_report"]["status"]
        record["error_code"] = record["actual_report"].get("error_code")
    except Exception as exc:
        record["outcome"] = "unhandled_exception"
        record["exception"] = {"type": type(exc).__name__, "message": str(exc),
                               "frames": [relative_frame(frame) for frame in traceback.extract_tb(exc.__traceback__)]}
    finally:
        record["selected_actions"] = model.actions
        saved = store.saved(req["request_id"])
        record["database_has_plan"] = saved is not None
        record["database_plan_id"] = saved["plan_id"] if saved else None
        record["database_plan_count"] = store.db.execute("SELECT count(*) FROM plans WHERE request_id=?", (req["request_id"],)).fetchone()[0]
        row = store.db.execute("SELECT state FROM requests WHERE id=?", (req["request_id"],)).fetchone()
        record["checkpoint_steps"] = json.loads(row[0]).get("tool_steps", []) if row else []
        store.close()
    if case in ("E01", "E02"):
        report = record.get("actual_report", {})
        code = "INVALID_RATES" if case == "E01" else "INVALID_ASSET_DATA"
        failures = [step for step in report.get("steps", []) if not step["result"]["ok"]]
        checks = {"no_unhandled_exception": "exception" not in record,
                  "explicit_incomplete": record["outcome"] == "incomplete",
                  "not_saved": not record["database_has_plan"],
                  "actual_data_error": any(s["result"]["error"]["code"] == code for s in failures),
                  "successful_work_retained": bool(record["checkpoint_steps"]),
                  "no_unchanged_data_retry": len(failures) == 1}
    else:
        checks = {"no_unhandled_exception": "exception" not in record,
                  "first_run_saved": record.get("first_run", {}).get("saved") is True,
                  "changed_request_rejected": record.get("error_code") == "STATE_CONFLICT",
                  "original_plan_preserved": record.get("database_plan_id") == record.get("first_run", {}).get("plan", {}).get("plan_id"),
                  "one_plan": record["database_plan_count"] == 1}
    record["regression_checks"] = checks
    record["regression_passed"] = all(checks.values())
    target = output_dir / (case + ".json")
    write_json(target, record)
    errors = [s["result"]["error"]["code"] for s in record.get("actual_report", {}).get("steps", []) if not s["result"]["ok"]]
    detail = record.get("exception", {}).get("type") or record.get("error_code") or ",".join(errors)
    print(f"{case} | {TITLES[case]} | {record['outcome']} | {detail}")
    print("  实际报告：" + target.relative_to(ROOT).as_posix() if target.is_relative_to(ROOT) else "  实际报告已写入指定目录")
    return record


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[*TITLES, "all"], default="all")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runtime" / "debugging" / "reports")
    args = parser.parse_args()
    results = [reproduce(case, args.output_dir.resolve()) for case in (TITLES if args.case == "all" else [args.case])]
    passed = sum(r["regression_passed"] for r in results)
    print(f"异常回归 {passed}/{len(results)} 通过；分析与修复见 docs/09-debugging-resolution.md。")
    raise SystemExit(0 if passed == len(results) else 1)
