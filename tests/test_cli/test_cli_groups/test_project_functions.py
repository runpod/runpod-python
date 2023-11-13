""" Test functions in runpod.cli.groups.project.functions module. """

import os
import unittest
from unittest.mock import patch, mock_open

from runpod.cli.groups.project.functions import (
    STARTER_TEMPLATES, create_new_project,
    start_project, create_project_endpoint
)


class TestCreateNewProject(unittest.TestCase):
    """ Test the create_new_project function."""

    @patch("os.makedirs")
    @patch("os.path.exists", return_value=False)
    @patch("os.getcwd", return_value="/current/path")
    @patch("runpod.cli.groups.project.functions.copy_template_files")
    def test_create_project_folder(self, mock_copy_template_files, mock_getcwd, mock_exists, mock_makedirs):  # pylint: disable=line-too-long
        """ Test that a new project folder is created if init_current_dir is False. """
        with patch("builtins.open", new_callable=mock_open):
            create_new_project("test_project", "volume_id", "11.1.1", "3.8")
        mock_makedirs.assert_called_once_with("/current/path/test_project")
        assert mock_copy_template_files.called
        assert mock_getcwd.called
        assert mock_exists.called

    @patch('os.makedirs')
    @patch('os.path.exists', return_value=False)
    @patch('os.getcwd', return_value='/tmp/testdir')
    @patch('builtins.open', new_callable=mock_open)
    def test_create_new_project_init_current_dir(self, mock_file_open, mock_getcwd, mock_path_exists, mock_makedirs):  # pylint: disable=line-too-long
        """ Test that a new project folder is not created if init_current_dir is True. """
        project_name = "test_project"
        runpod_volume_id = "12345"
        cuda_version = "11.1.1"
        python_version = "3.9"

        create_new_project(
            project_name, runpod_volume_id, cuda_version, python_version, init_current_dir=True)
        mock_makedirs.assert_not_called()
        mock_file_open.assert_called_with('/tmp/testdir/runpod.toml', 'w', encoding="UTF-8")
        assert mock_getcwd.called
        assert mock_path_exists.called is False

    @patch("os.makedirs")
    @patch("os.path.exists", return_value=False)
    @patch("os.getcwd", return_value="/current/path")
    @patch("runpod.cli.groups.project.functions.copy_template_files")
    def test_copy_template_files(self, mock_copy_template_files, mock_getcwd, mock_exists, mock_makedirs):  # pylint: disable=line-too-long
        """ Test that template files are copied to the new project folder. """
        with patch("builtins.open", new_callable=mock_open):
            create_new_project("test_project", "volume_id", "11.1.1", "3.8")
        mock_copy_template_files.assert_called_once_with(
            STARTER_TEMPLATES + "/default", "/current/path/test_project")  # pylint: disable=line-too-long
        assert mock_getcwd.called
        assert mock_exists.called
        assert mock_makedirs.called

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data="data with <<MODEL_NAME>> placeholder")  # pylint: disable=line-too-long
    def test_replace_placeholders_in_handler(self, mock_open_file, mock_exists):  # pylint: disable=line-too-long
        """ Test that placeholders in handler.py are replaced if model_name is given. """
        with patch("runpod.cli.groups.project.functions.copy_template_files"):
            create_new_project("test_project", "volume_id", "11.8.0", "3.8", model_name="my_model")
        assert mock_open_file.called
        assert mock_exists.called

    @patch("os.path.exists", return_value=False)
    @patch("builtins.open", new_callable=mock_open)
    def test_create_runpod_toml(self, mock_open_file, mock_exists):
        """ Test that runpod.toml configuration file is created. """
        with patch("runpod.cli.groups.project.functions.copy_template_files"):
            create_new_project("test_project", "volume_id", "11.8.0", "3.8")
        toml_file_location = os.path.join(os.getcwd(), "test_project", "runpod.toml")
        mock_open_file.assert_called_with(
            toml_file_location, 'w', encoding="UTF-8")  # pylint: disable=line-too-long
        assert mock_exists.called

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data="<<RUNPOD>> placeholder")
    def test_update_requirements_file(self, mock_open_file, mock_exists):
        """ Test that placeholders in requirements.txt are replaced correctly. """
        with patch("runpod.cli.groups.project.functions.__version__", "dev"), \
                patch("runpod.cli.groups.project.functions.copy_template_files"):
            create_new_project("test_project", "volume_id", "11.8.0", "3.8")
        assert mock_open_file.called
        assert mock_exists.called

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data="<<RUNPOD>> placeholder")
    def test_update_requirements_file_non_dev(self, mock_open_file, mock_exists):
        """ Test that placeholders in requirements.txt are replaced for non-dev versions. """
        with patch("runpod.cli.groups.project.functions.__version__", "1.0.0"), \
                patch("runpod.cli.groups.project.functions.copy_template_files"):
            create_new_project("test_project", "volume_id", "11.8.0", "3.8")
        assert mock_open_file.called
        assert mock_exists.called


