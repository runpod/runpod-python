""" Unit testing for runpod.cli.utils.rp_userspace.py """

import os
import unittest
from unittest.mock import patch

from runpod.cli.utils.rp_userspace import find_ssh_key_file


class TestFindSSHKeyFile(unittest.TestCase):
    """Unit testing for find_ssh_key_file"""

    def setUp(self):
        self.pod_ip = "127.0.0.1"
        self.pod_port = 22

    @patch("os.listdir")
    def test_no_keys_in_directory(self, mock_listdir):
        """Test find_ssh_key_file when there are no keys in the directory"""
        mock_listdir.return_value = []
        result = find_ssh_key_file(self.pod_ip, self.pod_port)
        self.assertIsNone(result)

    @patch("os.path.isfile")
    @patch("os.listdir")
    def test_all_keys_end_with_pub(self, mock_listdir, mock_isfile):
        """Test find_ssh_key_file when all keys in the directory end with .pub"""
        mock_listdir.return_value = ["key1.pub", "key2.pub"]
        mock_isfile.return_value = True
        result = find_ssh_key_file(self.pod_ip, self.pod_port)
        self.assertIsNone(result)

    @patch("paramiko.SSHClient")
    @patch("os.path.isfile")
    @patch("os.listdir")
    def test_valid_key_found(self, mock_listdir, mock_isfile, mock_ssh_client):
        """Test find_ssh_key_file when a valid key is found"""
        mock_listdir.return_value = ["key1", "key2"]
        mock_isfile.return_value = True
        mock_ssh_instance = mock_ssh_client.return_value
        mock_ssh_instance.connect.side_effect = [None, Exception("Error with key2")]
        result = find_ssh_key_file(self.pod_ip, self.pod_port)
        self.assertEqual(result, os.path.expanduser("~/.runpod/ssh/key1"))

    @patch("paramiko.SSHClient")
    @patch("os.path.isfile")
    @patch("os.listdir")
    def test_no_valid_key_found(self, mock_listdir, mock_isfile, mock_ssh_client):
        """Test find_ssh_key_file when no valid key is found"""
        mock_listdir.return_value = ["key1", "key2"]
        mock_isfile.return_value = True
        mock_ssh_instance = mock_ssh_client.return_value
        mock_ssh_instance.connect.side_effect = [
            Exception("Error with key1"),
            Exception("Error with key2"),
        ]
        result = find_ssh_key_file(self.pod_ip, self.pod_port)
        self.assertIsNone(result)
