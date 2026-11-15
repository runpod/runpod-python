"""Tests for rp_local.py"""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, mock_open, patch

from runpod.serverless.modules import rp_local
from runpod.serverless.modules.rp_prestart import (
    clear_prestart_hooks,
    register_prestart_hook,
)


class TestRunLocal(IsolatedAsyncioTestCase):
    """Tests for run_local function"""

    def setUp(self):
        clear_prestart_hooks()

    def tearDown(self):
        clear_prestart_hooks()

    async def test_prestart_runs_before_local_handler(self):
        calls = []

        @register_prestart_hook
        async def load_model():
            calls.append("prestart")

        async def run_job(*_args):
            calls.append("handler")
            return {"result": "success"}

        config = {
            "handler": "handler",
            "rp_args": {"test_input": {"input": "test"}},
        }
        with (
            patch(
                "runpod.serverless.modules.rp_local.run_job",
                new=AsyncMock(side_effect=run_job),
            ),
            self.assertRaises(SystemExit) as sys_exit,
        ):
            await rp_local.run_local(config)

        self.assertEqual(sys_exit.exception.code, 0)
        self.assertEqual(calls, ["prestart", "handler"])

    async def test_prestart_failure_skips_local_handler(self):
        @register_prestart_hook
        def load_model():
            raise RuntimeError("model unavailable")

        config = {
            "handler": "handler",
            "rp_args": {"test_input": {"input": "test"}},
        }
        with (
            patch(
                "runpod.serverless.modules.rp_local.run_job", new=AsyncMock()
            ) as run_job,
            patch("runpod.serverless.modules.rp_prestart.log") as logger,
            # The real helper calls os._exit; SystemExit stands in for that so the
            # test proves the handler is unreachable rather than merely unreached.
            patch(
                "runpod.serverless.modules.rp_local._terminate_unhealthy",
                side_effect=SystemExit(1),
            ) as terminate,
            self.assertRaises(SystemExit) as sys_exit,
        ):
            await rp_local.run_local(config)

        self.assertEqual(sys_exit.exception.code, 1)
        terminate.assert_called_once_with(1)
        run_job.assert_not_awaited()
        failure_log = logger.error.call_args.args[0]
        self.assertIn("prestart_failed", failure_log)
        self.assertIn("load_model", failure_log)
        self.assertIn("model unavailable", failure_log)

    @patch(
        "runpod.serverless.modules.rp_local.run_job", return_value={"result": "success"}
    )
    @patch("builtins.open", new_callable=mock_open, read_data='{"input": "test"}')
    async def test_run_local_with_test_input(self, mock_file, mock_run):
        """
        Test run_local function with test_input in rp_args
        """
        config = {
            "handler": "handler",
            "rp_args": {
                "test_input": {"input": "test", "id": "test_id"},
                "test_output": {"result": "success"},
            },
        }
        with self.assertRaises(SystemExit) as sys_exit:
            await rp_local.run_local(config)
            self.assertEqual(sys_exit.exception.code, 0)

        config["rp_args"]["test_output"] = {"result": "fail"}
        with self.assertRaises(SystemExit) as sys_exit:
            await rp_local.run_local(config)
            self.assertEqual(sys_exit.exception.code, 1)

        assert mock_file.called is False
        assert mock_run.called

    @patch("runpod.serverless.modules.rp_local.run_job", return_value={})
    @patch("builtins.open", new_callable=mock_open, read_data='{"input": "test"}')
    async def test_run_local_with_test_input_json(self, mock_file, mock_run):
        """
        Test run_local function with test_input.json
        """
        config = {"handler": "handler", "rp_args": {}}
        with patch("os.path.exists", return_value=True):
            with self.assertRaises(SystemExit) as sys_exit:
                await rp_local.run_local(config)
            self.assertEqual(sys_exit.exception.code, 0)

        assert mock_file.called
        assert mock_run.called

    @patch(
        "runpod.serverless.modules.rp_local.run_job",
        return_value={"error": "test_error"},
    )
    @patch("builtins.open", new_callable=mock_open, read_data='{"input": "test"}')
    async def test_run_local_with_error(self, mock_file, mock_run):
        """
        Test run_local function when run_job returns an error
        """
        config = {
            "handler": "handler",
            "rp_args": {"test_input": {"input": "test", "id": "test_id"}},
        }
        with self.assertRaises(SystemExit) as sys_exit:
            await rp_local.run_local(config)
        self.assertEqual(sys_exit.exception.code, 1)

        assert mock_file.called is False
        assert mock_run.called

    async def test_run_local_without_test_input_json(self):
        """
        Test run_local function without test_input.json
        """
        config = {"handler": "handler", "rp_args": {}}
        with patch("os.path.exists", return_value=False):
            with self.assertRaises(SystemExit) as sys_exit:
                await rp_local.run_local(config)
            self.assertEqual(sys_exit.exception.code, 1)

    @patch("runpod.serverless.modules.rp_local.run_job", return_value={})
    @patch("builtins.open", new_callable=mock_open, read_data='{"not_input": "test"}')
    async def test_run_local_without_input(self, mock_file, mock_run):
        """
        Test run_local function without input in test_input.json
        """
        config = {"handler": "handler", "rp_args": {}}
        with patch("os.path.exists", return_value=True):
            with self.assertRaises(SystemExit) as sys_exit:
                await rp_local.run_local(config)
            self.assertEqual(sys_exit.exception.code, 1)

        assert mock_file.called
        assert mock_run.called is False
