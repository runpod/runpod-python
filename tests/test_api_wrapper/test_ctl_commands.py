''' Tests for ctl_commands.py '''

import unittest

from unittest.mock import patch

from runpod.api_wrapper import ctl_commands

class TestCTL(unittest.TestCase):
    ''' Tests for CTL Commands '''

    def test_get_gpus(self):
        '''
        Tests get_gpus
        '''
        with patch("runpod.api_wrapper.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "gpuTypes": [
                        {
                            "id": "NVIDIA A100 80GB PCIe",
                            "displayName": "A100 80GB",
                            "memoryInGb": 80
                        }
                    ]
                }
            }

            gpus = ctl_commands.get_gpus()

            self.assertEqual(len(gpus), 1)
            self.assertEqual(gpus[0]["id"], "NVIDIA A100 80GB PCIe")

    def test_get_gpu(self):
        '''
        Tests get_gpu_by_id
        '''
        with patch("runpod.api_wrapper.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "gpuTypes": [
                        {
                            "id": "NVIDIA A100 80GB PCIe",
                            "displayName": "A100 80GB",
                            "memoryInGb": 80
                        }
                    ]
                }
            }

            gpu = ctl_commands.get_gpu("NVIDIA A100 80GB PCIe")

            self.assertEqual(gpu["id"], "NVIDIA A100 80GB PCIe")

    def test_create_pod(self):
        '''
        Tests create_pod
        '''
        with patch("runpod.api_wrapper.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "podFindAndDeployOnDemand": {
                        "id": "POD_ID"
                    }
                }
            }

            pod = ctl_commands.create_pod(
                name="POD_NAME",
                image_name="IMAGE_NAME",
                gpu_type_id="NVIDIA A100 80GB PCIe")

            self.assertEqual(pod["id"], "POD_ID")


    def test_stop_pod(self):
        '''
        Test stop_pod
        '''
        with patch("runpod.api_wrapper.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "podStop": {
                        "id": "POD_ID"
                    }
                }
            }

            pod = ctl_commands.stop_pod(
                pod_id="POD_ID")

            self.assertEqual(pod["id"], "POD_ID")

    def test_resume_pod(self):
        '''
        Test resume_pod
        '''
        with patch("runpod.api_wrapper.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "podResume": {
                        "id": "POD_ID"
                    }
                }
            }

            pod = ctl_commands.resume_pod(pod_id="POD_ID", gpu_count=1)

            self.assertEqual(pod["id"], "POD_ID")

    def test_terminate_pod(self):
        '''
        Test terminate_pod
        '''
        with patch("runpod.api_wrapper.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "data": {
                    "podTerminate": {
                        "id": "POD_ID"
                    }
                }
            }

            self.assertIsNone(ctl_commands.terminate_pod(pod_id="POD_ID"))

    def test_raised_error(self):
        '''
        Test raised_error
        '''
        with patch("runpod.api_wrapper.graphql.requests.post") as patch_request:
            patch_request.return_value.json.return_value = {
                "errors": [
                    {
                        "message": "Error Message"
                    }
                ]
            }

            with self.assertRaises(Exception) as context:
                ctl_commands.get_gpus()

            self.assertEqual(str(context.exception), "Error Message")


        # Test Unauthorized with status code 401
        with patch("runpod.api_wrapper.graphql.requests.post") as patch_request:
            patch_request.return_value.status_code = 401

            with self.assertRaises(Exception) as context:
                ctl_commands.get_gpus()

            self.assertEqual(
                str(context.exception), "Unauthorized request, please check your API key.")

    def test_get_pods(self):
        '''
        Tests get_pods
        '''
        with patch("runpod.api_wrapper.graphql.requests.post") as patch_request:
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
                                "machine": { "gpuDisplayName": "RTX 3090" }
                            }
                        ]
                    }
                }
            }

            pods = ctl_commands.get_pods()

            self.assertEqual(len(pods), 1)
            self.assertEqual(pods[0]["id"], "POD_ID")

    def test_get_pod(self):
        '''
        Tests get_pods
        '''
        with patch("runpod.api_wrapper.graphql.requests.post") as patch_request:
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
                        "machine": { "gpuDisplayName": "RTX 3090" }
                    }
                }
            }

            pods = ctl_commands.get_pod("POD_ID")

            self.assertEqual(pods["id"], "POD_ID")
