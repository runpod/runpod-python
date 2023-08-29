'''
Test rp_http.py module.
'''

import unittest
from unittest.mock import patch, Mock, AsyncMock
from aiohttp import ClientResponse
from aiohttp import ClientResponseError, ClientConnectionError, ClientError

from runpod.serverless.modules import rp_http

def mocked_transmit(*args, **kwargs):
    ''' Mock transmit function. '''
    del args, kwargs
    raise Exception("Forced exception") # pylint: disable=broad-exception-raised

class TestHTTP(unittest.IsolatedAsyncioTestCase):
    ''' Test HTTP module. '''

    def setUp(self) -> None:
        self.job = {"id": "test_id"}
        self.job_data = {"output": "test_output"}


    async def test_send_result_exception(self):
        '''
        Test send_result function.
        '''
        mock_session = AsyncMock()
        mock_session.post.return_value = AsyncMock()

        with patch('runpod.serverless.modules.rp_http.log') as mock_log, \
             patch('runpod.serverless.modules.rp_http._transmit') as mock_trans, \
             patch('runpod.serverless.modules.rp_http.job_list.jobs') as mock_jobs:

            mock_jobs.return_value = set(['test_id'])
            send_return_local = await rp_http.send_result(mock_session, self.job_data, self.job)

            assert send_return_local is None
            assert mock_log.debug.call_count == 0
            assert mock_log.error.call_count == 1
            assert mock_log.info.call_count == 0
            mock_trans.assert_called_once()


    async def test_send_result(self):
        '''
        Test send_result function.
        '''
        mock_session = AsyncMock()
        mock_session.post.return_value = AsyncMock()

        with patch('runpod.serverless.modules.rp_http.log') as mock_log, \
             patch('runpod.serverless.modules.rp_http._transmit') as mock_trans, \
             patch('runpod.serverless.modules.rp_http.job_list.jobs') as mock_jobs:

            mock_jobs.return_value = set(['test_id'])

            send_return_local = await rp_http.send_result(mock_session, self.job_data, self.job)

            assert send_return_local is None
            assert mock_log.debug.call_count == 1
            assert mock_log.error.call_count == 0
            assert mock_log.info.call_count == 1
            mock_trans.assert_called_once()


    async def test_stream_result(self):
        '''
        Test stream_result function.
        '''
        mock_session = AsyncMock()
        mock_session.post.return_value = AsyncMock()

        with patch('runpod.serverless.modules.rp_http.log') as mock_log,\
             patch('runpod.serverless.modules.rp_http._transmit') as mock_trans, \
             patch('runpod.serverless.modules.rp_http.job_list.jobs') as mock_jobs:

            mock_jobs.return_value = set(['test_id'])
            send_return_local = await rp_http.stream_result(mock_session, self.job_data, self.job)

            assert send_return_local is None
            assert mock_log.debug.call_count == 1
            assert mock_log.error.call_count == 0
            assert mock_log.info.call_count == 0
            mock_trans.assert_called_once()


    async def test_stream_result_exception(self):
        '''
        Test stream_result function exception.
        '''
        mock_session = AsyncMock()
        mock_session.post.return_value = AsyncMock()

        with patch('runpod.serverless.modules.rp_http.log') as mock_log, \
             patch('runpod.serverless.modules.rp_http.job_list.jobs') as mock_jobs, \
             patch('runpod.serverless.modules.rp_http._transmit', side_effect=mocked_transmit):

            mock_jobs.return_value = set(['test_id'])
            await rp_http.stream_result(mock_session, self.job_data, self.job)

            assert mock_log.debug.call_count == 0
            assert mock_log.error.call_count == 1


    async def test_transmit(self):
        '''
        Tests the transmit function
        '''
        # Mock the session and job data
        session = Mock()
        job_id = "test_id"
        job_data = {"output": "test_output"}
        url = "http://example.com"

        # Mock the response from the post request
        mock_response = AsyncMock(spec=ClientResponse)
        mock_response.text.return_value = "response text"

        # Mock context manager returned by post
        async_context_manager = AsyncMock()
        async_context_manager.__aenter__.return_value = mock_response
        session.post.return_value = async_context_manager

        # Call the function
        await rp_http._transmit(session, url, job_id, job_data) # pylint: disable=protected-access

        # Check that post was called with the correct arguments
        session.post.assert_called_once_with(url, data=job_data, headers={
            "charset": "utf-8",
            "Content-Type": "application/x-www-form-urlencoded"
        }, raise_for_status=True)

        # Check that text() method was called on the response
        mock_response.text.assert_called_once()


    async def test_transmit_errors(self):
        ''' Tests the transmit function with different client errors. '''

        session = Mock()
        job_id = "test_id"
        job_data = {"output": "test_output"}
        url = "http://example.com"

        errors = [
            (
                ClientResponseError(Mock(), Mock(), status=500, message="Response error"),
                ClientResponseError
            ),
            (ClientConnectionError("Connection error"), ClientConnectionError),
            (ClientError("Generic client error"), ClientError)
        ]

        for error_instance, error_type in errors:
            with self.subTest(error_type=error_type):

                # Mock context manager to raise the respective error
                async_context_manager = AsyncMock()
                async_context_manager.__aenter__.side_effect = error_instance
                session.post.return_value = async_context_manager

                with self.assertRaises(error_type):
                    await rp_http._transmit(session, url, job_id, job_data) # pylint: disable=protected-access

        with self.assertRaises(ClientError):
            await rp_http._transmit(session, url, job_id, job_data)  # pylint: disable=protected-access


if __name__ == '__main__':
    unittest.main()
