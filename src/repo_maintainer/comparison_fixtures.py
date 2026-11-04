"""Self-authored scripted fixtures; published answers, not unseen model tasks."""
from dataclasses import dataclass

from .demo import DEMO_TEST
from .models import Policy


@dataclass(frozen=True)
class ComparisonCase:
    id: str
    issue: str
    source: str
    first: str
    later: str | None
    assertions: tuple[str, ...]
    rejected: bool = False

    @property
    def files(self):
        body = "\n".join("        " + line for line in self.assertions)
        return {"logic.py": self.source,
                "tests/test_logic.py": "import unittest\nimport logic\n\n"
                    "class BehaviorTests(unittest.TestCase):\n    def test_contract(self):\n" + body + "\n",
                ".gitignore": "__pycache__/\n*.pyc\n"}

    @property
    def policy(self):
        return Policy(("logic.py",), (DEMO_TEST,), timeout_seconds=15,
                      readonly_paths=("tests/test_logic.py",))

    @property
    def task(self):
        return "comparison:" + self.id + ": " + self.issue


def cases():
    shipping = ComparisonCase("shipping", "Free shipping starts at total 100, preserve smaller orders",
        "def shipping(total):\n    return 0 if total > 100 else 12\n",
        "def shipping(total):\n    return 0 if total >= 100 else 12\n", None,
        ("self.assertEqual([12, 12, 0, 0], [logic.shipping(n) for n in (0, 99, 100, 101)])",))
    return [shipping,
        ComparisonCase("discount", "Integer cents discount must preserve refunds and fractional cents",
            "def discount(amount, percent):\n    return amount // 100 * (100 - percent)\n",
            "def discount(amount, percent):\n    return abs(amount) * (100 - percent) // 100\n", None,
            ("self.assertEqual(800, logic.discount(1000, 20))",
             "self.assertEqual(99, logic.discount(199, 50))",
             "self.assertEqual(-180, logic.discount(-200, 10))")),
        ComparisonCase("switch", "Parse enabled values case-insensitively, trimming whitespace",
            "def enabled(text):\n    return bool(text)\n",
            "def enabled(text):\n    return text.lower() == 'true'\n",
            "def enabled(text):\n    return text.strip().lower() in ('true', '1', 'yes')\n",
            ("self.assertFalse(logic.enabled('false'))", "self.assertFalse(logic.enabled('0'))",
             "self.assertTrue(logic.enabled(' TRUE '))", "self.assertTrue(logic.enabled('1'))")),
        ComparisonCase("inventory", "Parse integer quantity without adding an item",
            "def quantity(text):\n    return int(text) + 1\n",
            "def quantity(text):\n    return int(text\n",
            "def quantity(text):\n    return int(text)\n",
            ("self.assertEqual([0, 12, -2], [logic.quantity(v) for v in ('0', '12', '-2')])",)),
        ComparisonCase("approval_reject", shipping.issue, shipping.source, shipping.first, None,
                       shipping.assertions, rejected=True)]
