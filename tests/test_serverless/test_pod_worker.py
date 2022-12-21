''' Tests for runpod | serverless| modules | pod_worker.py '''

import unittest
from unittest.mock import patch

from runpod.serverless import pod_worker


class TestPodWorker(unittest.TestCase):
    ''' Tests for pod_worker '''

    @patch('runpod.serverless.modules.job.get', return_value=None)
    @patch('runpod.serverless.modules.lifecycle.LifecycleManager')
    def test_start_worker(self, mock_worker_life, mock_job_get):
        '''
        Tests start_worker
        '''
        pod_worker.start_worker()

        mwl_instance = mock_worker_life.return_value

        mock_worker_life.assert_called_once_with()
        mwl_instance.heartbeat_ping.assert_called_once_with()
        mock_job_get.assert_called_once_with(mwl_instance.worker_id)

    # @patch('os.path.exists', return_value=False)
    # @patch('shutil.rmtree')
    # def test_worker_file_management(self, mock_rmtree, mock_os_path_exists):
    #     '''
    #     Tests worker file
    #     '''
    #     mock_rmtree.assert_called_once_with("input_objects", ignore_errors=True)
    #     mock_rmtree.assert_called_once_with("output_objects", ignore_errors=True)
    #     mock_os_path_exists.assert_called_once_with('output.zip')
