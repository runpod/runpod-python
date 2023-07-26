'''
Test rp_http.py module.
'''

import asyncio
import unittest
from unittest.mock import patch, Mock, AsyncMock
import aiohttp
from aiohttp import ClientResponse

import pytest

from runpod.serverless.modules import rp_http

class TestHTTP(unittest.IsolatedAsyncioTestCase):
    ''' Test HTTP module. '''

    def setUp(self) -> None:
        self.job = {"id": "test_id"}
        self.job_data = {"output": "test_output"}

    def test_send_result_exception(self):
        '''
        Test send_result function.
        '''
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with patch('runpod.serverless.modules.rp_http.log') as mock_log:
            with patch('runpod.serverless.modules.rp_http.job_list.jobs') as mock_jobs:
                mock_jobs.return_value = set(['test_id'])
                send_return_local = asyncio.run(
                    rp_http.send_result(Mock(), self.job_data, self.job))

                assert send_return_local is None
                assert mock_log.debug.call_count == 3
                assert mock_log.error.call_count == 1

        loop.close()

    def test_send_result(self):
        '''
        Test send_result function.
        '''
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with patch('runpod.serverless.modules.rp_http.log') as mock_log,\
             patch('runpod.serverless.modules.rp_http.transmit', new=AsyncMock()) as mock_transmit:
            with patch('runpod.serverless.modules.rp_http.job_list.jobs') as mock_jobs:
                mock_jobs.return_value = set(['test_id'])

                send_return_local = asyncio.run(
                    rp_http.send_result(Mock(), self.job_data, self.job))

                assert send_return_local is None
                assert mock_log.debug.call_count == 1
                assert mock_log.error.call_count == 0
                assert mock_log.info.call_count == 1
                mock_transmit.assert_called_once()

        loop.close()

    def test_stream_result(self):
        '''
        Test stream_result function.
        '''
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with patch('runpod.serverless.modules.rp_http.log') as mock_log:
            with patch('runpod.serverless.modules.rp_http.job_list.jobs') as mock_jobs:
                mock_jobs.return_value = set(['test_id'])
                rp_http.IS_LOCAL_TEST = True
                send_return_local = asyncio.run(
                    rp_http.stream_result(Mock(), self.job_data, self.job))

                assert send_return_local is None
                assert mock_log.debug.call_count == 3
                #assert mock_log.error.call_count == 0
                #assert mock_log.info.call_count == 1

        loop.close()

@pytest.mark.asyncio
@patch('aiohttp.ClientSession.post')
async def test_transmit(mock_post):
    '''
    Tests the transmit function
    '''
    # Mock the session and job data
    session = Mock()
    job_data = {"output": "test_output"}
    url = "http://example.com"

    # Mock the response from the post request
    mock_response = AsyncMock(spec=ClientResponse)
    mock_response.text.return_value = "response text"

    # Mock context manager returned by post
    async_context_manager = AsyncMock()
    async_context_manager.__aenter__.return_value = mock_response

    # Mock post method on session
    mock_post.return_value = async_context_manager

    # Mock session
    session = aiohttp.ClientSession()

    # Call the function
    await rp_http.transmit(session, job_data, url)

    # Check that post was called with the correct arguments
    mock_post.assert_called_once_with(url, data=job_data, headers={
        "charset": "utf-8",
        "Content-Type": "application/x-www-form-urlencoded"
    }, raise_for_status=True)

    # Check that text() method was called on the response
    mock_response.text.assert_called_once()
