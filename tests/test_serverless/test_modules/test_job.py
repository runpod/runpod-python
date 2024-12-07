"""
Test Serverless Job Module
"""

from unittest.mock import Mock, patch

from unittest import IsolatedAsyncioTestCase
from aiohttp import ClientResponse, ClientResponseError
from aiohttp.test_utils import make_mocked_coro

from runpod.http_client import TooManyRequests
from runpod.serverless.modules import rp_job


class TestJob(IsolatedAsyncioTestCase):
    """Tests for the get_job function."""

    async def test_get_job_200(self):
        """Tests the get_job function with a valid 200 response."""
        # Mock the 200 response
        response = Mock(ClientResponse)
        response.status = 200
        response.content_type = "application/json"
        response.content_length = 50
        response.json = make_mocked_coro(
            return_value={"id": "123", "input": {"number": 1}}
        )

        with patch("aiohttp.ClientSession") as mock_session, patch(
            "runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"
        ):
            mock_session.get.return_value.__aenter__.return_value = response
            job = await rp_job.get_job(mock_session)
            # Assertions for the success case
            self.assertEqual(job, [{"id": "123", "input": {"number": 1}}])

    async def test_get_job_204(self):
        """Tests the get_job function with a 204 response."""
        # Mock 204 No Content response
        response = Mock(ClientResponse)
        response.status = 204
        response.content_type = "application/json"
        response.content_length = 0

        with patch("aiohttp.ClientSession") as mock_session, patch(
            "runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"
        ):
            mock_session.get.return_value.__aenter__.return_value = response
            job = await rp_job.get_job(mock_session)
            self.assertIsNone(job)
            self.assertEqual(mock_session.get.call_count, 1)

    async def test_get_job_400(self):
        """Tests the get_job function with a 400 response."""
        # Mock 400 response
        response = Mock(ClientResponse)
        response.status = 400

        with patch("aiohttp.ClientSession") as mock_session, patch(
            "runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"
        ):
            mock_session.get.return_value.__aenter__.return_value = response
            job = await rp_job.get_job(mock_session)
            self.assertIsNone(job)

    async def test_get_job_429(self):
        """Tests the get_job function with a 429 response."""
        response = Mock(ClientResponse)
        response.raise_for_status.side_effect = TooManyRequests(
            request_info=None,
            history=(),
            status=429,
        )

        with patch("aiohttp.ClientSession") as mock_session, patch(
            "runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"
        ):
            mock_session.get.return_value.__aenter__.return_value = response
            with self.assertRaises(ClientResponseError):
                await rp_job.get_job(mock_session)

    async def test_get_job_500(self):
        """Tests the get_job function with a 500 response."""
        # Mock 500 response
        response = Mock(ClientResponse)
        response.raise_for_status.side_effect = TooManyRequests(
            request_info=None,  # Not needed for the test
            history=(),  # Not needed for the test
            status=500, 
        )
        with patch("aiohttp.ClientSession") as mock_session, patch(
            "runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"
        ):
            mock_session.get.return_value.__aenter__.return_value = response
            with self.assertRaises(Exception):
                await rp_job.get_job(mock_session)

    async def test_get_job_no_id(self):
        """Tests the get_job function with a 200 response but no 'id' field."""
        response = Mock(ClientResponse)
        response.status = 200
        response.content_type = "application/json"
        response.content_length = 50
        response.json = make_mocked_coro(return_value={"input": "foobar"})

        with patch("aiohttp.ClientSession") as mock_session, patch(
            "runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"
        ):
            mock_session.get.return_value.__aenter__.return_value = response
            with self.assertRaises(Exception) as context:
                await rp_job.get_job(mock_session)
            self.assertEqual(str(context.exception), "Job has missing field(s): id or input.")

    async def test_get_job_invalid_content_type(self):
        """Tests the get_job function with an invalid content type."""
        response = Mock(ClientResponse)
        response.status = 200
        response.content_type = "text/html"  # Invalid content type
        response.content_length = 50

        with patch("aiohttp.ClientSession") as mock_session, patch(
            "runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"
        ):
            mock_session.get.return_value.__aenter__.return_value = response
            job = await rp_job.get_job(mock_session)
            self.assertIsNone(job)

    async def test_get_job_empty_content(self):
        """Tests the get_job function with an empty content response."""
        response = Mock(ClientResponse)
        response.status = 200
        response.content_type = "application/json"
        response.content_length = 0  # No content to parse

        with patch("aiohttp.ClientSession") as mock_session, patch(
            "runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"
        ):
            mock_session.get.return_value.__aenter__.return_value = response
            job = await rp_job.get_job(mock_session)
            self.assertIsNone(job)

    async def test_get_job_exception(self):
        """Tests the get_job function with a raised exception."""
        with patch("aiohttp.ClientSession") as mock_session, patch(
            "runpod.serverless.modules.rp_job.JOB_GET_URL", "http://mock.url"
        ):
            mock_session.get.return_value.__aenter__.side_effect = Exception("Unexpected error")
            with self.assertRaises(Exception) as context:
                await rp_job.get_job(mock_session)
            self.assertEqual(str(context.exception), "Unexpected error")


