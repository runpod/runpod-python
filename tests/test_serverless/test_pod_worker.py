''' Tests for runpod | serverless| modules | pod_worker.py '''

import os
import unittest
from unittest.mock import patch

from runpod.serverless import pod_worker


class TestPodWorker(unittest.TestCase):
    ''' Tests for pod_worker '''

    @patch('runpod.serverless.modules.job.get', return_value=None)
    @patch('runpod.serverless.modules.lifecycle.LifecycleManager.heartbeat_ping')
    @patch('runpod.serverless.modules.lifecycle.LifecycleManager')
    def test_start_worker(self, mock_worker_life, mock_worker_life_heartbeat, mock_job_get):
        '''
        Tests start_worker
        '''
        os.environ['DEBUG'] = 'true'

        pod_worker.start_worker()
        # print(os.environ.get('DEBUG', None))

        mock_worker_life.assert_called_once_with()
        mock_worker_life_heartbeat.assert_called_once_with()
        mock_job_get.assert_called_once_with(mock_worker_life.worker_id)

    # @patch('os.path.exists', return_value=False)
    # @patch('shutil.rmtree')
    # def test_worker_file_management(self, mock_rmtree, mock_os_path_exists):
    #     '''
    #     Tests worker file
    #     '''
    #     mock_rmtree.assert_called_once_with("input_objects", ignore_errors=True)
    #     mock_rmtree.assert_called_once_with("output_objects", ignore_errors=True)
    #     mock_os_path_exists.assert_called_once_with('output.zip')
