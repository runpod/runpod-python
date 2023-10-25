""" Unit tests for the runpod.cli.groups.exec.helpers module. """

import unittest
from unittest.mock import patch, mock_open
from runpod.cli.groups.exec.helpers import get_session_pod, POD_ID_FILE

class TestGetSessionPod(unittest.TestCase):
    """ Unit tests for get_session_pod """

    def setUp(self):
        self.mocked_pod_id = "sample_pod_id"

    @patch('os.path.exists')
    @patch('runpod.cli.groups.exec.helpers.get_pod')
    def test_existing_pod_id_file_and_valid_pod_id(self, mock_get_pod, mock_exists):
        """ Test get_session_pod when the pod_id file exists and the pod_id is valid """
        mock_exists.return_value = True
        mock_get_pod.return_value = True
        with patch('builtins.open', mock_open(read_data=self.mocked_pod_id)):
            result = get_session_pod()
        self.assertEqual(result, self.mocked_pod_id)

    @patch('os.path.exists')
    @patch('runpod.cli.groups.exec.helpers.get_pod')
    def test_existing_pod_id_file_and_invalid_pod_id(self, mock_get_pod, mock_exists):
        """ Test get_session_pod when the pod_id file exists and the pod_id is invalid """
        mock_exists.return_value = True
        mock_get_pod.return_value = None
        with patch('builtins.open', mock_open(read_data="invalid_pod_id")):
            with patch('click.prompt', return_value=self.mocked_pod_id):
                result = get_session_pod()
        self.assertEqual(result, self.mocked_pod_id)

    @patch('os.path.exists')
    @patch('runpod.cli.groups.exec.helpers.get_pod')
    def test_no_pod_id_file(self, mock_get_pod, mock_exists):
        """ Test get_session_pod when the pod_id file doesn't exist """
        mock_exists.return_value = False
        mock_get_pod.return_value = None
        with patch('click.prompt', return_value=self.mocked_pod_id):
            result = get_session_pod()
        self.assertEqual(result, self.mocked_pod_id)

    @patch('os.path.exists')
    @patch('runpod.cli.groups.exec.helpers.get_pod')
    def test_pod_id_file_written_to_when_not_existing(self, mock_get_pod, mock_exists):
        """ Test get_session_pod when the pod_id file doesn't exist """
        mock_exists.return_value = False
        mock_get_pod.return_value = None
        mocked = mock_open()
        with patch('builtins.open', mocked):
            with patch('click.prompt', return_value=self.mocked_pod_id):
                get_session_pod()
        mocked.assert_called_once_with(POD_ID_FILE, 'w', encoding="UTF-8")
        handle = mocked()
        handle.write.assert_called_once_with(self.mocked_pod_id)

    @patch('os.path.exists')
    @patch('runpod.cli.groups.exec.helpers.get_pod')
    def test_pod_id_file_written_to_when_invalid_pod_id_in_file(self, mock_get_pod, mock_exists):
        """ Test get_session_pod when the pod_id file exists and the pod_id is invalid """
        mock_exists.return_value = True
        mock_get_pod.return_value = None
        mocked = mock_open(read_data="invalid_pod_id")
        with patch('builtins.open', mocked):
            with patch('click.prompt', return_value=self.mocked_pod_id):
                get_session_pod()
        mocked.assert_called_with(POD_ID_FILE, 'w', encoding="UTF-8")
        handle = mocked()
        handle.write.assert_called_with(self.mocked_pod_id)
