"""Test-only real process exits at journal boundaries; self-authored fixtures only."""
import argparse
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.demo import DEMO_TEST
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider import MockProvider

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
parser.add_argument("--pi-mock-url")
parser.add_argument("--phase", choices=("consume", "close_before_commit", "close_after_commit"), default="consume")
args = parser.parse_args()
root = args.directory.resolve()
policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))
provider = PiProvider(policy.allowed_paths, args.pi_mock_url, policy.readonly_paths, 2) if args.pi_mock_url else MockProvider()
session = UISession(root / "repository", policy, provider, root / "runs")
if args.phase == "consume":
    plan = session.state()["plans"][-1]
    session._operate("claim_plan", {"plan_id": plan["id"], "revision": plan["revision"]})
    with patch("repo_maintainer.local_ui.repair", side_effect=lambda *a, **kw: os._exit(23)):
        session.submit("e"*32, "decision", {"plan_id": plan["id"], "fingerprint": plan["fingerprint"], "decision": "approve"})
        session.close()
else:
    identity = session.state()["runs"][-1]["run_id"]
    preview = session._operate("inspect_interruption", {"run_id": identity})["reconciliation"]
    if args.phase == "close_before_commit":
        original = session.registry.put
        def fail_write(kind, key, value, db=None):
            result = original(kind, key, value, db)
            if kind == "run" and value.get("resolution"):
                os._exit(25)
            return result
        with patch.object(session.registry, "put", side_effect=fail_write):
            session.submit("f"*32, "close_interruption", {"run_id": identity, "revision": preview["revision"]})
            session.close()
    else:
        original = session._inspect_interruption
        calls = 0
        def fail_after_commit(*values):
            global calls
            calls += 1
            if calls == 2:
                os._exit(24)
            return original(*values)
        with patch.object(session, "_inspect_interruption", side_effect=fail_after_commit):
            session.submit("f"*32, "close_interruption", {"run_id": identity, "revision": preview["revision"]})
            session.close()
