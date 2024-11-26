""" Tests for runpod.serverless.utils.validate """

import unittest
from unittest.mock import Mock

from runpod.serverless.utils import rp_validator


class TestValidator(unittest.TestCase):
    """Tests for validator"""

    def setUp(self):
        self.raw_input = {"a": 1.1, "x": 10, "y": 20, "z": 30}
        self.schema = {
            "a": {"type": float, "required": True},
            "x": {"type": int, "required": True},
            "y": {"type": int, "required": True, "default": 5},
            "z": {
                "type": int,
                "required": False,
                "default": 5,
                "constraints": Mock(return_value=True),
            },
            "w": {"type": int, "required": False, "default": 5},
        }

    def test_validate_success(self):
        """
        Tests validate
        """
        result = rp_validator.validate(self.raw_input, self.schema)

        self.assertNotIn("errors", result)
        expected_output = self.raw_input.copy()
        expected_output["w"] = self.schema["w"]["default"]
        self.assertEqual(result["validated_input"], expected_output)

    def test_validate_constraints_error(self):
        """
        Tests validate with constraints error
        """
        # Now add a constraint that the 'x' value must be less than 10
        self.schema["x"]["constraints"] = Mock(return_value=False)

        result = rp_validator.validate(self.raw_input, self.schema)

        self.assertIn("errors", result)
        self.assertIn("x does not meet the constraints.", result["errors"])

    def test_validate_missing_required_input(self):
        """
        Tests validate with missing required input
        """
        del self.raw_input["x"]
        result = rp_validator.validate(self.raw_input, self.schema)

        self.assertIn("errors", result)
        self.assertIn("x is a required input.", result["errors"])

    def test_validate_unexpected_input(self):
        """
        Tests validate with unexpected input
        """
        self.raw_input["unexpected"] = "unexpected"
        result = rp_validator.validate(self.raw_input, self.schema)

        self.assertIn("errors", result)
        self.assertIn(
            "Unexpected input. unexpected is not a valid input option.",
            result["errors"],
        )

    def test_validate_missing_default(self):
        """
        Tests validate with missing default
        """
        del self.schema["w"]["default"]
        result = rp_validator.validate(self.raw_input, self.schema)

        self.assertIn("errors", result)
        self.assertIn("Schema error, missing default value for w.", result["errors"])

    def test_validate_invalid_type(self):
        """
        Tests validate with invalid type
        """
        self.raw_input["x"] = "invalid"
        result = rp_validator.validate(self.raw_input, self.schema)

        self.assertIn("errors", result)
        self.assertIn(
            "x should be <class 'int'> type, not <class 'str'>.", result["errors"]
        )

    def test_validate_rules_not_dict(self):
        """
        Tests validate with rules not dict
        """
        result = rp_validator.validate(self.raw_input, {"x": "not dict"})
        self.assertIn("errors", result)

    def test_validate_simple_input(self):
        """
        Tests validate with simple input
        """
        result = rp_validator.validate(
            {"my_input": None}, {"my_input": {"type": str, "required": True}}
        )
        self.assertIn("errors", result)

    def test_validate_none_type(self):
        """
        Tests validate with None type
        """
        result = rp_validator.validate(
            {"my_input": None}, {"my_input": {"type": type(None), "required": True}}
        )
        self.assertNotIn("errors", result)


if __name__ == "__main__":
    unittest.main()
