'''
Test Serverless Job Module
'''

import unittest

import pytest
from aiohttp import ClientResponse
from aiohttp.test_utils import make_mocked_coro
from unittest.mock import Mock, patch, mock_open

from runpod.serverless.modules import job as job_module

class TestJob:
    ''' Tests the Job class. '''

    @pytest.mark.asyncio
    async def test_get_job_test_input(self):
        '''
        Tests the get_job function
        '''
        # Mock the response status and .json method
        response = Mock(ClientResponse)
        response.status = 200
        response.json = make_mocked_coro(return_value={"id": "123"})

        # Mock the config
        rp_args = {
                "test_input": {"input": {"number": 1}},
        }

        with patch("aiohttp.ClientSession") as mock_session, \
            patch("runpod.serverless.modules.job.log", new_callable=Mock) as mock_log, \
            patch("runpod.serverless.modules.job.IS_LOCAL_TEST", False), \
            patch("runpod.serverless.modules.job.JOB_GET_URL", "http://mock.url"):

            mock_session.get.return_value.__aenter__.return_value = response

            job = await job_module.get_job(mock_session, rp_args)

            # Assertions for the success case
            assert job == {"id": "test_input_provided", "input": {"number": 1}}
            assert mock_log.debug.call_count == 1
            assert mock_log.warn.call_count == 1
            assert mock_log.error.call_count == 0


    @pytest.mark.asyncio
    async def test_get_job_is_local_test(self):
        '''
        Tests the get_job function
        '''
        rp_args_empty = {"test_input": None}

        with patch("runpod.serverless.modules.job.IS_LOCAL_TEST", True), \
            patch("runpod.serverless.modules.job.os.path.exists") as mock_exists, \
            patch("runpod.serverless.modules.job.sys") as mock_sys, \
            patch("builtins.open", mock_open(read_data='{"input":{"number":1}}')) as mock_file:

            mock_exists.return_value = False

            await job_module.get_job(None, rp_args_empty)

            # Assertions for the success case
            # assert job == {"id": "local_test", "input": {"number": 1}}
            assert mock_exists.call_count == 1
            assert mock_sys.exit.call_count == 1

            mock_exists.return_value = True
            job = await job_module.get_job(None, rp_args_empty)

            assert mock_file.call_count == 2
            assert job["id"] == "local_test"


    @pytest.mark.asyncio
    async def test_get_job_default(self):
        '''
        Tests the get_job function
        '''
        # Mock the response status and .json method
        response = Mock(ClientResponse)
        response.status = 200
        response.json = make_mocked_coro(return_value={"id": "123"})

        # Mock the config
        rp_args = {
                "test_input": None,
        }

        with patch("aiohttp.ClientSession") as mock_session, \
            patch("runpod.serverless.modules.job.log", new_callable=Mock) as mock_log, \
            patch("runpod.serverless.modules.job.IS_LOCAL_TEST", False), \
            patch("runpod.serverless.modules.job.JOB_GET_URL", "http://mock.url"):

            mock_session.get.return_value.__aenter__.return_value = response
            job = await job_module.get_job(mock_session, rp_args)

            # Assertions for the success case
            assert job == {"id": "123"}
            assert mock_log.debug.call_count == 2
            assert mock_log.warn.call_count == 0
            assert mock_log.error.call_count == 0

    @pytest.mark.asyncio
    async def test_get_job_204(self):
        '''
        Tests the get_job function with a 204 response
        '''
        # 204 Mock
        response_204 = Mock(ClientResponse)
        response_204.status = 204
        response_204.json = make_mocked_coro(return_value=None)

        with patch("aiohttp.ClientSession") as mock_session_204, \
            patch("runpod.serverless.modules.job.IS_LOCAL_TEST", False), \
            patch("runpod.serverless.modules.job.JOB_GET_URL", "http://mock.url"):

            mock_session_204.get.return_value.__aenter__.return_value = response_204
            job = await job_module.get_job(mock_session_204, {"test_input": None})

            assert job is None

    @pytest.mark.asyncio
    async def test_get_job_500(self):
        '''
        Tests the get_job function with a 500 response
        '''
        # 500 Mock
        response_500 = Mock(ClientResponse)
        response_500.status = 500

        with patch("aiohttp.ClientSession") as mock_session_500, \
            patch("runpod.serverless.modules.job.IS_LOCAL_TEST", False), \
            patch("runpod.serverless.modules.job.JOB_GET_URL", "http://mock.url"):

            mock_session_500.get.return_value.__aenter__.return_value = response_500
            job = await job_module.get_job(mock_session_500, {"test_input": None})

            assert job is None


    @pytest.mark.asyncio
    async def test_get_job_no_id(self):
        response = Mock(ClientResponse)
        response.status = 200
        response.json = make_mocked_coro(return_value={})

        rp_args = {
                "test_input": None,
        }

        with patch("aiohttp.ClientSession") as mock_session, \
            patch("runpod.serverless.modules.job.log", new_callable=Mock) as mock_log, \
            patch("runpod.serverless.modules.job.IS_LOCAL_TEST", False), \
            patch("runpod.serverless.modules.job.JOB_GET_URL", "http://mock.url"):

            mock_session.get.return_value.__aenter__.return_value = response


            job = await job_module.get_job(mock_session, rp_args)

            # Assertions for the case when the job doesn't have an id
            assert job is None
            assert mock_log.error.call_count == 1

    @pytest.mark.asyncio
    async def test_get_job_exception(self):
        '''
        Tests the get_job function with an exception
        '''
        # Exception Mock
        response_exception = Mock(ClientResponse)
        response_exception.status = 200

        with patch("aiohttp.ClientSession") as mock_session_exception, \
            patch("runpod.serverless.modules.job.log", new_callable=Mock) as mock_log, \
            patch("runpod.serverless.modules.job.IS_LOCAL_TEST", False), \
            patch("runpod.serverless.modules.job.JOB_GET_URL", "http://mock.url"):

            mock_session_exception.get.return_value.__aenter__.side_effect = Exception
            job = await job_module.get_job(mock_session_exception, {"test_input": None})

            assert job is None
            assert mock_log.error.call_count == 1

