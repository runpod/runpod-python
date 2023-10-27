''' Tests for runpod.serverless.modules.rp_fastapi.py '''
import os
import asyncio

import unittest
from unittest.mock import patch, Mock
import pytest

import runpod
from runpod.serverless.modules import rp_fastapi

class TestFastAPI(unittest.TestCase):
    ''' Tests the FastAPI '''

    def setUp(self) -> None:
        self.handler = Mock()
        self.handler.return_value = {"result": "success"}

    def test_start_serverless_with_realtime(self):
        '''
        Tests the start_serverless() method with the realtime option.
        '''
        module_location = "runpod.serverless.modules.rp_fastapi"
        with patch(f"{module_location}.Heartbeat.start_ping", Mock()) as mock_ping, \
            patch(f"{module_location}.FastAPI", Mock()) as mock_fastapi, \
            patch(f"{module_location}.APIRouter", return_value=Mock()) as mock_router, \
            patch(f"{module_location}.uvicorn", Mock()) as mock_uvicorn:


            rp_fastapi.RUNPOD_REALTIME_PORT = '1111'
            rp_fastapi.RUNPOD_ENDPOINT_ID = 'test_endpoint_id'

            os.environ["RUNPOD_REALTIME_PORT"] = '1111'
            os.environ["RUNPOD_ENDPOINT_ID"] = 'test_endpoint_id'

            runpod.serverless.start({"handler": self.handler})

            self.assertTrue(mock_ping.called)

            self.assertTrue(mock_fastapi.called)
            self.assertTrue(mock_router.called)

            self.assertTrue(rp_fastapi.RUNPOD_ENDPOINT_ID == 'test_endpoint_id')
            self.assertTrue(mock_router.return_value.add_api_route.called)

            self.assertTrue(mock_uvicorn.run.called)


    @pytest.mark.asyncio
    def test_run(self):
        '''
        Tests the _run() method.
        '''
        loop = asyncio.get_event_loop()

        module_location = "runpod.serverless.modules.rp_fastapi"
        with patch(f"{module_location}.Heartbeat.start_ping", Mock()) as mock_ping, \
            patch(f"{module_location}.FastAPI", Mock()), \
            patch(f"{module_location}.APIRouter", return_value=Mock()), \
            patch(f"{module_location}.uvicorn", Mock()):

            job_object = rp_fastapi.Job(
                id="test_job_id",
                input={"test_input": "test_input"}
            )

            # Test without handler
            worker_api_without_handler = rp_fastapi.WorkerAPI()

            handlerless_run_return = asyncio.run(worker_api_without_handler._run(job_object)) # pylint: disable=protected-access
            assert handlerless_run_return == {"error": "Handler not provided"}

            handlerless_debug_run = asyncio.run(worker_api_without_handler._debug_run(job_object)) # pylint: disable=protected-access
            assert handlerless_debug_run == {"error": "Handler not provided"}

            # Test with handler
            worker_api = rp_fastapi.WorkerAPI(handler=self.handler)

            run_return = asyncio.run(worker_api._run(job_object)) # pylint: disable=protected-access
            assert run_return == {"output": {"result": "success"}}

            debug_run_return = asyncio.run(worker_api._debug_run(job_object)) # pylint: disable=protected-access
            assert debug_run_return == {
                        "id": "test_job_id",
                        "output": {"result": "success"}
                    }

            self.assertTrue(mock_ping.called)

            # Test with generator handler
            def generator_handler(job):
                del job
                yield {"result": "success"}

            generator_worker_api = rp_fastapi.WorkerAPI(handler=generator_handler)
            generator_run_return = asyncio.run(generator_worker_api._debug_run(job_object)) # pylint: disable=protected-access
            assert generator_run_return == {
                    "id": "test_job_id",
                    "output": [{"result": "success"}]
                }

        loop.close()
