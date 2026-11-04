import unittest

from repeat import repeat


class RepeatTests(unittest.TestCase):
    def test_multiple(self):
        self.assertEqual(repeat("a", 3), "aaa")

    def test_zero(self):
        self.assertEqual(repeat("x", 0), "")

    def test_empty_text(self):
        self.assertEqual(repeat("", 5), "")

    def test_longer_text(self):
        self.assertEqual(repeat("ab", 2), "abab")
