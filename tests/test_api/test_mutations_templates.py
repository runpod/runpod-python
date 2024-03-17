""" Unit tests for the function generate_pod_template in the file api_wrapper.py """

import unittest

from runpod.api.mutations.templates import generate_pod_template


class TestGeneratePodTemplate(unittest.TestCase):
    """Unit tests for the function generate_pod_template in the file api_wrapper.py"""

    def test_basic_required_fields(self):
        """Test the basic required fields are present in the generated template"""
        result = generate_pod_template("test_name", "test_image_name")
        self.assertIn('name: "test_name"', result)
        self.assertIn('imageName: "test_image_name"', result)
        self.assertIn('dockerArgs: ""', result)  # Defaults
        self.assertIn("containerDiskInGb: 10", result)  # Defaults
        self.assertIn("volumeInGb: 0", result)  # Defaults
        self.assertIn('ports: ""', result)  # Defaults
        self.assertIn("env: []", result)  # Defaults
        self.assertIn("isServerless: false", result)  # Defaults

    def test_optional_fields(self):
        """Test the optional fields are present in the generated template"""
        result = generate_pod_template(
            "test_name",
            "test_image_name",
            docker_start_cmd="test_cmd",
            volume_in_gb=5,
            volume_mount_path="/path/to/volume",
            ports="8000, 8001",
            env={"VAR1": "val1", "VAR2": "val2"},
            is_serverless=True,
            registry_auth_id="test_auth",
        )
        self.assertIn('dockerArgs: "test_cmd"', result)
        self.assertIn("volumeInGb: 5", result)
        self.assertIn('volumeMountPath: "/path/to/volume"', result)
        self.assertIn('ports: "8000,8001"', result)
        self.assertIn(
            'env: [{ key: "VAR1", value: "val1" }, { key: "VAR2", value: "val2" }]',
            result,
        )
        self.assertIn("isServerless: true", result)
        self.assertIn('containerRegistryAuthId : "test_auth"', result)
