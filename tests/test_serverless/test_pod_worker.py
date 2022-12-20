''' Tests for runpod | serverless| modules | pod_worker.py '''

import os
import unittest
from unittest.mock import patch

from runpod.serverless import pod_worker


class TestPodWorker(unittest.TestCase):
    ''' Tests for pod_worker '''

    @patch('os.path.exists', return_value=False)
    @patch('shutil.rmtree')
    @patch('runpod.serverless.modules.job.get', return_value=None)
    @patch('runpod.serverless.modules.lifecycle.LifecycleManager')
    def test_start_worker(self, mock_worker_life, mock_job_get, mock_rmtree, mock_os_path_exists):
        '''
        Tests start_worker
        '''
        os.environ['DEBUG'] = 'true'

        pod_worker.start_worker()
        print(os.environ.get('DEBUG', None))

        mock_worker_life.assert_called_once_with()
        mock_worker_life.heartbeat_ping.assert_called_once_with()
        mock_job_get.assert_called_once_with(mock_worker_life.worker_id)
        mock_rmtree.assert_called_once_with("input_objects", ignore_errors=True)
        mock_rmtree.assert_called_once_with("output_objects", ignore_errors=True)
        mock_os_path_exists.assert_called_once_with('output.zip')
