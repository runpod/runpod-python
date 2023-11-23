'''
Test Serverless Job Module
'''

from unittest.mock import Mock, patch

from unittest import IsolatedAsyncioTestCase
from aiohttp import ClientResponse
from aiohttp.test_utils import make_mocked_coro

from runpod.serverless.modules import rp_job


class TestJob(IsolatedAsyncioTestCase):
    ''' Tests the Job class. '''

    async def test_get_job_200(self):
        '''
        Tests the get_job function
        '''
        # Mock the non-200 response
        response1 = Mock()
        response1.status = 500
        response1.json = make_mocked_coro(return_value=None)

        # Mock the non-200 response
        response2 = Mock()
        response2.status = 400
        response2.json = make_mocked_coro(return_value=None)

        # Mock the non-200 response
        response3 = Mock()
        response3.status = 204
        response3.json = make_mocked_coro(return_value=None)

        # Mock the 200 response
        response4 = Mock()
        response4.status = 200
        response4.json = make_mocked_coro(return_value={"id": "123", "input": {"number": 1}})

        with patch("aiohttp.ClientSession") as mock_session, \
                patch("runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"):

            # Set side_effect to a list of mock responses
            mock_session.get.return_value.__aenter__.side_effect = [
                response1, response2, response3, response4
            ]

            job = await rp_job.get_job(mock_session, retry=True)

            # Assertions for the success case
            assert job == {"id": "123", "input": {"number": 1}}

    async def test_get_job_204(self):
        '''
        Tests the get_job function with a 204 response
        '''
        # 204 Mock
        response_204 = Mock()
        response_204.status = 204
        response_204.json = make_mocked_coro(return_value=None)

        with patch("aiohttp.ClientSession") as mock_session_204, \
                patch("runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"):

            mock_session_204.get.return_value.__aenter__.return_value = response_204
            job = await rp_job.get_job(mock_session_204, retry=False)

            assert job is None
            assert mock_session_204.get.call_count == 1

    async def test_get_job_400(self):
        '''
        Test the get_job function with a 400 response
        '''
        # 400 Mock
        response_400 = Mock(ClientResponse)
        response_400.status = 400

        with patch("aiohttp.ClientSession") as mock_session_400, \
                patch("runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"):

            mock_session_400.get.return_value.__aenter__.return_value = response_400
            job = await rp_job.get_job(mock_session_400, retry=False)

            assert job is None

    async def test_get_job_500(self):
        '''
        Tests the get_job function with a 500 response
        '''
        # 500 Mock
        response_500 = Mock(ClientResponse)
        response_500.status = 500

        with patch("aiohttp.ClientSession") as mock_session_500, \
                patch("runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"):

            mock_session_500.get.return_value.__aenter__.return_value = response_500
            job = await rp_job.get_job(mock_session_500, retry=False)

            assert job is None

    async def test_get_job_no_id(self):
        '''
        Tests the get_job function with a 200 response but no id
        '''
        response = Mock(ClientResponse)
        response.status = 200
        response.json = make_mocked_coro(return_value={})

        with patch("aiohttp.ClientSession") as mock_session, \
                patch("runpod.serverless.modules.rp_job.log", new_callable=Mock) as mock_log, \
                patch("runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"):

            mock_session.get.return_value.__aenter__.return_value = response

            job = await rp_job.get_job(mock_session, retry=False)

            assert job is None
            assert mock_log.error.call_count == 1

    async def test_get_job_no_input(self):
        '''
        Tests the get_job function with a 200 response but no input
        '''
        response = Mock(ClientResponse)
        response.status = 200
        response.json = make_mocked_coro(return_value={"id": "123"})

        with patch("aiohttp.ClientSession") as mock_session, \
                patch("runpod.serverless.modules.rp_job.log", new_callable=Mock) as mock_log, \
                patch("runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"):

            mock_session.get.return_value.__aenter__.return_value = response

            job = await rp_job.get_job(mock_session, retry=False)

            assert job is None
            assert mock_log.error.call_count == 1

    async def test_get_job_exception(self):
        '''
        Tests the get_job function with an exception
        '''
        # Exception Mock
        response_exception = Mock(ClientResponse)
        response_exception.status = 200

        with patch("aiohttp.ClientSession") as mock_session_exception, \
                patch("runpod.serverless.modules.rp_job.log", new_callable=Mock) as mock_log, \
                patch("runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"):

            mock_session_exception.get.return_value.__aenter__.side_effect = Exception
            job = await rp_job.get_job(mock_session_exception, retry=False)

            assert job is None
            assert mock_log.error.call_count == 2


