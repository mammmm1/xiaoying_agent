"""Re-evaluate saved evidence without calling any API or editing source reports."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from evaluate import check_report


def recheck(folder):
    folder = Path(folder)
    summary_path = folder / "summary.json"
    original = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = []
    for row in original["cases"]:
        path = folder / row["report"]
        content = path.read_bytes()
        report = json.loads(content)
        name = row["case"]
        expected = ("needs_input" if name in ("missing_budget", "missing_deadline") else
                    "incomplete" if name in ("save_failure", "over_budget", "deadline_impossible", "no_assets", "step_limit") else "completed")
        # Preserve the first run's actual database row-count check; don't reconstruct it from text.
        count = 1 if name == "duplicate" and row["checks"].get("one_saved_plan") is True else None
        checks = check_report(name, report["request"], report, report["max_steps"], expected, count)
        rows.append({"case": name, "report": row["report"], "source_sha256": hashlib.sha256(content).hexdigest(),
                     "status": report["status"], "checks": checks, "passed": all(checks.values())})
    output = {"rechecked_at": datetime.now(timezone.utc).isoformat(), "provider": original["provider"],
              "mock": original["mock"], "original_summary": "summary.json", "original_passed": original["passed"],
              "original_summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
              "cases": rows, "passed": sum(r["passed"] for r in rows), "total": len(rows),
              "note": "仅复核原始记录，未调用模型、未修改原始输出；无素材时允许明确追问(needs_input)或说明资料缺口后停止(incomplete)，并验证实际空检索与无编造保存。原始数据库防重复检查沿用首次实测结果。"}
    (folder / "summary-rechecked.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Rechecked {output['passed']}/{output['total']} (original {original['passed']}/{original['total']}); no API calls")
    return 0 if output["passed"] == output["total"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    raise SystemExit(recheck(parser.parse_args().directory))
