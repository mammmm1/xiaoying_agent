"""DeepSeek Chat Completions adapter and explicitly labelled offline simulator."""
import json
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse
from .common import canonical


class ModelError(Exception):
    pass


class DeepSeekModel:
    mode = "deepseek"

    def __init__(self, api_key=None, base_url=None, model=None, timeout=None):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.name = model or os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
        self.timeout = float(timeout or os.getenv("DEEPSEEK_TIMEOUT", "60"))
        if not self.api_key or self.api_key.startswith("填入"):
            raise ModelError("未配置 DEEPSEEK_API_KEY；请在本地 .env 设置，或使用 --provider mock")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ModelError("DEEPSEEK_BASE_URL 必须为无认证信息、无查询参数的 HTTPS 地址")
        if not 0 < self.timeout <= 120:
            raise ModelError("DEEPSEEK_TIMEOUT 必须在 0~120 秒内")

    def decide(self, messages, tools):
        body = {"model": self.name, "messages": messages, "tools": tools, "tool_choice": "auto",
                "thinking": {"type": "disabled"}, "stream": False, "temperature": 0, "max_tokens": 1800}
        request = urllib.request.Request(self.base_url + "/chat/completions",
                                         data=canonical(body).encode(),
                                         headers={"Authorization": "Bearer " + self.api_key,
                                                  "Content-Type": "application/json"}, method="POST")
        try:
            # Avoid forwarding credentials to a redirect destination.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None
            opener = urllib.request.build_opener(NoRedirect)
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ModelError("模型响应超过大小上限")
            payload = json.loads(raw)
            choice = payload["choices"][0]
            if choice.get("finish_reason") not in ("stop", "tool_calls"):
                raise ModelError("模型输出未完成：" + str(choice.get("finish_reason")))
            message = choice["message"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ModelError("模型响应的 message 格式无效")
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list):
                raise ModelError("模型工具调用格式无效")
            for call in calls:
                if (not isinstance(call, dict) or not isinstance(call.get("id"), str)
                        or not isinstance(call.get("function"), dict)
                        or not isinstance(call["function"].get("name"), str)
                        or not isinstance(call["function"].get("arguments"), str)):
                    raise ModelError("模型工具调用缺少有效 ID、工具名或 JSON 参数")
            # Deliberately exclude reasoning_content and HTTP headers from trace and history.
            clean = {"role": "assistant", "content": message.get("content") or ""}
            if calls:
                clean["tool_calls"] = calls
            return clean, {"response_id": payload.get("id"), "model": payload.get("model", self.name),
                           "usage": payload.get("usage", {})}
        except urllib.error.HTTPError as exc:
            raise ModelError(f"模型 API HTTP {exc.code}；检查密钥、余额、模型名或限流；未自动切换 Mock") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise ModelError("模型 API 网络或超时错误；检查网络后使用原 request_id 续跑") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError("模型响应不是有效的 Chat Completions JSON") from None


class MockModel:
    """A rule-based test double. Its traces never count as real model decision evidence."""
    mode, name = "mock", "offline-rule-simulator"

    def __init__(self):
        self.counter = 0

    def decide(self, messages, tools):
        self.counter += 1
        context = json.loads(messages[1]["content"])
        state = context["checkpoint"]
        request = context["request"]
        # Reconstruct from actual observations, including checkpoints after a prior run.
        observations = list(state.get("tool_steps", []))
        by_id = {}
        for message in messages[2:]:
            for call in message.get("tool_calls", []):
                by_id[call["id"]] = call
            if message.get("role") == "tool":
                call = by_id[message["tool_call_id"]]
                observations.append({"tool": call["function"]["name"],
                                     "arguments": json.loads(call["function"]["arguments"]),
                                     "result": json.loads(message["content"])})

        def action(name, reason, **args):
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"mock-{self.counter}", "type": "function", "function": {
                    "name": name, "arguments": canonical({**args, "reason": reason})}}]}, {"model": self.name, "usage": {}}

        def end(summary):
            return action("finish", summary, status="incomplete", summary=summary)

        if observations and not observations[-1]["result"]["ok"]:
            failed = observations[-1]
            if failed["result"].get("error", {}).get("retryable") is False:
                return end("工具资料需要修正，保留已成功步骤：" + failed["result"]["error"]["message"])
            times = sum(o["tool"] == failed["tool"] and not o["result"]["ok"] for o in observations)
            if times >= 2:
                return end("工具持续失败；保留已成功步骤，稍后可以续跑")
            return action(failed["tool"], "观察到工具失败，复用已成功结果并重试失败步骤",
                          **{k: v for k, v in failed["arguments"].items() if k != "reason"})
        successful = [o for o in observations if o["result"]["ok"]]
        saved = next((o for o in reversed(successful) if o["tool"] == "save_plan"), None)
        if saved:
            return action("finish", "已观察到持久化成功", status="completed", summary="可行方案已保存，制作任务待执行")
        scripts = [o for o in successful if o["tool"] == "generate_script"]
        if not scripts and not state.get("current_script_id"):
            searches = [o for o in successful if o["tool"] == "search_assets"]
            if not searches:
                return action("search_assets", "先查找主题相关素材", query=request["theme"])
            found = searches[-1]["result"]["assets"]
            if not found:
                return end("未检索到可用素材，需要补充素材或调整主题")
            return action("generate_script", "依据已检索素材生成脚本", asset_ids=[a["id"] for a in found], format="tutorial")
        estimates = [o["result"]["estimate"] for o in successful if o["tool"] == "estimate_plan"]
        if not estimates:
            return action("estimate_plan", "先评估精细制作方案的成本和耗时", tier="premium")
        last = estimates[-1]
        if last["feasible"]:
            return action("save_plan", "估算同时满足预算和截止时间，保存方案", estimate_id=last["estimate_id"])
        tried = {e["tier"] for e in estimates}
        for tier in ("standard", "basic"):
            if tier not in tried:
                return action("estimate_plan", "观察到预算或时间超限，保留脚本并评估更轻量方案", tier=tier)
        return end(f"所有方案均不可行：最低方案预算缺口 {last['budget_gap']}，时间缺口 {last['deadline_gap_minutes']} 分钟")
