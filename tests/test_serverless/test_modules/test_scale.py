import asyncio
import sys
import traceback
from unittest import TestCase
from unittest.mock import patch

from runpod.serverless.modules.rp_scale import JobScaler, _handle_uncaught_exception


class TestHandleUncaughtException(TestCase):
    def setUp(self):
        sys.excepthook = sys.__excepthook__

    @patch("runpod.serverless.modules.rp_scale.log")
    def test_handle_uncaught_exception(self, mock_logger):
        exc_type = ValueError
        exc_value = ValueError("This is a test error")
        exc_traceback = None  # No traceback for simplicity

        _handle_uncaught_exception(exc_type, exc_value, exc_traceback)

        formatted_exception = traceback.format_exception(exc_type, exc_value, exc_traceback)

        mock_logger.error.assert_called_once()
        log_message = mock_logger.error.call_args[0][0]
        assert "Uncaught exception | " in log_message
        assert str(formatted_exception) in log_message

    @patch("runpod.serverless.modules.rp_scale.log")
    def test_handle_uncaught_exception_with_traceback(self, mock_logger):
        try:
            raise RuntimeError("This is a runtime error")
        except RuntimeError:
            exc_type, exc_value, exc_traceback = sys.exc_info()

            _handle_uncaught_exception(exc_type, exc_value, exc_traceback)

            formatted_exception = traceback.format_exception(exc_type, exc_value, exc_traceback)

            mock_logger.error.assert_called_once()
            log_message = mock_logger.error.call_args[0][0]
            assert "Uncaught exception | " in log_message
            assert str(formatted_exception) in log_message

    @patch("runpod.serverless.modules.rp_scale.log")
    def test_handle_uncaught_exception_with_no_exception(self, mock_logger):
        _handle_uncaught_exception(None, None, None)

        mock_logger.error.assert_called_once()
        log_message = mock_logger.error.call_args[0][0]
        assert "Uncaught exception | " in log_message

    def test_excepthook_not_set_when_start_not_invoked(self):
        assert sys.excepthook == sys.__excepthook__
        assert sys.excepthook != _handle_uncaught_exception


class TestSetScaleConcurrencyValidation(TestCase):
    """Tests that set_scale guards against invalid concurrency_modifier returns. (Fixes #458)"""

    def _make_scaler(self, modifier):
        config = {"handler": lambda job: job, "concurrency_modifier": modifier}
        return JobScaler(config)

    @patch("runpod.serverless.modules.rp_scale.log")
    def test_none_return_defaults_to_1(self, _mock_log):
        scaler = self._make_scaler(lambda _: None)
        asyncio.run(scaler.set_scale())
        assert scaler.current_concurrency == 1

    @patch("runpod.serverless.modules.rp_scale.log")
    def test_negative_return_defaults_to_1(self, _mock_log):
        scaler = self._make_scaler(lambda _: -5)
        asyncio.run(scaler.set_scale())
        assert scaler.current_concurrency == 1

    @patch("runpod.serverless.modules.rp_scale.log")
    def test_zero_return_defaults_to_1(self, _mock_log):
        scaler = self._make_scaler(lambda _: 0)
        asyncio.run(scaler.set_scale())
        assert scaler.current_concurrency == 1

    @patch("runpod.serverless.modules.rp_scale.log")
    def test_exception_keeps_current(self, _mock_log):
        scaler = self._make_scaler(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
        scaler.current_concurrency = 4
        asyncio.run(scaler.set_scale())
        assert scaler.current_concurrency == 4

    @patch("runpod.serverless.modules.rp_scale.log")
    def test_valid_int_applied(self, _mock_log):
        scaler = self._make_scaler(lambda _: 8)
        asyncio.run(scaler.set_scale())
        assert scaler.current_concurrency == 8
