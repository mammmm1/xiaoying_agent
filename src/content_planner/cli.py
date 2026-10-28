import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from .agent import run_agent
from .common import read_json, write_json
from .models import DeepSeekModel, MockModel, ModelError
from .store import Store
from .tools import ROOT, SPEC


def load_env(path):
    """Small literal .env reader: never evaluates shell code or overrides environment."""
    if not Path(path).is_file():
        return
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key.startswith("DEEPSEEK_"):
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)


def main(argv=None):
    parser = argparse.ArgumentParser(description="第 2 题：内容任务规划 Agent。仅保存方案，不执行制作或发布。")
    parser.add_argument("--input", required=True, help="请求 JSON 文件")
    parser.add_argument("--provider", choices=["deepseek", "mock"], default="deepseek")
    parser.add_argument("--db", help="SQLite 断点路径，默认 runtime/<provider>.sqlite")
    parser.add_argument("--output", help="包含输入、输出与 Trace 的实际运行报告")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--fail", action="append", default=[], metavar="TOOL:COUNT", help="可重复；用于注入业务工具失败")
    parser.add_argument("--data-dir", default=str(ROOT / "data"), help="合成资料路径，便于替换数据验证")
    args = parser.parse_args(argv)
    store = None
    try:
        load_env(args.env_file)
        request = read_json(args.input)
        failures = {}
        for value in args.fail:
            name, count = value.rsplit(":", 1)
            if name not in SPEC or int(count) < 1:
                raise ValueError("--fail 需要有效工具名及正整数次数")
            failures[name] = int(count)
        model = MockModel() if args.provider == "mock" else DeepSeekModel()
        store = Store(args.db or f"runtime/{args.provider}.sqlite")
        report = run_agent(request, model, store, args.max_steps, failures, args.data_dir)
        if args.output:
            write_json(args.output, report)
        # Ensure Chinese output on terminals whose default encoding isn't UTF-8.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "completed" else 2
    except (ValueError, OSError, sqlite3.Error, ModelError) as exc:
        # Never include provider keys, request headers, or arbitrary HTTP response bodies.
        error = {"status": "incomplete", "saved": False, "error_code": "SETUP_ERROR",
                 "summary": str(exc) if not isinstance(exc, (OSError, sqlite3.Error)) else "无法读取输入、资料或写入本地文件"}
        if args.output:
            write_json(args.output, error)
        print(json.dumps(error, ensure_ascii=False, indent=2))
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
