import unittest
from settings import load_limit

class SettingsTests(unittest.TestCase):
    def test_valid_integer_limit(self):
        self.assertEqual(load_limit(), 10)

    def test_limit_type(self):
        self.assertIsInstance(load_limit(), int)
