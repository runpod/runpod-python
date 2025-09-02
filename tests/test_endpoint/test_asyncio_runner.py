"""Unit tests for the asyncio_runner module."""

# pylint: disable=too-few-public-methods

import asyncio
import tracemalloc
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from runpod.endpoint.asyncio.asyncio_runner import Endpoint, Job

tracemalloc.start()


class TestJob(IsolatedAsyncioTestCase):
    """Tests the Job class."""

    def setUp(self):
        """Set up test fixtures"""
        import runpod
        runpod.api_key = "MOCK_API_KEY"

    async def test_status(self):
        """
        Tests Job.status
        """
        with patch(
            "aiohttp.ClientSession", new_callable=AsyncMock
        ) as mock_session_class:
            mock_session = mock_session_class.return_value
            mock_get = mock_session.get
            mock_resp = AsyncMock()

            mock_resp.json.return_value = {"status": "COMPLETED"}
            mock_get.return_value = mock_resp

            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer MOCK_API_KEY",
                "X-Request-ID": "job_id",
            }
            job = Job("endpoint_id", "job_id", mock_session, headers)
            status = await job.status()
            assert status == "COMPLETED"
            assert await job.status() == "COMPLETED"

    async def test_output(self):
        """
        Tests Job.output
        """
        with patch(
            "runpod.endpoint.asyncio.asyncio_runner.asyncio.sleep"
        ) as mock_sleep, patch(
            "aiohttp.ClientSession", new_callable=AsyncMock
        ) as mock_session_class:
            mock_session = mock_session_class.return_value
            mock_get = mock_session.get
            mock_resp = AsyncMock()

            async def json_side_effect():
                if mock_sleep.call_count == 0:
                    return {"status": "IN_PROGRESS"}
                return {"output": "OUTPUT", "status": "COMPLETED"}

            mock_resp.json.side_effect = json_side_effect
            mock_get.return_value = mock_resp

            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer MOCK_API_KEY",
                "X-Request-ID": "job_id",
            }
            job = Job("endpoint_id", "job_id", mock_session, headers)
            output_task = asyncio.create_task(job.output(timeout=3))

            output = await output_task
            assert output == "OUTPUT"
            assert await job.output() == "OUTPUT"

    async def test_output_timeout(self):
        """
        Tests Job.output with a timeout
        """
        with patch(
            "aiohttp.ClientSession", new_callable=AsyncMock
        ) as mock_session_class:
            mock_session = mock_session_class.return_value
            mock_get = mock_session.get
            mock_resp = AsyncMock()

            mock_resp.json.return_value = {"status": "IN_PROGRESS"}
            mock_get.return_value = mock_resp

            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer MOCK_API_KEY",
                "X-Request-ID": "job_id",
            }
            job = Job("endpoint_id", "job_id", mock_session, headers)
            output_task = asyncio.create_task(job.output(timeout=1))

            with self.assertRaises(TimeoutError):
                await output_task

    async def test_stream(self):
        """
        Tests Job.stream
        """
        with patch(
            "aiohttp.ClientSession", new_callable=AsyncMock
        ) as mock_session_class:
            mock_session = mock_session_class.return_value
            mock_get = mock_session.get
            mock_resp = AsyncMock()

            responses = [
                {"stream": [{"output": "OUTPUT1"}], "status": "IN_PROGRESS"},
                {"stream": [{"output": "OUTPUT2"}], "status": "IN_PROGRESS"},
            ]

            async def json_side_effect():
                return (
                    responses.pop(0)
                    if responses
                    else {"stream": [], "status": "COMPLETED"}
                )

            mock_resp.json.side_effect = json_side_effect
            mock_get.return_value = mock_resp

            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer MOCK_API_KEY",
                "X-Request-ID": "job_id",
            }
            job = Job("endpoint_id", "job_id", mock_session, headers)

            outputs = []
            async for stream_output in job.stream():
                outputs.append(stream_output)

            assert outputs == ["OUTPUT1", "OUTPUT2"]

    async def test_cancel(self):
        """
        Tests Job.cancel
        """
        with patch("aiohttp.ClientSession") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value=asyncio.Future())
            mock_resp.json.return_value.set_result({"result": "CANCELLED"})
            mock_session.post.return_value.__aenter__.return_value = mock_resp

            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer MOCK_API_KEY",
                "X-Request-ID": "job_id",
            }
            job = Job("endpoint_id", "job_id", mock_session, headers)
            cancel_result = await job.cancel()
            assert cancel_result == {"result": "CANCELLED"}

    async def test_output_in_progress_then_completed(self):
        """Tests Job.output when status is initially IN_PROGRESS and then changes to COMPLETED"""
        with patch(
            "aiohttp.ClientSession", new_callable=AsyncMock
        ) as mock_session_class:
            mock_session = mock_session_class.return_value
            mock_get = mock_session.get
            mock_resp = AsyncMock()

            responses = [
                {"status": "IN_PROGRESS"},
                {"status": "COMPLETED", "output": "OUTPUT"},
            ]

            async def json_side_effect():
                return (
                    responses.pop(0)
                    if responses
                    else {"status": "COMPLETED", "output": "OUTPUT"}
                )  # pylint: disable=line-too-long

            mock_resp.json.side_effect = json_side_effect
            mock_get.return_value = mock_resp

            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer MOCK_API_KEY",
                "X-Request-ID": "job_id",
            }
            job = Job("endpoint_id", "job_id", mock_session, headers)
            output = await job.output(timeout=3)
            assert output == "OUTPUT"


