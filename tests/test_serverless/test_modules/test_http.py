
import asyncio
import unittest
from unittest.mock import patch, Mock, MagicMock
from aiohttp import ClientSession

from runpod.serverless.modules import rp_http

class TestHTTP(unittest.TestCase):
    ''' Test HTTP module. '''

    def setUp(self) -> None:
        self.job = {"id": "test_id"}
        self.job_data = {"output": "test_output"}


    def test_send_result(self):
        '''
        Test send_result function.
        '''
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with patch('runpod.serverless.modules.rp_http.log') as mock_log:
            send_return_local = asyncio.run(rp_http.send_result(Mock(), self.job_data, self.job))

            assert send_return_local is None
            assert mock_log.debug.call_count == 0
            assert mock_log.warn.call_count == 1

            rp_http.IS_LOCAL_TEST = False
            send_return = asyncio.run(rp_http.send_result(Mock(), self.job_data, self.job))

            assert send_return is None
            assert mock_log.debug.call_count == 3

        loop.close()

    def test_stream_result(self):
        '''
        Test stream_result function.
        '''
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with patch('runpod.serverless.modules.rp_http.log') as mock_log:
            rp_http.IS_LOCAL_TEST = True
            send_return_local = asyncio.run(rp_http.stream_result(Mock(), self.job_data, self.job))

            assert send_return_local is None
            assert mock_log.debug.call_count == 0
            assert mock_log.warn.call_count == 1

            rp_http.IS_LOCAL_TEST = False
            send_return = asyncio.run(rp_http.stream_result(Mock(), self.job_data, self.job))

            assert send_return is None
            assert mock_log.debug.call_count == 3

        loop.close()
