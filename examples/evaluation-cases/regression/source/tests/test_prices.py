import unittest
from prices import apply_discount

class PriceTests(unittest.TestCase):
    def test_discount(self):
        self.assertEqual(apply_discount(100, .2), 80)

    def test_no_discount_regression(self):
        self.assertEqual(apply_discount(100, 0), 100)

    def test_full_discount(self):
        self.assertEqual(apply_discount(100, 1), 0)
