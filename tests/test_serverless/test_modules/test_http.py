'''
Test rp_http.py module.
'''
# pylint: disable=too-few-public-methods

import gc
import json
import unittest
from unittest.mock import patch, AsyncMock
import aiohttp

from runpod.serverless.modules import rp_http

class MockRequestInfo:
    ''' Mock aiohttp.RequestInfo class. '''

    def __init__(self, *args, **kwargs):
        del args, kwargs
        self.url = "http://test_url"
        self.method = "POST"
        self.headers = {"Content-Type": "application/json"}
        self.real_url = "http://test_url"

    real_url = "http://test_url"


class TestHTTP(unittest.IsolatedAsyncioTestCase):
    ''' Test HTTP module. '''

    def setUp(self) -> None:
        self.job = {"id": "test_id"}
        self.job_data = {"output": "test_output"}

    def tearDown(self) -> None:
        gc.collect()


    async def test_send_result(self):
        '''
        Test send_result function.
        '''
        with patch('runpod.serverless.modules.rp_http.log') as mock_log, \
             patch('runpod.serverless.modules.rp_http.job_list.jobs') as mock_jobs, \
             patch('runpod.serverless.modules.rp_http.RetryClient') as mock_retry:

            mock_retry.return_value.post.return_value = AsyncMock()
            mock_retry.return_value.post.return_value.__aenter__.return_value.text.return_value = "response text" # pylint: disable=line-too-long

            mock_jobs.return_value = set(['test_id'])
            send_return_local = await rp_http.send_result(AsyncMock(), self.job_data, self.job)

            assert send_return_local is None
            assert mock_log.debug.call_count == 1
            assert mock_log.error.call_count == 0
            assert mock_log.info.call_count == 1

            mock_retry.return_value.post.assert_called_with(
                'JOB_DONE_URL',
                data=str(json.dumps(self.job_data, ensure_ascii=False)),
                headers={
                    "charset": "utf-8",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                raise_for_status=True
            )


    async def test_send_result_client_response_error(self):
        '''
        Test send_result function with ClientResponseError.
        '''
        def mock_request_info_init(self, *args, **kwargs):
            '''
            Mock aiohttp.RequestInfo.__init__ method.
            '''
            del args, kwargs
            self.url = "http://test_url"
            self.method = "POST"
            self.headers = {"Content-Type": "application/json"}
            self.real_url = "http://test_url"

        with patch('runpod.serverless.modules.rp_http.log') as mock_log, \
             patch('runpod.serverless.modules.rp_http.job_list.jobs') as mock_jobs, \
             patch('runpod.serverless.modules.rp_http.RetryClient') as mock_retry, \
             patch.object(aiohttp.RequestInfo, "__init__", mock_request_info_init):

            mock_retry.side_effect = aiohttp.ClientResponseError(
                request_info=MockRequestInfo,
                history=None,
                status=500,
                message="Error message"
            )

            mock_jobs.return_value = set(['test_id'])
            send_return_local = await rp_http.send_result(AsyncMock(), self.job_data, self.job)

            assert send_return_local is None
            assert mock_log.debug.call_count == 0
            assert mock_log.error.call_count == 1
            assert mock_log.info.call_count == 1


    async def test_send_result_type_error(self):
        '''
        Test send_result function with TypeError.
        '''
        with patch('runpod.serverless.modules.rp_http.log') as mock_log, \
             patch('runpod.serverless.modules.rp_http.job_list.jobs') as mock_jobs, \
             patch('runpod.serverless.modules.rp_http.json.dumps') as mock_dumps, \
             patch('runpod.serverless.modules.rp_http.RetryClient') as mock_retry:

            mock_dumps.side_effect = TypeError("Forced exception")

            mock_jobs.return_value = set(['test_id'])
            send_return_local = await rp_http.send_result("No Session", self.job_data, self.job)

            assert send_return_local is None
            assert mock_log.debug.call_count == 0
            assert mock_log.error.call_count == 1
            assert mock_log.info.call_count == 1
            assert mock_retry.return_value.post.call_count == 0
            mock_log.error.assert_called_with("Error while returning job result test_id: Forced exception") # pylint: disable=line-too-long


    async def test_stream_result(self):
        '''
        Test stream_result function.
        '''
        with patch('runpod.serverless.modules.rp_http.log') as mock_log, \
             patch('runpod.serverless.modules.rp_http.job_list.jobs') as mock_jobs, \
             patch('runpod.serverless.modules.rp_http.RetryClient') as mock_retry:

            mock_retry.return_value.post.return_value = AsyncMock()
            mock_retry.return_value.post.return_value.__aenter__.return_value.text.return_value = "response text" # pylint: disable=line-too-long

            mock_jobs.return_value = set(['test_id'])
            send_return_local = await rp_http.stream_result(AsyncMock(), self.job_data, self.job)

            assert send_return_local is None
            assert mock_log.debug.call_count == 1
            assert mock_log.error.call_count == 0
            assert mock_log.info.call_count == 0

            mock_retry.return_value.post.assert_called_with(
                'JOB_STREAM_URL',
                data=str(json.dumps(self.job_data, ensure_ascii=False)),
                headers={
                    "charset": "utf-8",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                raise_for_status=True
            )


if __name__ == '__main__':
    unittest.main()