class TestRunJob(IsolatedAsyncioTestCase):
    ''' Tests the run_job function '''

    def setUp(self) -> None:
        self.sample_job = {
            "id": "123",
            "input": {
                "test_input": None,
            }
        }

    async def test_simple_job(self):
        '''
        Tests the run_job function
        '''
        mock_handler = Mock()

        mock_handler.return_value = "test"
        job_result = await rp_job.run_job(mock_handler, self.sample_job)
        assert job_result == {"output": "test"}

        mock_handler.return_value = ['test1', 'test2']
        job_result_list = await rp_job.run_job(mock_handler, self.sample_job)
        assert job_result_list == {"output": ["test1", "test2"]}

        mock_handler.return_value = 123
        job_result_int = await rp_job.run_job(mock_handler, self.sample_job)
        assert job_result_int == {"output": 123}

    async def test_job_with_errors(self):
        '''
        Tests the run_job function with errors
        '''
        mock_handler = Mock()
        mock_handler.return_value = {"error": "test"}

        job_result = await rp_job.run_job(mock_handler, self.sample_job)

        assert job_result == {"error": "test"}

    async def test_job_with_raised_exception(self):
        '''
        Tests the run_job function with a raised exception
        '''
        mock_handler = Mock()
        mock_handler.side_effect = Exception

        job_result = await rp_job.run_job(mock_handler, self.sample_job)

        assert "error" in job_result

    async def test_job_with_refresh_worker(self):
        '''
        Tests the run_job function with refresh_worker
        '''
        mock_handler = Mock()
        mock_handler.return_value = {"refresh_worker": True}

        job_result = await rp_job.run_job(mock_handler, self.sample_job)

        assert job_result["stopPod"] is True

    async def test_job_bool_output(self):
        '''
        Tests the run_job function with a boolean output
        '''
        mock_handler = Mock()
        mock_handler.return_value = True

        job_result = await rp_job.run_job(mock_handler, self.sample_job)

        assert job_result == {"output": True}

    async def test_job_with_exception(self):
        '''
        Tests the run_job function with an exception
        '''
        mock_handler = Mock()
        mock_handler.side_effect = Exception

        job_result = await rp_job.run_job(mock_handler, self.sample_job)

        self.assertRaises(Exception, job_result)


class TestRunJobGenerator(IsolatedAsyncioTestCase):
    ''' Tests the run_job_generator function '''

    def handler_gen_success(self, job):  # pylint: disable=unused-argument
        '''
        Test handler that returns a generator.
        '''
        yield "partial_output_1"
        yield "partial_output_2"

    async def handler_async_gen_success(self, job):  # pylint: disable=unused-argument
        '''
        Test handler that returns an async generator.
        '''
        yield "partial_output_1"
        yield "partial_output_2"

    def handler_fail(self, job):
        '''
        Test handler that raises an exception.
        '''
        raise Exception("Test Exception")  # pylint: disable=broad-exception-raised

    async def test_run_job_generator_success(self):
        '''
        Tests the run_job_generator function with a successful generator
        '''
        handler = self.handler_gen_success
        job = {"id": "123"}

        with patch("runpod.serverless.modules.rp_job.log", new_callable=Mock) as mock_log:
            result = [i async for i in rp_job.run_job_generator(handler, job)]

        assert result == [{"output": "partial_output_1"}, {"output": "partial_output_2"}]
        assert mock_log.error.call_count == 0
        assert mock_log.info.call_count == 1
        mock_log.info.assert_called_with('Finished', '123')

    async def test_run_job_generator_success_async(self):
        '''
        Tests the run_job_generator function with a successful generator
        '''
        handler = self.handler_async_gen_success
        job = {"id": "123"}

        with patch("runpod.serverless.modules.rp_job.log", new_callable=Mock) as mock_log:
            result = [i async for i in rp_job.run_job_generator(handler, job)]

        assert result == [{"output": "partial_output_1"}, {"output": "partial_output_2"}]
        assert mock_log.error.call_count == 0
        assert mock_log.info.call_count == 1
        mock_log.info.assert_called_with('Finished', '123')

    async def test_run_job_generator_exception(self):
        '''
        Tests the run_job_generator function with an exception
        '''
        handler = self.handler_fail
        job = {"id": "123"}

        with patch("runpod.serverless.modules.rp_job.log", new_callable=Mock) as mock_log:
            result = [i async for i in rp_job.run_job_generator(handler, job)]

        assert len(result) == 1
        assert "error" in result[0]
        assert mock_log.error.call_count == 1
        assert mock_log.info.call_count == 1
        mock_log.info.assert_called_with('Finished', '123')