class TestStartProject(unittest.TestCase):
    """ Test the start_project function. """

    @patch('runpod.cli.groups.project.functions.load_project_config')
    @patch('runpod.cli.groups.project.functions.get_project_pod')
    @patch('runpod.cli.groups.project.functions.attempt_pod_launch')
    @patch('runpod.cli.groups.project.functions.get_pod')
    @patch('runpod.cli.groups.project.functions.SSHConnection')
    @patch('os.getcwd', return_value='/current/path')
    def test_start_nonexistent_successfully(self, mock_getcwd, mock_ssh_connection, mock_get_pod, mock_attempt_pod_launch, mock_get_project_pod, mock_load_project_config):  # pylint: disable=line-too-long, too-many-arguments
        """ Test that a project is launched successfully. """
        mock_load_project_config.return_value = {
            'project': {
                'uuid': '123456',
                'name': 'test_project',
                'volume_mount_path': '/mount/path',
                'env_vars': {'ENV_VAR': 'value'},
                'gpu': 'NVIDIA GPU'
            },
            'runtime': {
                'python_version': '3.8',
                'handler_path': 'handler.py',
                'requirements_path': 'requirements.txt'
            }
        }

        mock_get_project_pod.return_value = False

        mock_attempt_pod_launch.return_value = {
            'id': 'new_pod_id',
            'desiredStatus': 'PENDING',
            'runtime': None
        }

        mock_get_pod.return_value = {
            'id': 'new_pod_id',
            'desiredStatus': 'RUNNING',
            'runtime': 'ONLINE'
        }

        mock_ssh_instance = mock_ssh_connection.return_value
        mock_ssh_instance.__enter__.return_value = mock_ssh_instance
        mock_ssh_instance.run_commands.return_value = None

        with patch('runpod.cli.groups.project.functions.sync_directory') as mock_sync_directory:
            start_project()

        mock_attempt_pod_launch.assert_called()
        mock_get_pod.assert_called_with('new_pod_id')
        mock_ssh_connection.assert_called_with('new_pod_id')
        mock_ssh_instance.run_commands.assert_called()
        assert mock_getcwd.called
        assert mock_sync_directory.called

    @patch('runpod.cli.groups.project.functions.get_project_pod')
    @patch('runpod.cli.groups.project.functions.attempt_pod_launch')
    def test_failed_pod_launch(self, mock_attempt_pod, mock_get_pod):
        """ Test that a project is not launched if pod launch fails. """
        mock_attempt_pod.return_value = None
        mock_get_pod.return_value = None

        with patch('builtins.print') as mock_print, \
                patch('runpod.cli.groups.project.functions.load_project_config'):

            start_project()
            mock_print.assert_called_with(
                "Selected GPU types unavailable, try again later or use a different type.")  # pylint: disable=line-too-long


