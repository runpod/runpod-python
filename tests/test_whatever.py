import unittest
from whatever import is_even


class TestIsEven(unittest.TestCase):

    def test_is_even_valid_input(self):
        job = {"id": "test-id", "input": {"number": 4}}
        result = is_even(job)
        self.assertTrue(result)

    def test_is_even_invalid_input(self):
        job = {"id": "test-id", "input": {"number": "not an integer"}}
        result = is_even(job)
        self.assertEqual(result, {"error": "Silly human, you need to pass an integer."})

    def test_is_even_odd_number(self):
        job = {"id": "test-id", "input": {"number": 3}}
        result = is_even(job)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
