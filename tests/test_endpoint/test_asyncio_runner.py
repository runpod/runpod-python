''' Unit tests for the asyncio_runner module. '''
# pylint: disable=too-few-public-methods


import tracemalloc
import asyncio
import unittest
from unittest.mock import patch, MagicMock
from unittest import IsolatedAsyncioTestCase
import pytest

from runpod.endpoint.asyncio.asyncio_runner import Job, Endpoint

tracemalloc.start()


class TestJob(IsolatedAsyncioTestCase):
    ''' Tests the Job class. '''

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_output(self):
        '''
        Tests Job.output
        '''
        with (
            patch("runpod.endpoint.asyncio.asyncio_runner.asyncio.sleep") as mock_sleep,
            patch("aiohttp.ClientSession") as mock_session
        ):
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

    @pytest.mark.asyncio
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

class TestEndpoint:
    ''' Unit tests for the Endpoint class. '''

    @pytest.mark.asyncio
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

if __name__ == "__main__":
    unittest.main()

    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics('lineno')

    print("[ Top 10 ]")
    for stat in top_stats[:10]:
        print(stat)
