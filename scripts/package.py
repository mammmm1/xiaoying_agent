"""Create a source delivery ZIP without local credentials, state or caches."""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from content_planner.cli import load_env
import os


if __name__ == "__main__":
    load_env(ROOT / ".env")
    key = os.getenv("DEEPSEEK_API_KEY", "")
    target = ROOT.parent / "content-planner-agent.zip"
    allowed_files = {"README.md", "run.py", "pyproject.toml", "requirements.txt", ".gitignore", ".gitattributes", ".env.example"}
    allowed_dirs = {"src", "tests", "examples", "data", "docs", "scripts", ".github"}
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(ROOT)
        if rel.parts[0] not in allowed_dirs and str(rel) not in allowed_files:
            continue
        if "__pycache__" in rel.parts or path.suffix in (".pyc", ".pyo", ".sqlite", ".db"):
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            continue
        if key and not key.startswith("填入") and key.encode() in path.read_bytes():
            raise SystemExit(f"Refusing to package: credential detected in {rel}")
        files.append((path, rel))
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, rel in files:
            archive.write(path, Path(ROOT.name) / rel)
    print(f"Packaged {len(files)} files: {target}")
