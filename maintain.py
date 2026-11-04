"""Run from a source checkout without installing dependencies."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from repo_maintainer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
