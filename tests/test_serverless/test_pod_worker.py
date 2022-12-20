''' Tests for runpod | serverless| modules | pod_worker.py '''

import os
import unittest
from unittest.mock import patch

from runpod.serverless import pod_worker


class TestPodWorker(unittest.TestCase):
    ''' Tests for pod_worker '''

    @patch('runpod.serverless.modules.job.get')
    @patch('runpod.serverless.modules.lifecycle.LifecycleManager')
    def test_start_worker(self, mock_worker_life, mock_job_get):
        '''
        Tests start_worker
        '''
        os.environ['DEBUG'] = 'true'

        pod_worker.start_worker()
        print(os.environ.get('DEBUG', None))

        mock_worker_life.assert_called_once_with()
        mock_worker_life.heartbeat_ping.assert_called_once_with()
        mock_job_get.assert_called_once_with(mock_worker_life.worker_id)