class TestRunJob(unittest.TestCase):

    def setUp(self) -> None:
        self.sample_job = {
            "id": "123",
            "input": {
                "test_input": None,
            }
        }

    def test_simple_job(self):
        '''
        Tests the run_job function
        '''
        mock_handler = Mock()
        mock_handler.return_value = "test"

        job_result = job_module.run_job(mock_handler, self.sample_job)

        assert job_result == {"output": "test"}

    def test_job_with_errors(self):
        '''
        Tests the run_job function with errors
        '''
        mock_handler = Mock()
        mock_handler.return_value = {"error": "test"}

        job_result = job_module.run_job(mock_handler, self.sample_job)

        assert job_result == {"error": "test"}

    def test_job_with_refresh_worker(self):
        '''
        Tests the run_job function with refresh_worker
        '''
        mock_handler = Mock()
        mock_handler.return_value = {"refresh_worker": True}

        job_result = job_module.run_job(mock_handler, self.sample_job)

        assert job_result["stopPod"] is True

    def test_job_bool_output(self):
        '''
        Tests the run_job function with a boolean output
        '''
        mock_handler = Mock()
        mock_handler.return_value = True

        job_result = job_module.run_job(mock_handler, self.sample_job)

        assert job_result == {"output": True}

    def test_job_with_exception(self):
        '''
        Tests the run_job function with an exception
        '''
        mock_handler = Mock()
        mock_handler.side_effect = Exception

        job_result = job_module.run_job(mock_handler, self.sample_job)

        self.assertRaises(Exception, job_result)


class TestRunJobGenerator(unittest.TestCase):
    ''' Tests the run_job_generator function '''

    def handler_success(self, job):
        '''
        Test handler that returns a generator
        '''
        yield "partial_output_1"
        yield "partial_output_2"

    def handler_fail(self, job):
        '''
        Test handler that raises an exception
        '''
        raise Exception("Test Exception")

    def test_run_job_generator_success(self):
        '''
        Tests the run_job_generator function with a successful generator
        '''
        handler = self.handler_success
        job = {"id": "123"}

        with patch("runpod.serverless.modules.job.log", new_callable=Mock) as mock_log:
            result = list(job_module.run_job_generator(handler, job))

        assert result == [{"output": "partial_output_1"}, {"output": "partial_output_2"}]
        assert mock_log.error.call_count == 0
        assert mock_log.info.call_count == 1
        mock_log.info.assert_called_with('123 | Finished ')

    def test_run_job_generator_exception(self):
        '''
        Tests the run_job_generator function with an exception
        '''
        handler = self.handler_fail
        job = {"id": "123"}

        with patch("runpod.serverless.modules.job.log", new_callable=Mock) as mock_log:
            result = list(job_module.run_job_generator(handler, job))

        assert len(result) == 1
        assert "error" in result[0]
        assert mock_log.error.call_count == 1
        assert mock_log.info.call_count == 1
        mock_log.info.assert_called_with('123 | Finished ')
