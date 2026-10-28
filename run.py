"""无需安装依赖：python run.py --help。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from content_planner.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
