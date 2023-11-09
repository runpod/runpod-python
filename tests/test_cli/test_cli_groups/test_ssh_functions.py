""" Tests for the SSH functions """

import base64
import unittest
from unittest.mock import patch, mock_open
from runpod.cli.groups.ssh.functions import (
    get_ssh_key_fingerprint, get_user_pub_keys,
    generate_ssh_key_pair, add_ssh_key
)


class TestSSHFunctions(unittest.TestCase):
    """ Tests for the SSH functions """

    def test_get_ssh_key_fingerprint(self):
        """ Test the get_ssh_key_fingerprint function """
        key = "ssh-rsa AAAAB3Nza...base64data...Q== user@host"
        fingerprint = get_ssh_key_fingerprint(key)
        expected_start = "SHA256:"
        self.assertTrue(fingerprint.startswith(expected_start))

    def test_get_ssh_key_fingerprint_invalid(self):
        """ Test the get_ssh_key_fingerprint function with an invalid key """
        with self.assertRaises(ValueError):
            get_ssh_key_fingerprint("invalidkey")

    @patch("runpod.cli.groups.ssh.functions.get_user")
    def test_get_user_pub_keys(self, mock_get_user):
        """ Test the get_user_pub_keys function """

        # Create dummy base64 data for our mock SSH keys
        dummy_data1 = base64.b64encode("test data 1".encode('utf-8')).decode('utf-8')
        dummy_data2 = base64.b64encode("test data 2".encode('utf-8')).decode('utf-8')

        mock_get_user.return_value = {
            'pubKey': f'ssh-rsa {dummy_data1} key1\nssh-rsa {dummy_data2} key2\n1'
        }

        keys = get_user_pub_keys()

        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0]['fingerprint'].startswith("SHA256:"), True)

    @patch("runpod.cli.groups.ssh.functions.get_user")
    def test_add_ssh_key_already_exists(self, mock_get_user):
        """ Test the add_ssh_key function when the key already exists """
        mock_get_user.return_value = {'pubKey': 'ssh-rsa ABCDE12345 key1'}
        key = "ssh-rsa AAAAB3Nza...base64data...Q== user@host"
        with patch("runpod.cli.groups.ssh.functions.update_user_settings") as mock_update_settings:
            mock_update_settings.return_value = None
            self.assertIsNone(add_ssh_key(key))
            assert mock_update_settings.called

    @patch("runpod.cli.groups.ssh.functions.os.path.join")
    @patch("runpod.cli.groups.ssh.functions.paramiko.RSAKey.generate")
    def test_generate_ssh_key_pair(self, mock_generate, mock_path_join):
        """ Test the generate_ssh_key_pair function """
        mock_generate.return_value.get_name.return_value = "ssh-rsa"
        mock_generate.return_value.get_base64.return_value = "ABCDE12345"
        mock_path_join.return_value = "/path/to/private_key"

        with patch("os.mkdir") as mock_mkdir, \
                patch("builtins.open", mock_open()) as mock_file, \
                patch("runpod.cli.groups.ssh.functions.os.chmod") as mock_chmod, \
                patch("runpod.cli.groups.ssh.functions.add_ssh_key") as mock_add_key:
            mock_mkdir.return_value = None
            mock_file.return_value.write.return_value = None
            private_key, public_key = generate_ssh_key_pair("test_key")
            self.assertEqual(public_key, "ssh-rsa ABCDE12345 test_key")
            assert private_key is not None
            assert mock_file.called
            assert mock_add_key.called
            assert mock_chmod.called

    @patch("runpod.cli.groups.ssh.functions.get_user")
    @patch("runpod.cli.groups.ssh.functions.update_user_settings")
    def test_add_ssh_key_new(self, mock_update_settings, mock_get_user):
        """ Test the add_ssh_key function when the key is new """
        mock_get_user.return_value = {'pubKey': ''}
        key = "ssh-rsa ABCDE12345 somecomment"
        add_ssh_key(key)
        mock_update_settings.assert_called_once_with(key)

        mock_get_user.return_value = {'pubKey': 'ssh-rsa ABCDE12345 key1'}
        assert add_ssh_key('ssh-rsa ABCDE12345 key1') is None