class TestEndpoint(IsolatedAsyncioTestCase):
    """Unit tests for the Endpoint class."""

    def setUp(self):
        """Set up test fixtures"""
        import runpod
        runpod.api_key = "MOCK_API_KEY"

    async def test_run(self):
        """
        Tests Endpoint.run
        """
        with patch("aiohttp.ClientSession") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value=asyncio.Future())
            mock_resp.json.return_value.set_result({"id": "job_id"})
            mock_session.post.return_value.__aenter__.return_value = mock_resp

            endpoint = Endpoint("endpoint_id", mock_session)
            job = await endpoint.run({"input": "INPUT"})
            assert job.job_id == "job_id"

    async def test_health(self):
        """
        Tests Endpoint.health
        """
        with patch("aiohttp.ClientSession") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value=asyncio.Future())
            mock_resp.json.return_value.set_result({"status": "HEALTHY"})
            mock_session.get.return_value.__aenter__.return_value = mock_resp

            endpoint = Endpoint("endpoint_id", mock_session)
            health = await endpoint.health()
            assert health == {"status": "HEALTHY"}

    async def test_purge_queue(self):
        """
        Tests Endpoint.purge_queue
        """
        with patch("aiohttp.ClientSession") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value=asyncio.Future())
            mock_resp.json.return_value.set_result({"result": "PURGED"})
            mock_session.post.return_value.__aenter__.return_value = mock_resp

            endpoint = Endpoint("endpoint_id", mock_session)
            purge_result = await endpoint.purge_queue()
            assert purge_result == {"result": "PURGED"}


class TestEndpointInitialization(unittest.TestCase):
    """Tests for the Endpoint class initialization."""

    def setUp(self):
        """Set up test fixtures"""
        import runpod
        runpod.api_key = "MOCK_API_KEY"

    def test_endpoint_initialization(self):
        """Tests initialization of Endpoint class."""
        with patch("aiohttp.ClientSession"):
            endpoint = Endpoint("endpoint_id", MagicMock())
            self.assertEqual(
                endpoint.endpoint_url, "https://api.runpod.ai/v2/endpoint_id/run"
            )
            self.assertIn("Content-Type", endpoint.headers)
            self.assertIn("Authorization", endpoint.headers)


if __name__ == "__main__":
    unittest.main()

    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics("lineno")

    print("[ Top 10 ]")
    for stat in top_stats[:10]:
        print(stat)
