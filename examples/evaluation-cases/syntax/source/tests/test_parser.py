import unittest
from parser import normalize

class ParserTests(unittest.TestCase):
    def test_whitespace(self):
        self.assertEqual(normalize(" HELLO "), "hello")

    def test_empty(self):
        self.assertEqual(normalize("  "), "")
