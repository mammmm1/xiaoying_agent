"""Business tools: model chooses actions; tools enforce data and save constraints."""
from datetime import timedelta
from pathlib import Path
import math
import sqlite3
from .common import digest, parse_time, read_json

ROOT = Path(__file__).resolve().parents[2]


def schema(name, description, properties, required):
    properties = {**properties, "reason": {"type": "string", "description": "一句可公开的动作目的，不输出内部思维过程"}}
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": [*required, "reason"], "additionalProperties": False}}}


STRING = {"type": "string"}
SCHEMAS = [
    schema("search_assets", "按主题检索合成素材。无结果可改写查询或停止。", {"query": STRING}, ["query"]),
    schema("generate_script", "本地模拟脚本服务：用已检索素材原文生成可编辑脚本。成功结果可复用。",
           {"asset_ids": {"type": "array", "items": STRING, "minItems": 1},
            "format": {"type": "string", "enum": ["tutorial", "story"]}}, ["asset_ids", "format"]),
    schema("estimate_plan", "估算当前脚本的总成本、串行耗时与截止时间。返回真实费率和可行性，不接受自行填写成本。",
           {"tier": {"type": "string", "enum": ["premium", "standard", "basic"]}}, ["tier"]),
    schema("save_plan", "仅保存预算和截止时间均通过的最新脚本方案，幂等保存。失败可重试或停止。",
           {"estimate_id": STRING}, ["estimate_id"]),
    schema("finish", "明确结束；未保存不得标为完成。不可行时说明预算或时间缺口。",
           {"status": {"type": "string", "enum": ["completed", "incomplete", "needs_input"]},
            "summary": STRING}, ["status", "summary"]),
]
SPEC = {item["function"]["name"]: item["function"]["parameters"] for item in SCHEMAS}


class ToolError(Exception):
    def __init__(self, code, message, retryable=None):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


def data_error(code, message):
    return ToolError(code, message, retryable=False)


