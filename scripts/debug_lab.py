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
              "request": dict(req), "diagnosis_status": "awaiting_candidate_analysis"}
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
        row = store.db.execute("SELECT state FROM requests WHERE id=?", (req["request_id"],)).fetchone()
        record["checkpoint_steps"] = json.loads(row[0]).get("tool_steps", []) if row else []
        store.close()
    target = output_dir / (case + ".json")
    write_json(target, record)
    detail = record.get("exception", {}).get("type") or record.get("error_code") or ""
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
    for case in TITLES if args.case == "all" else [args.case]:
        reproduce(case, args.output_dir.resolve())
    print("演练已复现；异常诊断与修复尚未完成，详见 docs/08-debugging-lab.md。")
