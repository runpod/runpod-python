""" Test API Wrapper Pod Mutations """

import unittest

from runpod.api.mutations import pods


class TestPodMutations(unittest.TestCase):
    """Test API Wrapper Pod Mutations"""

    def test_generate_pod_deployment_mutation(self):
        """
        Test generate_pod_deployment_mutation for both GPU and CPU pods
        """
        # Test GPU pod deployment
        gpu_result = pods.generate_pod_deployment_mutation(
            name="test",
            image_name="test_image", 
            gpu_type_id="1",
            cloud_type="cloud",
            data_center_id="1",
            country_code="US",
            gpu_count=1,
            volume_in_gb=100,
            container_disk_in_gb=10,
            min_vcpu_count=1,
            min_memory_in_gb=1,
            docker_args="args",
            ports="8080",
            volume_mount_path="/path",
            env={"ENV": "test"},
            support_public_ip=True,
            template_id="abcde",
            allowed_cuda_versions=["11.8", "12.0"],
        )

        # Test CPU pod deployment
        cpu_result = pods.generate_pod_deployment_mutation(
            name="test-cpu",
            image_name="test_image",
            cloud_type="cloud",
            data_center_id="1",
            country_code="US",
            volume_in_gb=100,
            container_disk_in_gb=10,
            min_vcpu_count=2,
            min_memory_in_gb=4,
            docker_args="args",
            ports="8080",
            volume_mount_path="/path",
            env={"ENV": "test"},
            instance_id="cpu3c-2-4"
        )

        # Check GPU pod mutation structure
        self.assertIn("mutation", gpu_result)
        self.assertIn("podFindAndDeployOnDemand", gpu_result)
        
        # Check CPU pod mutation structure  
        self.assertIn("mutation", cpu_result)
        self.assertIn("deployCpuPod", cpu_result)

    def test_generate_pod_stop_mutation(self):
        """
        Test generate_pod_stop_mutation
        """
        result = pods.generate_pod_stop_mutation("pod_id")
        # Here you should check the correct structure of the result
        self.assertIn("mutation", result)

    def test_generate_pod_resume_mutation(self):
        """
        Test generate_pod_resume_mutation
        """
        result = pods.generate_pod_resume_mutation("pod_id", 1)
        # Here you should check the correct structure of the result
        self.assertIn("mutation", result)

    def test_generate_pod_terminate_mutation(self):
        """
        Test generate_pod_terminate_mutation
        """
        result = pods.generate_pod_terminate_mutation("pod_id")
        # Here you should check the correct structure of the result
        self.assertIn("mutation", result)


if __name__ == "__main__":
    unittest.main()
