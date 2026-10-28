import hashlib
import json
import math
from datetime import datetime


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:24]


def read_json(path):
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path, value):
    from pathlib import Path
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_time(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("时间必须包含时区，例如 +08:00")
    return dt


def validate_request(request):
    if not isinstance(request, dict):
        return ["input 必须是 JSON 对象"]
    errors = []
    for key in ("request_id", "theme", "start_at", "deadline"):
        if not isinstance(request.get(key), str) or not request[key].strip():
            errors.append(f"缺少或无效字段：{key}")
    amount = request.get("budget")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
        errors.append("budget 必须为非负有限数（模拟单位）")
    for key in ("start_at", "deadline"):
        if isinstance(request.get(key), str):
            try:
                parse_time(request[key])
            except (ValueError, TypeError):
                errors.append(f"{key} 必须为带时区的 ISO 8601 时间")
    if not errors and parse_time(request["deadline"]) <= parse_time(request["start_at"]):
        errors.append("deadline 必须晚于 start_at")
    return errors
