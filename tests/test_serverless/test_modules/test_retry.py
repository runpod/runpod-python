''' Unit tests for the retry module. '''

from unittest.mock import patch
import pytest
from runpod.serverless.modules.retry import retry

@retry(max_attempts=3, base_delay=1, max_delay=10)
async def func_raises_exception():
    '''
    A test function to be decorated with the retry decorator.
    '''
    raise Exception("Test Exception") # pylint: disable=broad-exception-raised

@retry(max_attempts=3, base_delay=1, max_delay=10)
async def func_returns_success():
    '''
    A test function to be decorated with the retry decorator.
    '''
    return "Success"

@pytest.mark.asyncio
async def test_retry():
    '''
    Test the retry decorator.
    '''
    with patch("asyncio.sleep", return_value=None) as mock_sleep:
        with pytest.raises(Exception) as excinfo:
            await func_raises_exception()
        assert str(excinfo.value) == "Test Exception"
        assert mock_sleep.call_count == 2
        assert 0.5 <= mock_sleep.call_args_list[0][0][0] <= 1.5  # First call
        assert 1.0 <= mock_sleep.call_args_list[1][0][0]

@pytest.mark.asyncio
async def test_retry_success():
    '''
    Test the retry decorator.
    '''
    with patch("asyncio.sleep", return_value=None) as mock_sleep:
        result = await func_returns_success()
        assert result == "Success"
        assert mock_sleep.call_count == 0
