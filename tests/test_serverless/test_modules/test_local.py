""" Tests for rp_local.py """

from unittest import IsolatedAsyncioTestCase
from unittest.mock import mock_open, patch

from runpod.serverless.modules import rp_local


class TestRunLocal(IsolatedAsyncioTestCase):
    """Tests for run_local function"""

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
