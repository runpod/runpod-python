"""compatibility locks: the legacy surface must not change.

these tests intentionally assert exact signatures and behaviors of the
pre-apps sdk. if one fails, the apps work has leaked into the legacy
surface, which is a release blocker.
"""

import inspect
import unittest
from unittest.mock import patch

import runpod
from runpod.endpoint.runner import Endpoint, Job, RunPodClient


class TestEndpointSignatureLock(unittest.TestCase):
    """runpod.Endpoint(id).run_sync() is byte-for-byte the legacy client."""

    def test_endpoint_is_runner_endpoint(self):
        self.assertIs(runpod.Endpoint, Endpoint)

    def test_constructor_signature(self):
        params = list(inspect.signature(Endpoint.__init__).parameters)
        self.assertEqual(params, ["self", "endpoint_id", "api_key"])

    def test_run_sync_signature(self):
        params = list(inspect.signature(Endpoint.run_sync).parameters)
        self.assertEqual(params, ["self", "request_input", "timeout"])
        default = inspect.signature(Endpoint.run_sync).parameters["timeout"].default
        self.assertEqual(default, 86400)

    def test_run_signature(self):
        params = list(inspect.signature(Endpoint.run).parameters)
        self.assertEqual(params, ["self", "request_input"])

    def test_endpoint_methods_present(self):
        for method in ("run", "run_sync", "health", "purge_queue"):
            self.assertTrue(callable(getattr(Endpoint, method)))

    def test_job_methods_present(self):
        for method in ("status", "output", "stream", "cancel"):
            self.assertTrue(callable(getattr(Job, method)))

    @patch.object(RunPodClient, "post")
    @patch("runpod.api_key", "test-key")
    def test_run_sync_behavior(self, mock_post):
        mock_post.return_value = {
            "id": "job-1",
            "status": "COMPLETED",
            "output": {"result": 42},
        }
        endpoint = Endpoint("ep-abc", api_key="test-key")
        result = endpoint.run_sync({"prompt": "hi"})
        self.assertEqual(result, {"result": 42})
        mock_post.assert_called_once_with(
            "ep-abc/runsync", {"input": {"prompt": "hi"}}, timeout=86400
        )

    @patch.object(RunPodClient, "post")
    @patch("runpod.api_key", "test-key")
    def test_run_returns_job(self, mock_post):
        mock_post.return_value = {"id": "job-2"}
        endpoint = Endpoint("ep-abc", api_key="test-key")
        job = endpoint.run({"prompt": "hi"})
        self.assertIsInstance(job, Job)
        self.assertEqual(job.job_id, "job-2")


class TestLegacyFlatVerbsPresent(unittest.TestCase):
    """the flat api verbs stay importable from the package root."""

    def test_flat_verbs(self):
        for verb in (
            "create_pod",
            "get_pods",
            "get_pod",
            "stop_pod",
            "resume_pod",
            "terminate_pod",
            "create_endpoint",
            "get_endpoints",
            "create_template",
            "get_gpus",
            "get_gpu",
            "get_user",
            "update_user_settings",
            "update_endpoint_template",
            "create_container_registry_auth",
            "update_container_registry_auth",
            "delete_container_registry_auth",
        ):
            self.assertTrue(
                callable(getattr(runpod, verb)), f"runpod.{verb} missing"
            )

    def test_asyncio_endpoint_present(self):
        self.assertTrue(hasattr(runpod, "AsyncioEndpoint"))
        self.assertTrue(hasattr(runpod, "AsyncioJob"))

    def test_serverless_start_present(self):
        self.assertTrue(callable(runpod.serverless.start))


if __name__ == "__main__":
    unittest.main()