def valid_amount(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def validate_args(name, args):
    if name not in SPEC:
        raise ToolError("UNKNOWN_TOOL", "工具不在白名单中")
    if not isinstance(args, dict):
        raise ToolError("INVALID_ARGUMENTS", "参数必须为对象")
    spec = SPEC[name]
    if set(args) - set(spec["properties"]) or set(spec["required"]) - set(args):
        raise ToolError("INVALID_ARGUMENTS", "参数缺失或包含未定义字段")
    for key, value in args.items():
        prop = spec["properties"][key]
        if prop["type"] == "string" and (not isinstance(value, str) or not value.strip() or len(value) > 2000):
            raise ToolError("INVALID_ARGUMENTS", f"{key} 必须为非空短字符串")
        if "enum" in prop and value not in prop["enum"]:
            raise ToolError("INVALID_ARGUMENTS", f"{key} 取值无效")
        if prop["type"] == "array" and (not isinstance(value, list) or not value or len(value) > 10
                                         or any(not isinstance(v, str) for v in value)):
            raise ToolError("INVALID_ARGUMENTS", f"{key} 必须为非空字符串列表")


class Tools:
    def __init__(self, request, state, store, failures=None, data_dir=None):
        self.request, self.state, self.store = request, state, store
        self.failures = dict(failures or {})
        folder = Path(data_dir or ROOT / "data")
        self.assets, self.rates = read_json(folder / "assets.json"), read_json(folder / "rates.json")
        self.data_hash = digest([self.assets, self.rates])
        if state.get("data_hash") and state["data_hash"] != self.data_hash:
            raise ValueError("DATA_CHANGED：断点所用资料已变化，请使用新 request_id")
        state["data_hash"] = self.data_hash
        for key in ("retrieved", "scripts", "estimates"):
            state.setdefault(key, {})
        state.setdefault("tool_steps", [])

    def call(self, name, args):
        try:
            validate_args(name, args)
            if self.failures.get(name, 0) > 0:
                self.failures[name] -= 1
                raise ToolError("INJECTED_FAILURE", f"模拟 {name} 工具失败；未执行副作用，可由模型选择重试、替代或停止")
            result = getattr(self, name)(**{key: value for key, value in args.items() if key != "reason"})
            result = {"ok": True, **result}
        except ToolError as exc:
            result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
            if exc.retryable is not None:
                result["error"]["retryable"] = exc.retryable
        except (sqlite3.Error, OSError):
            result = {"ok": False, "error": {"code": "STORAGE_ERROR", "message": "工具存储失败；未确认保存成功"}}
        self.state["tool_steps"].append({"tool": name, "arguments": args, "result": result,
                                         "status": "completed" if result["ok"] else "failed"})
        self.store.checkpoint(self.request["request_id"], self.state)
        return result

    def search_assets(self, query):
        query = query.lower().strip()
        scored = []
        if not isinstance(self.assets, list):
            raise data_error("INVALID_ASSET_DATA", "素材库必须是素材对象列表")
        seen = set()
        for asset in self.assets:
            if not isinstance(asset, dict):
                raise data_error("INVALID_ASSET_DATA", "素材条目必须是对象")
            if (not isinstance(asset.get("id"), str) or not asset["id"].strip()
                    or asset["id"] in seen):
                raise data_error("INVALID_ASSET_DATA", "素材 id 必须是非空且唯一的文本")
            seen.add(asset["id"])
            if (not isinstance(asset.get("title"), str) or not asset["title"].strip()
                    or not isinstance(asset.get("tags"), list)
                    or any(not isinstance(tag, str) or not tag.strip() for tag in asset["tags"])):
                raise data_error("INVALID_ASSET_DATA", "素材 title 必须为非空文本，tags 必须为文本列表")
            # Deliberately simple local keyword retrieval; no vector database required.
            terms = [*asset["tags"], asset["title"]]
            score = sum(term.lower() in query or query in term.lower() for term in terms)
            if score:
                scored.append((score, asset))
        found = [asset for _, asset in sorted(scored, key=lambda x: (-x[0], x[1]["id"]))[:3]]
        for asset in found:
            self.state["retrieved"][asset["id"]] = asset
        return {"assets": found, "count": len(found), "query": query}

    def generate_script(self, asset_ids, format):
        asset_ids = list(dict.fromkeys(asset_ids))
        if any(key not in self.state["retrieved"] for key in asset_ids):
            raise ToolError("DEPENDENCY_MISSING", "只能使用检索成功且已观察到的素材 ID")
        for aid in asset_ids:
            facts = self.state["retrieved"][aid].get("facts")
            if (not isinstance(facts, list) or not facts
                    or any(not isinstance(fact, str) or not fact.strip() for fact in facts)):
                raise data_error("INVALID_ASSET_DATA", f"素材 {aid} 的 facts 必须为非空文本列表；请补充资料或改用有效素材")
        key = "script-" + digest([asset_ids, format, self.data_hash])
        cached = key in self.state["scripts"]
        if not cached:
            scenes = [{"scene": i + 1, "visual": self.state["retrieved"][aid]["title"],
                       "narration": "；".join(self.state["retrieved"][aid]["facts"]), "source_ids": [aid]}
                      for i, aid in enumerate(asset_ids)]
            self.state["scripts"][key] = {"script_id": key, "format": format, "scenes": scenes,
                                            "service": "local_template_mock", "asset_ids": asset_ids}
        self.state["current_script_id"] = key
        return {"script": self.state["scripts"][key], "cached": cached}

    def rate_parts(self, tier):
        rate = self.rates
        if not isinstance(rate, dict):
            raise data_error("INVALID_RATES", "费率表必须是对象")
        for field in ("currency", "version"):
            if not isinstance(rate.get(field), str) or not rate[field].strip():
                raise data_error("INVALID_RATES", f"费率表 {field} 必须为非空文本")
        production = rate.get("production")
        if not isinstance(production, dict) or tier not in production:
            raise data_error("INVALID_RATES", f"费率表缺少 production.{tier} 配置")
        parts = []
        for step, part, path in [("research", rate.get("research"), "research"),
                                 ("script", rate.get("script"), "script"),
                                 ("production", production[tier], f"production.{tier}"),
                                 ("save", rate.get("save"), "save")]:
            if not isinstance(part, dict):
                raise data_error("INVALID_RATES", f"费率表 {path} 必须是对象")
            for field in ("cost", "minutes"):
                if not valid_amount(part.get(field)):
                    raise data_error("INVALID_RATES", f"费率表 {path}.{field} 必须是非负有限数，不能使用文本或布尔值")
            parts.append({**part, "step": step})
        return parts

    def estimate_plan(self, tier):
        script_id = self.state.get("current_script_id")
        if not script_id:
            raise ToolError("DEPENDENCY_MISSING", "请先成功检索并生成脚本")
        rate = self.rates
        parts = self.rate_parts(tier)
        cost, minutes = sum(v["cost"] for v in parts), sum(v["minutes"] for v in parts)
        if not valid_amount(cost) or not valid_amount(minutes):
            raise data_error("INVALID_RATES", "费率合计超出有限数值范围")
        try:
            finish = parse_time(self.request["start_at"]) + timedelta(minutes=minutes)
        except (OverflowError, ValueError):
            raise data_error("INVALID_RATES", "费率耗时超出可表示的日期范围") from None
        deadline = parse_time(self.request["deadline"])
        estimate = {"estimate_id": "estimate-" + digest([script_id, tier, self.request, self.data_hash]),
                    "script_id": script_id, "tier": tier, "breakdown": parts,
                    "total_cost": cost, "currency": rate["currency"], "total_minutes": minutes,
                    "finish_at": finish.isoformat(), "within_budget": cost <= self.request["budget"],
                    "within_deadline": finish <= deadline,
                    "budget_gap": max(0, cost - self.request["budget"]),
                    "deadline_gap_minutes": max(0, (finish - deadline).total_seconds() / 60),
                    "production_options": rate["production"], "rates_version": rate["version"]}
        estimate["feasible"] = estimate["within_budget"] and estimate["within_deadline"]
        self.state["estimates"][estimate["estimate_id"]] = estimate
        self.state["current_estimate_id"] = estimate["estimate_id"]
        return {"estimate": estimate}

    def save_plan(self, estimate_id):
        estimate = self.state["estimates"].get(estimate_id)
        if not estimate:
            raise ToolError("DEPENDENCY_MISSING", "不存在已执行成功的估算")
        if estimate["script_id"] != self.state.get("current_script_id"):
            raise ToolError("STALE_ESTIMATE", "脚本已变化，请对当前脚本重新估算")
        # Recalculate from current server-side rates; never trust model-provided totals.
        fresh = self.estimate_plan(estimate["tier"])["estimate"]
        if not fresh["feasible"]:
            raise ToolError("CONSTRAINT_VIOLATION", "预算或截止时间不满足，拒绝保存")
        tasks, cursor = [], parse_time(self.request["start_at"])
        for part in fresh["breakdown"]:
            end = cursor + timedelta(minutes=part["minutes"])
            tasks.append({**part, "start_at": cursor.isoformat(), "end_at": end.isoformat(),
                          "depends_on": [tasks[-1]["step"]] if tasks else [],
                          "status": "planned" if part["step"] == "production" else "completed"})
            cursor = end
        plan = self.store.save(self.request["request_id"], {
            "theme": self.request["theme"], "budget": self.request["budget"],
            "deadline": self.request["deadline"], "estimate": fresh,
            "script": self.state["scripts"][fresh["script_id"]], "tasks": tasks,
            "scope": "内容活动方案已保存；制作任务尚待执行，无真实发布",
            "risks": ["素材、制作费用与耗时为合成数据", "脚本生成使用本地模板工具", "真实模型 API 费用不在模拟预算中"]})
        self.state["saved_plan_id"] = plan["plan_id"]
        return {"saved": True, "plan_id": plan["plan_id"], "plan": plan}

    def finish(self, status, summary):
        saved = self.store.saved(self.request["request_id"])
        if status == "completed" and not saved:
            raise ToolError("NOT_SAVED", "尚未确认保存成功，不能标记 completed")
        self.state["final"] = {"status": status, "summary": summary}
        return {"status": status, "summary": summary}
