import unittest

from calculator import add


class CalculatorTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative_regression(self):
        self.assertEqual(add(-2, -3), -5)

    def test_zero(self):
        self.assertEqual(add(0, 0), 0)

    def test_mixed(self):
        self.assertEqual(add(5, -2), 3)
