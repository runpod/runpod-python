"""Unit tests for the error classes in the runpod.error module."""

import unittest

# Assuming the error classes are in a file named 'error.py'
from runpod.error import AuthenticationError, QueryError, RunPodError


class TestErrorClasses(unittest.TestCase):
    """Unit tests for the error classes in the runpod.error module."""

    def test_run_pod_error_with_message(self):
        """Test the RunPodError class with a message."""
        error_msg = "An error occurred"
        err = RunPodError(error_msg)
        self.assertEqual(str(err), error_msg)

    def test_run_pod_error_without_message(self):
        """Test the RunPodError class without a message."""
        err = RunPodError()
        self.assertEqual(str(err), "None")

    def test_authentication_error(self):
        """Test the AuthenticationError class."""
        error_msg = "Authentication failed"
        err = AuthenticationError(error_msg)
        self.assertEqual(str(err), error_msg)

    def test_query_error_with_request_details(self):
        """Test the QueryError class with request details."""
        error_msg = "Query failed"
        query_str = "POST /v2/pods"
        err = QueryError(
            error_msg,
            query_str,
            status_code=422,
            errors=["$.name is required"],
        )
        self.assertEqual(str(err), error_msg)
        self.assertEqual(err.query, query_str)
        self.assertEqual(err.status_code, 422)
        self.assertEqual(err.errors, ["$.name is required"])

    def test_query_error_without_request_details(self):
        """Test the QueryError class without request details."""
        err = QueryError()
        self.assertEqual(str(err), "None")
        self.assertIsNone(err.query)
        self.assertIsNone(err.status_code)
        self.assertEqual(err.errors, [])
