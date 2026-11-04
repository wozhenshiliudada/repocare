"""Driver for real Pi SDK / local synthetic responses, with no live credentials."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.comparison_fixtures import cases
from repo_maintainer.comparison import run_comparison

parser = argparse.ArgumentParser()
parser.add_argument("--describe", action="store_true")
parser.add_argument("--directory", type=Path)
parser.add_argument("--url")
parser.add_argument("--showcase", action="store_true")
parser.add_argument("--case", choices=[c.id for c in cases()])
args = parser.parse_args()
selected = [c for c in cases() if args.case is None or args.case == c.id]
if args.describe:
    print(json.dumps([asdict(c) for c in selected]))
else:
    if not args.url or not args.directory:
        parser.error("Replay requires an explicit loopback URL and new directory")
    summary = run_comparison(args.directory, args.url, args.showcase, selected_cases=selected)
    print(json.dumps({k: v for k, v in summary.items() if k != "cells"}))
    sys.exit(0 if summary["passed"] else 1)
