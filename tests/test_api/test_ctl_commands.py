""" Tests for ctl_commands.py """

import unittest
from unittest.mock import patch

from runpod.api import ctl_commands


class TestCTL(unittest.TestCase):
    """Tests for CTL Commands"""

    def setUp(self):
        """Set up test fixtures"""
        import runpod
        runpod.api_key = "MOCK_API_KEY"

    def test_get_user(self):
        """
        Tests get_user
        """
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "myself": {
                        "id": "USER_ID",
                    }
                }
            }

            user = ctl_commands.get_user()
            self.assertEqual(user["id"], "USER_ID")

    def test_update_user_settings(self):
        """
        Tests update_user_settings
        """
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "updateUserSettings": {"id": "USER_ID", "publicKey": "PUBLIC_KEY"}
                }
            }

            user = ctl_commands.update_user_settings("PUBLIC_KEY")
            self.assertEqual(user["id"], "USER_ID")
            self.assertEqual(user["publicKey"], "PUBLIC_KEY")

    def test_get_gpus(self):
        """
        Tests get_gpus
        """
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "gpuTypes": [
                        {
                            "id": "NVIDIA A100 80GB PCIe",
                            "displayName": "A100 80GB",
                            "memoryInGb": 80,
                        }
                    ]
                }
            }

            gpus = ctl_commands.get_gpus()

            self.assertEqual(len(gpus), 1)
            self.assertEqual(gpus[0]["id"], "NVIDIA A100 80GB PCIe")

    def test_get_gpu(self):
        """
        Tests get_gpu_by_id
        """
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "gpuTypes": [
                        {
                            "id": "NVIDIA A100 80GB PCIe",
                            "displayName": "A100 80GB",
                            "memoryInGb": 80,
                        }
                    ]
                }
            }

            gpu = ctl_commands.get_gpu("NVIDIA A100 80GB PCIe")
            self.assertEqual(gpu["id"], "NVIDIA A100 80GB PCIe")

            patch_request.return_value.json.return_value = {"data": {"gpuTypes": []}}

            with self.assertRaises(ValueError) as context:
                gpu = ctl_commands.get_gpu("Not a GPU")

            self.assertEqual(
                str(context.exception),
                "No GPU found with the specified ID, "
                "run runpod.get_gpus() to get a list of all GPUs",
            )

    def test_create_pod(self):
        """
        Tests create_pod
        """
        with patch("runpod.api.graphql.requests.post") as patch_request, patch(
            "runpod.api.ctl_commands.get_gpu"
        ) as patch_get_gpu, patch("runpod.api.ctl_commands.get_user") as patch_get_user:
            patch_request.return_value.json.return_value = {
                "data": {"podFindAndDeployOnDemand": {"id": "POD_ID"}}
            }

            patch_get_gpu.return_value = None

            patch_get_user.return_value = {
                "networkVolumes": [
                    {"id": "NETWORK_VOLUME_ID", "dataCenterId": "us-east-1"}
                ]
            }

            pod = ctl_commands.create_pod(
                name="POD_NAME",
                image_name="IMAGE_NAME",
                support_public_ip=False,
                gpu_type_id="NVIDIA A100 80GB PCIe",
                network_volume_id="NETWORK_VOLUME_ID",
            )

            self.assertEqual(pod["id"], "POD_ID")

            with self.assertRaises(ValueError) as context:
                pod = ctl_commands.create_pod(
                    name="POD_NAME",
                    cloud_type="NOT_A_CLOUD_TYPE",
                    image_name="IMAGE_NAME",
                    gpu_type_id="NVIDIA A100 80GB PCIe",
                    network_volume_id="NETWORK_VOLUME_ID",
                )

            self.assertEqual(
                str(context.exception),
                "cloud_type must be one of ALL, COMMUNITY or SECURE",
            )

            with self.assertRaises(ValueError) as context:
                pod = ctl_commands.create_pod(
                    name="POD_NAME",
                    gpu_type_id="NVIDIA A100 80GB PCIe",
                    network_volume_id="NETWORK_VOLUME_ID",
                )

            self.assertEqual(
                str(context.exception),
                "Either image_name or template_id must be provided",
            )

    def test_stop_pod(self):
        """
        Test stop_pod
        """
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {"podStop": {"id": "POD_ID"}}
            }

            pod = ctl_commands.stop_pod(pod_id="POD_ID")

            self.assertEqual(pod["id"], "POD_ID")

    def test_resume_pod(self):
        """
        Test resume_pod
        """
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {"podResume": {"id": "POD_ID"}}
            }

            pod = ctl_commands.resume_pod(pod_id="POD_ID", gpu_count=1)

            self.assertEqual(pod["id"], "POD_ID")

    def test_terminate_pod(self):
        """
        Test terminate_pod
        """
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {"podTerminate": {"id": "POD_ID"}}
            }

            self.assertIsNone(ctl_commands.terminate_pod(pod_id="POD_ID"))

    def test_raised_error(self):
        """
        Test raised_error
        """
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "errors": [{"message": "Error Message"}]
            }

            with self.assertRaises(Exception) as context:
                ctl_commands.get_gpus()

            self.assertEqual(str(context.exception), "Error Message")

        # Test Unauthorized with status code 401
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.status_code = 401

            with self.assertRaises(Exception) as context:
                ctl_commands.get_gpus()

            self.assertEqual(
                str(context.exception),
                "Unauthorized request, please check your API key.",
            )

    def test_get_pods(self):
        """
        Tests get_pods
        """
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "myself": {
                        "pods": [
                            {
                                "id": "POD_ID",
                                "containerDiskInGb": 5,
                                "costPerHr": 0.34,
                                "desiredStatus": "RUNNING",
                                "dockerArgs": None,
                                "dockerId": None,
                                "env": [],
                                "gpuCount": 1,
                                "imageName": "runpod/pytorch:2.0.1-py3.10-cuda11.8.0-devel",
                                "lastStatusChange": "Rented by User: Tue Aug 15 2023",
                                "machineId": "MACHINE_ID",
                                "memoryInGb": 83,
                                "name": "POD_NAME",
                                "podType": "RESERVED",
                                "port": None,
                                "ports": "80/http",
                                "uptimeSeconds": 0,
                                "vcpuCount": 21,
                                "volumeInGb": 200,
                                "volumeMountPath": "/workspace",
                                "machine": {"gpuDisplayName": "RTX 3090"},
                            }
                        ]
                    }
                }
            }

            pods = ctl_commands.get_pods()

            self.assertEqual(len(pods), 1)
            self.assertEqual(pods[0]["id"], "POD_ID")

    def test_get_pod(self):
        """
        Tests get_pods
        """
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "pod": {
                        "id": "POD_ID",
                        "containerDiskInGb": 5,
                        "costPerHr": 0.34,
                        "desiredStatus": "RUNNING",
                        "dockerArgs": None,
                        "dockerId": None,
                        "env": [],
                        "gpuCount": 1,
                        "imageName": "runpod/pytorch:2.0.1-py3.10-cuda11.8.0-devel",
                        "lastStatusChange": "Rented by User: Tue Aug 15 2023",
                        "machineId": "MACHINE_ID",
                        "memoryInGb": 83,
                        "name": "POD_NAME",
                        "podType": "RESERVED",
                        "port": None,
                        "ports": "80/http",
                        "uptimeSeconds": 0,
                        "vcpuCount": 21,
                        "volumeInGb": 200,
                        "volumeMountPath": "/workspace",
                        "machine": {"gpuDisplayName": "RTX 3090"},
                    }
                }
            }

            pods = ctl_commands.get_pod("POD_ID")

            self.assertEqual(pods["id"], "POD_ID")

    def test_create_template(self):
        """
        Tests create_template
        """
        with patch("runpod.api.graphql.requests.post") as patch_request, patch(
            "runpod.api.ctl_commands.get_gpu"
        ) as patch_get_gpu:
            patch_request.return_value.json.return_value = {
                "data": {"saveTemplate": {"id": "TEMPLATE_ID"}}
            }

            patch_get_gpu.return_value = None

            template = ctl_commands.create_template(
                name="TEMPLATE_NAME", image_name="IMAGE_NAME"
            )

            self.assertEqual(template["id"], "TEMPLATE_ID")

    def test_get_endpoints(self):
        """
        Tests get_endpoints
        """
        with patch("runpod.api.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "myself": {
                        "endpoints": [
                            {
                                "id": "ENDPOINT_ID",
                                "name": "ENDPOINT_NAME",
                                "template": {
                                    "id": "TEMPLATE_ID",
                                    "imageName": "IMAGE_NAME",
                                },
                            }
                        ]
                    }
                }
            }

            endpoints = ctl_commands.get_endpoints()

            self.assertEqual(len(endpoints), 1)
            self.assertEqual(endpoints[0]["id"], "ENDPOINT_ID")

    def test_create_endpoint(self):
        """
        Tests create_endpoint
        """
        with patch("runpod.api.graphql.requests.post") as patch_request, patch(
            "runpod.api.ctl_commands.get_gpu"
        ) as patch_get_gpu:
            patch_request.return_value.json.return_value = {
                "data": {"saveEndpoint": {"id": "ENDPOINT_ID"}}
            }

            patch_get_gpu.return_value = None

            endpoint = ctl_commands.create_endpoint(
                name="ENDPOINT_NAME", template_id="TEMPLATE_ID"
            )

            self.assertEqual(endpoint["id"], "ENDPOINT_ID")

    def test_update_endpoint_template(self):
        """
        Tests update_endpoint_template
        """
        with patch("runpod.api.graphql.requests.post") as patch_request, patch(
            "runpod.api.ctl_commands.get_gpu"
        ) as patch_get_gpu:
            patch_request.return_value.json.return_value = {
                "data": {"updateEndpointTemplate": {"id": "ENDPOINT_ID"}}
            }

            patch_get_gpu.return_value = None

            endpoint = ctl_commands.update_endpoint_template(
                endpoint_id="ENDPOINT_ID", template_id="TEMPLATE_ID"
            )

            self.assertEqual(endpoint["id"], "ENDPOINT_ID")

    @patch("runpod.api.ctl_commands.run_graphql_query")
    def test_create_container_registry_auth(self, mock_run_graphql_query):
        """
        Tests create_container_registry_auth by mocking the run_graphql_query function
        """
        # Set up the mock to return a predefined response
        mock_run_graphql_query.return_value = {
            "data": {
                "saveRegistryAuth": {"id": "REGISTRY_AUTH_ID", "name": "REGISTRY_NAME"}
            }
        }

        # Call the function under test with dummy arguments
        result = ctl_commands.create_container_registry_auth(
            name="REGISTRY_NAME", username="username", password="password"
        )

        # Assertions to verify the function behavior
        self.assertEqual(result["id"], "REGISTRY_AUTH_ID")
        self.assertEqual(result["name"], "REGISTRY_NAME")

        # Verify that run_graphql_query was called with the correct parameters
        mock_run_graphql_query.assert_called_once()  # Ensure it was called exactly once

        # Access the first (and only) call's arguments directly
        called_args, _ = mock_run_graphql_query.call_args

        # The GraphQL query is expected to be the first positional argument in the call
        graphql_query = called_args[0]

        self.assertIn("mutation SaveRegistryAuth", graphql_query)
        self.assertIn("REGISTRY_NAME", graphql_query)
        self.assertIn("username", graphql_query)
        self.assertIn("password", graphql_query)
