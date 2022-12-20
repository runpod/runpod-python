''' Tests for runpod | serverless| modules | pod_worker.py '''

import unittest
from unittest.mock import patch

from runpod.serverless import pod_worker


class TestPodWorker(unittest.TestCase):
    ''' Tests for pod_worker '''

    @patch('runpod.serverless.modules.job')
    @patch('runpod.serverless.modules.lifecycle.LifecycleManager')
    def test_start_worker(self, mock_worker_life, mock_job):
        '''
        Tests start_worker
        '''
        pod_worker.start_worker()
        mock_worker_life.assert_called_once_with()
        mock_worker_life.heartbeat_ping.assert_called_once_with()
        mock_job.get.assert_called_once_with(mock_worker_life.worker_id)