class TestStartProjectAPI(unittest.TestCase):
    """ Test the start_project_api function. """

    @patch('runpod.cli.groups.project.functions.load_project_config')
    @patch('runpod.cli.groups.project.functions.get_project_pod')
    @patch('runpod.cli.groups.project.functions.SSHConnection')
    @patch('os.getcwd', return_value='/current/path')
    @patch('runpod.cli.groups.project.functions.sync_directory')
    def test_start_project_api_successfully(self, mock_sync_directory, mock_getcwd, mock_ssh_connection, mock_get_project_pod, mock_load_project_config):  # pylint: disable=line-too-long, too-many-arguments
        """ Test that a project API is started successfully. """
        mock_load_project_config.return_value = {
            'project': {
                'uuid': '123456',
                'name': 'test_project',
                'volume_mount_path': '/mount/path'
            },
            'runtime': {
                'python_version': '3.8',
                'handler_path': 'handler.py',
                'requirements_path': 'requirements.txt'
            }
        }

        mock_get_project_pod.return_value = {'id': 'pod_id'}

        mock_ssh_instance = mock_ssh_connection.return_value
        mock_ssh_instance.__enter__.return_value = mock_ssh_instance
        mock_ssh_instance.run_commands.return_value = None

        start_project()

        mock_get_project_pod.assert_called_with('123456')
        mock_ssh_connection.assert_called_with({'id': 'pod_id'})
        mock_sync_directory.assert_called_with(mock_ssh_instance,
                                               '/current/path', '/mount/path/123456/dev')
        mock_ssh_instance.run_commands.assert_called()
        assert mock_getcwd.called


class TestCreateProjectEndpoint(unittest.TestCase):
    """ Test the create_project_endpoint function. """

    @patch('runpod.cli.groups.project.functions.SSHConnection')
    @patch('runpod.cli.groups.project.functions.load_project_config')
    @patch('runpod.cli.groups.project.functions.create_template')
    @patch('runpod.cli.groups.project.functions.create_endpoint')
    @patch('runpod.cli.groups.project.functions.update_endpoint_template')
    @patch('runpod.cli.groups.project.functions.get_project_pod')
    @patch('runpod.cli.groups.project.functions.get_project_endpoint')
    def test_create_project_endpoint(self, mock_get_project_endpoint, mock_get_project_pod, mock_update_endpoint, mock_create_endpoint,  # pylint: disable=too-many-arguments,line-too-long
                                     mock_create_template, mock_load_project_config, mock_ssh_connection):  # pylint: disable=line-too-long
        """ Test that a project endpoint is created successfully. """
        mock_get_project_endpoint.return_value = False

        mock_get_project_pod.return_value = None
        with patch('runpod.cli.groups.project.functions._launch_dev_pod') as mock_launch_dev_pod:
            mock_launch_dev_pod.return_value = None
            assert create_project_endpoint() is None

        mock_get_project_pod.return_value = {'id': 'test_pod_id'}
        mock_load_project_config.return_value = {
            'project': {
                'name': 'test_project',
                'volume_mount_path': '/runpod-volume/123456',
                'uuid': '123456',
                'env_vars': {'TEST_VAR': 'value'},
                'base_image': 'test_image',
                'container_disk_size_gb': 10,
                'storage_id': 'test_storage_id',
            },
            'runtime': {
                'python_version': '3.8',
                'handler_path': 'handler.py',
                'requirements_path': 'requirements.txt'
            }
        }
        mock_create_template.return_value = {'id': 'test_template_id'}
        mock_create_endpoint.return_value = {'id': 'test_endpoint_id'}

        mock_ssh_instance = mock_ssh_connection.return_value
        mock_ssh_instance.__enter__.return_value = mock_ssh_instance
        mock_ssh_instance.run_commands.return_value = None

        with patch('runpod.cli.groups.project.functions.datetime') as mock_datetime:
            mock_datetime.now.return_value = '123456'
            result = create_project_endpoint()

        self.assertEqual(result, 'test_endpoint_id')
        mock_create_template.assert_called_with(
            name='test_project-endpoint | 123456 | 123456',
            image_name='test_image',
            container_disk_in_gb=10,
            docker_start_cmd='bash -c ". /runpod-volume/123456/prod/venv/bin/activate && python -u /runpod-volume/123456/prod/test_project/handler.py"',  # pylint: disable=line-too-long
            env={'TEST_VAR': 'value'},
            is_serverless=True
        )
        mock_create_endpoint.assert_called_with(
            name='test_project-endpoint | 123456',
            template_id='test_template_id',
            network_volume_id='test_storage_id'
        )

        mock_update_endpoint.return_value = {'id': 'test_endpoint_id'}
        mock_get_project_endpoint.return_value = {'id': 'test_endpoint_id'}
        self.assertEqual(create_project_endpoint(), 'test_endpoint_id')
