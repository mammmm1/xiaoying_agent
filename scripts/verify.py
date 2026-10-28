"""Run the real test suite and save its actual output as submission evidence."""
import io
import platform
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    stream = io.StringIO()
    stream.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\nPython: {platform.python_version()}\n")
    stream.write("Command equivalent: python -m unittest discover -s tests -v\n\n")
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    target = ROOT / "examples" / "test-results.txt"
    target.write_text(stream.getvalue(), encoding="utf-8")
    print(stream.getvalue())
    print(f"Saved: {target}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
