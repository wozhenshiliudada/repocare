import unittest
from ranges import within

class RangeTests(unittest.TestCase):
    def test_boundaries_are_inclusive(self):
        self.assertTrue(within(1, 1, 3))
        self.assertTrue(within(3, 1, 3))

    def test_interior(self):
        self.assertTrue(within(2, 1, 3))

    def test_outside(self):
        self.assertFalse(within(0, 1, 3))
        self.assertFalse(within(4, 1, 3))
