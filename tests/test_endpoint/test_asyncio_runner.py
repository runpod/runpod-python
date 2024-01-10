''' Unit tests for the asyncio_runner module. '''
# pylint: disable=too-few-public-methods

import tracemalloc
import asyncio
import unittest
from unittest.mock import patch, MagicMock
from unittest import IsolatedAsyncioTestCase

from runpod.endpoint.asyncio.asyncio_runner import Job, Endpoint

tracemalloc.start()


class TestJob(IsolatedAsyncioTestCase):
    ''' Tests the Job class. '''

    async def test_status(self):
        '''
        Tests Job.status
        '''
        with patch("aiohttp.ClientSession") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value=asyncio.Future())
            mock_resp.json.return_value.set_result({"status": "COMPLETED"})
            mock_session.get.return_value.__aenter__.return_value = mock_resp

            job = Job("endpoint_id", "job_id", mock_session)
            status = await job.status()
            assert status == "COMPLETED"

    async def test_output(self):
        '''
        Tests Job.output
        '''
        with patch("runpod.endpoint.asyncio.asyncio_runner.asyncio.sleep") as mock_sleep, \
             patch("aiohttp.ClientSession") as mock_session:
            mock_resp = MagicMock()

            async def json_side_effect():
                if mock_sleep.call_count == 0:
                    return {"status": "IN_PROGRESS"}
                return {"output": "OUTPUT", "status": "COMPLETED"}

            mock_resp.json = json_side_effect
            mock_session.get.return_value.__aenter__.return_value = mock_resp

            job = Job("endpoint_id", "job_id", mock_session)
            output_task = asyncio.create_task(job.output())

            output = await output_task
            assert output == "OUTPUT"

    async def test_cancel(self):
        '''
        Tests Job.cancel
        '''
        with patch("aiohttp.ClientSession") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value=asyncio.Future())
            mock_resp.json.return_value.set_result({"result": "CANCELLED"})
            mock_session.post.return_value.__aenter__.return_value = mock_resp

            job = Job("endpoint_id", "job_id", mock_session)
            cancel_result = await job.cancel()
            assert cancel_result == {"result": "CANCELLED"}

    async def test_output_in_progress_then_completed(self):
        '''Tests Job.output when status is initially IN_PROGRESS and then changes to COMPLETED'''
        with patch("runpod.endpoint.asyncio.asyncio_runner.asyncio.sleep") as mock_sleep, \
             patch("aiohttp.ClientSession") as mock_session:
            mock_resp = MagicMock()
            responses = [
                {"status": "IN_PROGRESS"},
                {"status": "COMPLETED"},
                {"output": "OUTPUT"}
            ]

            async def json_side_effect():
                if responses:
                    return responses.pop(0)
                return {"status": "IN_PROGRESS"}

            mock_resp.json = json_side_effect
            mock_session.get.return_value.__aenter__.return_value = mock_resp

            job = Job("endpoint_id", "job_id", mock_session)
            output = await job.output()
            assert output == "OUTPUT"
            mock_sleep.assert_called_once_with(1)

class TestEndpoint(IsolatedAsyncioTestCase):
    ''' Unit tests for the Endpoint class. '''

    async def test_run(self):
        '''
        Tests Endpoint.run
        '''
        with patch("aiohttp.ClientSession") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value=asyncio.Future())
            mock_resp.json.return_value.set_result({"id": "job_id"})
            mock_session.post.return_value.__aenter__.return_value = mock_resp

            endpoint = Endpoint("endpoint_id", mock_session)
            job = await endpoint.run({"input": "INPUT"})
            assert job.job_id == "job_id"

class TestEndpointInitialization(unittest.TestCase):
    '''Tests for the Endpoint class initialization.'''

    def test_endpoint_initialization(self):
        '''Tests initialization of Endpoint class.'''
        with patch("aiohttp.ClientSession"):
            endpoint = Endpoint("endpoint_id", MagicMock())
            self.assertEqual(endpoint.endpoint_url, "https://api.runpod.ai/v2/endpoint_id/run")
            self.assertIn("Content-Type", endpoint.headers)
            self.assertIn("Authorization", endpoint.headers)

if __name__ == "__main__":
    unittest.main()

    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics('lineno')

    print("[ Top 10 ]")
    for stat in top_stats[:10]:
        print(stat)
