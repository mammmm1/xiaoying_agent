"""Human-readable CLI demo for reviewing the same agent and business tools."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from content_planner.agent import run_agent
from content_planner.cli import load_env
from content_planner.common import read_json, write_json
from content_planner.models import DeepSeekModel, MockModel, ModelError
from content_planner.store import Store

CASES = {
    "1": ("normal", "正常完成", {}),
    "2": ("replan", "超预算后重规划：12 → 8", {}),
    "3": ("tool_failure", "工具失败后调整，保留成功结果", {"generate_script": 1}),
    "4": ("missing_budget", "缺少预算", {}),
    "5": ("over_budget", "预算不足，无可行方案", {}),
    "6": ("save_failure", "保存持续失败，不得虚报成功", {"save_plan": 30}),
    "7": ("duplicate", "重复执行，只保存一份方案", {}),
}
LABELS = {"search_assets": "检索素材", "generate_script": "生成脚本", "estimate_plan": "估算方案",
          "save_plan": "保存方案", "finish": "结束任务"}


def display(report):
    for event in report["trace"]:
        name = event.get("tool")
        print(f"\n  第 {event['step']} 步 · {LABELS.get(name, event.get('event', name))}")
        if name:
            args = event.get("arguments") or {}
            print("    参数：" + json.dumps({k: v for k, v in args.items() if k != "reason"}, ensure_ascii=False))
            print("    依据：" + event.get("decision_basis", ""))
        observation = event.get("observation", {})
        if observation.get("ok") is False:
            print("    工具失败：" + observation["error"]["message"])
        elif "estimate" in observation:
            estimate = observation["estimate"]
            feasible = "可行" if estimate["feasible"] else "不可行"
            print(f"    结果：{estimate['tier']}，费用 {estimate['total_cost']}，预计 {estimate['total_minutes']} 分钟，{feasible}")
            if not estimate["feasible"]:
                print(f"    缺口：预算 {estimate['budget_gap']}，时间 {estimate['deadline_gap_minutes']} 分钟")
        elif "assets" in observation:
            print("    结果：找到 " + str(observation["count"]) + " 条素材，" + ", ".join(a["id"] for a in observation["assets"]))
        elif "script" in observation:
            print("    结果：已生成 " + str(len(observation["script"]["scenes"])) + " 个分镜；来源为检索素材")
        elif "plan_id" in observation:
            print("    结果：数据库已保存，" + observation["plan_id"])
        elif event.get("error"):
            print("    错误：" + event["error"])
        print("    后续：" + LABELS.get(event.get("next_action"), event.get("next_action", "")))
    print("\n  " + "─" * 58)
    print(f"  状态：{report['status']} | 已保存：{'是' if report['saved'] else '否'}")
    print("  说明：" + report["summary"])
    print(f"  本次模型调用 {report['model_calls']} 次，工具调用 {report['tool_calls']} 次")
    plan = report.get("plan")
    if plan:
        print(f"  最终费用：{plan['estimate']['total_cost']} / 预算 {plan['budget']}（模拟单位）")
        print(f"  预计完成：{plan['estimate']['finish_at']}")
        print("\n  任务计划：")
        for task in plan["tasks"]:
            print(f"    {task['step']:<12} {task['status']:<10} 费用 {task['cost']} | {task['minutes']} 分钟")
        print("\n  可编辑脚本：")
        for scene in plan["script"]["scenes"]:
            print(f"    分镜 {scene['scene']}：{scene['visual']} [{','.join(scene['source_ids'])}]")
            print("      " + scene["narration"])
        print("\n  制作任务仍为 planned，当前只保存方案。")


def demo(choice, provider, session_dir):
    name, title, failures = CASES[choice]
    print("\n" + "═" * 66)
    print("  内容任务规划 Agent · 第 2 题 · " + title)
    print("  模式：" + ("离线 Mock（规则模拟，只验证流程）" if provider == "mock" else "真实 DeepSeek API"))
    print("═" * 66, flush=True)
    req = read_json(ROOT / "examples/inputs" / ("normal.json" if name == "duplicate" else f"{name}.json"))
    run_id = uuid4().hex[:8]
    req["request_id"] = f"demo-{name}-{run_id}"
    print(f"  主题：{req['theme']}")
    print(f"  预算：{req.get('budget', '未提供')} | 截止：{req.get('deadline', '未提供')}", flush=True)
    load_env(ROOT / ".env")
    store = Store(session_dir / "demo.sqlite")
    try:
        model = MockModel() if provider == "mock" else DeepSeekModel()
        report = run_agent(req, model, store, failures=failures)
        display(report)
        target = session_dir / f"{name}-{run_id}.json"
        write_json(target, report)
        if name == "duplicate":
            print("\n  再执行相同请求，验证防重复：")
            replay = run_agent(req, model, store)
            display(replay)
            write_json(session_dir / f"duplicate-replay-{run_id}.json", replay)
            count = store.db.execute("SELECT count(*) FROM plans WHERE request_id=?", (req["request_id"],)).fetchone()[0]
            print(f"  数据库中该请求的方案数：{count}")
        print("\n  完整输入、输出和 Trace：" + str(target), flush=True)
        return 0 if report["status"] == "completed" else 2
    except ModelError as exc:
        print("  配置错误：" + str(exc))
        return 2
    finally:
        store.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["mock", "deepseek"], default="mock")
    parser.add_argument("--scenario", choices=list(CASES), default="2")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    folder = ROOT / "runtime" / ("demo-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])
    folder.mkdir(parents=True, exist_ok=True)
    result = demo(args.scenario, args.provider, folder)
    if not args.interactive:
        return result
    while True:
        print("\n  可选演示：")
        for number, (_, title, _) in CASES.items():
            print(f"    {number}. {title}")
        print(f"    8. 切换模式（当前 {args.provider}）\n    0. 退出")
        try:
            choice = input("\n  输入编号后回车：").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice == "0":
            return 0
        if choice == "8":
            args.provider = "deepseek" if args.provider == "mock" else "mock"
            print("  已切换为 " + args.provider + "；真实模式实际调用 API，使用账号余额。")
        elif choice in CASES:
            demo(choice, args.provider, folder)
        else:
            print("  请输入菜单中的编号。")


if __name__ == "__main__":
    raise SystemExit(main())
