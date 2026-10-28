import json
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
from .common import canonical, validate_request
from .models import ModelError
from .tools import Tools, SCHEMAS

SYSTEM_PROMPT = """你是内容活动规划 Agent。必须依据实际工具结果规划，在预算与截止时间内保存方案。
工具选择由你完成；工具返回结果后再决定下一步。每轮只调用一个工具。
根据主题检索素材，再使用检索到的 ID 生成脚本，再选择制作档次并估算。
先评估 premium 的质量方案；若超预算、超截止时间或工具失败，根据实际观察选择更轻量的
standard/basic、重试失败步骤、补查或停止。保留成功结果，不要无理由重复成功步骤。
用户预算、开始时间和截止时间为硬要求，不得自行改变。费用表和工具返回为准。
无可行方案则 finish(incomplete)，说明缺口；不要无限重试。素材为空可改写查询或请求补充。
save_plan 成功后才能 finish(completed)。保存失败必须如实记录，不能声称成功。
completed 仅代表方案已保存，不代表制作或发布完成。断点状态可复用；失败 final 可以重新规划。
每个动作的 reason 仅写一句可公开的简要目的，不提供内部思维过程。
finish.summary 使用中文，说明结果或未完成原因。不能只用自然语言宣称完成，须调用 finish。
"""


def run_agent(request, model, store, max_steps=12, failures=None, data_dir=None):
    if not isinstance(max_steps, int) or not 1 <= max_steps <= 30:
        raise ValueError("max_steps 必须在 1~30 内")
    report = {"run_id": str(uuid4()), "started_at": datetime.now(timezone.utc).isoformat(),
              "provider": model.mode, "model": model.name, "mock": model.mode == "mock",
              "request": request, "max_steps": max_steps, "trace": [], "model_calls": 0, "tool_calls": 0}
    state = {}
    bound_request = False

    def finish(status, reason, code=None):
        saved = store.saved(request["request_id"]) if bound_request else None
        estimates = state.get("estimates", {})
        current = estimates.get(state.get("current_estimate_id"))
        for i, event in enumerate(report["trace"]):
            following = report["trace"][i + 1] if i + 1 < len(report["trace"]) else {}
            event["next_action"] = following.get("tool", following.get("event", f"结束：{status}"))
            observation = event.get("observation", {})
            if "estimate" in observation:
                e = observation["estimate"]
                event["observation_summary"] = {k: e[k] for k in ("tier", "total_cost", "total_minutes", "feasible", "budget_gap", "deadline_gap_minutes")}
            elif "script" in observation:
                event["observation_summary"] = {"script_id": observation["script"]["script_id"], "cached": observation["cached"]}
            elif "plan_id" in observation:
                event["observation_summary"] = {"saved": observation["saved"], "plan_id": observation["plan_id"]}
            else:
                event["observation_summary"] = {k: v for k, v in observation.items() if k != "assets"}
        report["api_usage"] = {key: sum(e.get("metadata", {}).get("usage", {}).get(key, 0) or 0 for e in report["trace"])
                               for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        report.update({"status": status, "summary": reason, "error_code": code,
                       "saved": saved is not None, "plan": saved,
                       "latest_estimate": current, "steps": deepcopy(state.get("tool_steps", [])),
                       "ended_at": datetime.now(timezone.utc).isoformat(),
                       "evidence_note": "离线规则模拟，仅验证流程，不证明真实模型决策效果" if model.mode == "mock"
                                        else "真实 API 路径；须结合 trace 检查本次是否实际完成模型调用"})
        return report

    errors = validate_request(request)
    if errors:
        return finish("needs_input", "；".join(errors), "INVALID_INPUT")
    try:
        state = store.begin(request)
        origin = {"provider": model.mode, "model": model.name}
        if state.get("origin") and state["origin"] != origin:
            return finish("incomplete", "断点的模型来源与当前模式不同，请使用新的数据库或 request_id", "PROVENANCE_CONFLICT")
        state["origin"] = origin
        bound_request = True
        # Explicitly retain evidence provenance when a saved request is replayed.
        if store.saved(request["request_id"]):
            report["replayed"] = True
            return finish("completed", "同一请求已保存，返回原方案；本次未重复调用模型或保存")
        state.pop("final", None)
        tools = Tools(request, state, store, failures, data_dir)
        store.checkpoint(request["request_id"], state)
    except ValueError as exc:
        return finish("incomplete", str(exc), "STATE_CONFLICT")
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": canonical({"request": request, "checkpoint": deepcopy(state),
                    "rates": tools.rates, "remaining_model_rounds": max_steps})}]
    for step in range(1, max_steps + 1):
        report["model_calls"] += 1
        try:
            message, metadata = model.decide(messages, SCHEMAS)
        except ModelError as exc:
            report["trace"].append({"step": step, "event": "model_error", "error": str(exc)})
            return finish("incomplete", str(exc), "MODEL_ERROR")
        messages.append(message)
        calls = message.get("tool_calls") or []
        if len(calls) != 1:
            error = {"ok": False, "error": {"code": "ONE_ACTION_REQUIRED", "message": "每轮必须且只能调用一个工具"}}
            if calls:
                for call in calls:
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": canonical(error)})
            else:
                messages.append({"role": "user", "content": canonical(error)})
            report["trace"].append({"step": step, "event": "protocol_error", "observation": error, "metadata": metadata})
            continue
        call = calls[0]
        name = call["function"]["name"]
        report["tool_calls"] += 1
        try:
            args = json.loads(call["function"]["arguments"])
        except (ValueError, TypeError):
            args = None
        result = tools.call(name, args)
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": canonical(result)})
        report["trace"].append({"step": step, "tool": name, "arguments": args,
                                "decision_basis": args.get("reason", "") if isinstance(args, dict) else "",
                                "observation": result, "metadata": metadata})
        if state.get("final"):
            final = state["final"]
            return finish(final["status"], final["summary"])
    return finish("incomplete", "达到执行轮数上限；已保留成功结果，可使用相同请求续跑", "STEP_LIMIT")