class TestRunJob(IsolatedAsyncioTestCase):
    """Tests the run_job function"""

    async def asyncSetUp(self) -> None:
        self.sample_job = {
            "id": "123",
            "input": {
                "test_input": None,
            },
        }

    async def test_simple_job(self):
        """
        Tests the run_job function
        """
        mock_handler = Mock()

        mock_handler.return_value = "test"
        job_result = await rp_job.run_job(mock_handler, self.sample_job)
        assert job_result == {"output": "test"}

        mock_handler.return_value = ["test1", "test2"]
        job_result_list = await rp_job.run_job(mock_handler, self.sample_job)
        assert job_result_list == {"output": ["test1", "test2"]}

        mock_handler.return_value = 123
        job_result_int = await rp_job.run_job(mock_handler, self.sample_job)
        assert job_result_int == {"output": 123}

    async def test_job_with_errors(self):
        """
        Tests the run_job function with errors
        """
        mock_handler = Mock()
        mock_handler.return_value = {"error": "test"}

        job_result = await rp_job.run_job(mock_handler, self.sample_job)

        assert job_result == {"error": "test"}

    async def test_job_with_raised_exception(self):
        """
        Tests the run_job function with a raised exception
        """
        mock_handler = Mock()
        mock_handler.side_effect = Exception

        job_result = await rp_job.run_job(mock_handler, self.sample_job)

        assert "error" in job_result

    async def test_job_with_refresh_worker(self):
        """
        Tests the run_job function with refresh_worker
        """
        mock_handler = Mock()
        mock_handler.return_value = {"refresh_worker": True}

        job_result = await rp_job.run_job(mock_handler, self.sample_job)

        assert job_result["stopPod"] is True

    async def test_job_bool_output(self):
        """
        Tests the run_job function with a boolean output
        """
        mock_handler = Mock()
        mock_handler.return_value = True

        job_result = await rp_job.run_job(mock_handler, self.sample_job)

        assert job_result == {"output": True}

    async def test_job_with_exception(self):
        """
        Tests the run_job function with an exception
        """
        mock_handler = Mock()
        mock_handler.side_effect = Exception

        job_result = await rp_job.run_job(mock_handler, self.sample_job)

        self.assertRaises(Exception, job_result)


class TestRunJobGenerator(IsolatedAsyncioTestCase):
    """Tests the run_job_generator function"""

    def handler_gen_success(self, job):  # pylint: disable=unused-argument
        """
        Test handler that returns a generator.
        """
        yield "partial_output_1"
        yield "partial_output_2"

    async def handler_async_gen_success(self, job):  # pylint: disable=unused-argument
        """
        Test handler that returns an async generator.
        """
        yield "partial_output_1"
        yield "partial_output_2"

    def handler_fail(self, job):
        """
        Test handler that raises an exception.
        """
        raise Exception("Test Exception")  # pylint: disable=broad-exception-raised

    async def test_run_job_generator_success(self):
        """
        Tests the run_job_generator function with a successful generator
        """
        handler = self.handler_gen_success
        job = {"id": "123"}

        with patch(
            "runpod.serverless.modules.rp_job.log", new_callable=Mock
        ) as mock_log:
            result = [i async for i in rp_job.run_job_generator(handler, job)]

        assert result == [
            {"output": "partial_output_1"},
            {"output": "partial_output_2"},
        ]
        assert mock_log.error.call_count == 0
        assert mock_log.info.call_count == 1
        mock_log.info.assert_called_with("Finished running generator.", "123")

    async def test_run_job_generator_success_async(self):
        """
        Tests the run_job_generator function with a successful generator
        """
        handler = self.handler_async_gen_success
        job = {"id": "123"}

        with patch(
            "runpod.serverless.modules.rp_job.log", new_callable=Mock
        ) as mock_log:
            result = [i async for i in rp_job.run_job_generator(handler, job)]

        assert result == [
            {"output": "partial_output_1"},
            {"output": "partial_output_2"},
        ]
        assert mock_log.error.call_count == 0
        assert mock_log.info.call_count == 1
        mock_log.info.assert_called_with("Finished running generator.", "123")

    async def test_run_job_generator_exception(self):
        """
        Tests the run_job_generator function with an exception
        """
        handler = self.handler_fail
        job = {"id": "123"}

        with patch(
            "runpod.serverless.modules.rp_job.log", new_callable=Mock
        ) as mock_log:
            result = [i async for i in rp_job.run_job_generator(handler, job)]

        assert len(result) == 1
        assert "error" in result[0]
        assert mock_log.error.call_count == 1
        assert mock_log.info.call_count == 1
        mock_log.info.assert_called_with("Finished running generator.", "123")
