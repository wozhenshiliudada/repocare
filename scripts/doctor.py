"""Compatible source-check entrypoint; implementation ships inside the package.

Only the source CLI adds src to its own import path. Service readiness imports
the application package directly and never executes this file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
# Standard-library-only checker also works before optional dependencies exist.
from repo_maintainer.installation_check import (
    BUILD_STEP, SERVER_ASSETS, UI_ROOT, _linked, _local_asset, check_workbench_assets,
    inspect_environment,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['core', 'web', 'dev'], default='web')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    report = inspect_environment(root=ROOT, profile=args.profile, installation_kind='source')
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for row in report['checks']:
            print(f"[{row['status']}] {row['name']}: {row['detail']}")
        print('READY' if report['ready'] else 'SETUP INCOMPLETE: see SETUP.md')
        print(report['scope'])
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
