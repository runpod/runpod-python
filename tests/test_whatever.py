"""
This module contains unit tests for the 'whatever' module, specifically the is_even function.
"""

import unittest
from .whatever import is_even


class TestIsEven(unittest.TestCase):
    """
    TestIsEven class contains test cases for the is_even function in the whatever module.
    """

    def test_is_even_true(self):
        """
        Test that is_even returns True for even numbers.
        """
        job = {"input": {"number": 2}}
        self.assertTrue(is_even(job))

    def test_is_even_false(self):
        """
        Test that is_even returns False for odd numbers.
        """
        job = {"input": {"number": 3}}
        self.assertFalse(is_even(job))

    def test_is_even_error(self):
        """
        Test that is_even returns an error message for non-integer inputs.
        """
        job = {"input": {"number": "two"}}
        expected_output = {"error": "Silly human, you need to pass an integer."}
        self.assertEqual(is_even(job), expected_output)


if __name__ == "__main__":
    unittest.main()
