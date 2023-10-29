"""Tests for the endpoint mutation generation."""

import unittest

from runpod.api.mutations.endpoints import generate_endpoint_mutation

class TestGenerateEndpointMutation(unittest.TestCase):
    """Tests for the endpoint mutation generation."""

    def test_required_fields(self):
        """Test the required fields."""
        result = generate_endpoint_mutation("test_name", "test_template_id")
        self.assertIn('name: "test_name"', result)
        self.assertIn('templateId: "test_template_id"', result)
        self.assertIn('gpuIds: "AMPERE_16"', result)  # Default value
        self.assertIn('networkVolumeId: ""', result)  # Default value
        self.assertIn('locations: ""', result)  # Default value

    def test_all_fields(self):
        """Test all the fields."""
        result = generate_endpoint_mutation(
            "test_name", "test_template_id", "AMPERE_20",
            "test_volume_id", "US_WEST", 10, "WORKER_COUNT", 5, 2, 4
        )
        self.assertIn('name: "test_name"', result)
        self.assertIn('templateId: "test_template_id"', result)
        self.assertIn('gpuIds: "AMPERE_20"', result)
        self.assertIn('networkVolumeId: "test_volume_id"', result)
        self.assertIn('locations: "US_WEST"', result)
        self.assertIn('idleTimeout: 10', result)
        self.assertIn('scalerType: "WORKER_COUNT"', result)
        self.assertIn('scalerValue: 5', result)
        self.assertIn('workersMin: 2', result)
        self.assertIn('workersMax: 4', result)
