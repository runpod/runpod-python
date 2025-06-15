"""
Integration test for worker state persistence between job_scaler and heartbeat.
This test mimics the runpod.serverless.worker.run_worker path.
"""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from runpod.serverless.modules.rp_ping import Heartbeat
from runpod.serverless.modules.rp_scale import JobScaler
from runpod.serverless.modules.worker_state import JobsProgress


class TestWorkerStateIntegration:
    """Test the integration between JobScaler and Heartbeat for state persistence."""
    
    def setup_method(self):
        """Setup test environment."""
        # Clear any existing singleton instance
        JobsProgress._instance = None
        
        # Create a temporary directory for state files
        self.temp_dir = tempfile.mkdtemp()
        
        # Mock environment variables for testing
        self.env_patcher = patch.dict(os.environ, {
            'RUNPOD_AI_API_KEY': 'test_key',
            'RUNPOD_POD_ID': 'test_pod_id',
            'RUNPOD_WEBHOOK_PING': 'http://test.com/ping',
            'RUNPOD_PING_INTERVAL': '5000'
        })
        self.env_patcher.start()
        
        # Mock the state directory to use our temp directory
        self.state_dir_patcher = patch.object(JobsProgress, '_STATE_DIR', self.temp_dir)
        self.state_file_patcher = patch.object(JobsProgress, '_STATE_FILE', 
                                               os.path.join(self.temp_dir, '.runpod_jobs.pkl'))
        self.state_dir_patcher.start()
        self.state_file_patcher.start()
    
    def teardown_method(self):
        """Cleanup test environment."""
        self.env_patcher.stop()
        self.state_dir_patcher.stop()
        self.state_file_patcher.stop()
        
        # Clean up temp directory
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        
        # Reset singleton
        JobsProgress._instance = None
    
    def test_jobs_progress_singleton_persistence(self):
        """Test that JobsProgress maintains singleton behavior across processes."""
        jobs1 = JobsProgress()
        jobs2 = JobsProgress()
        
        assert jobs1 is jobs2
        
        # Add a job and verify it's visible in both instances
        jobs1.add("test_job_1")
        assert "test_job_1" in jobs2.get_job_list()
    
    def test_file_based_state_persistence(self):
        """Test that job state persists to file and can be loaded."""
        # Create initial instance and add jobs
        jobs1 = JobsProgress()
        jobs1.add("job_1")
        jobs1.add("job_2")
        
        # Verify state is saved to file
        assert os.path.exists(jobs1._STATE_FILE)
        
        # Reset singleton to simulate new process
        JobsProgress._instance = None
        
        # Create new instance and verify state is loaded
        jobs2 = JobsProgress()
        job_list = jobs2.get_job_list()
        
        assert "job_1" in job_list
        assert "job_2" in job_list
    
    def test_jobs_progress_add_and_remove_jobs(self):
        """Test JobsProgress job tracking functionality."""
        # Reset JobsProgress singleton
        JobsProgress._instance = None
        
        # Get JobsProgress instance
        jobs_progress = JobsProgress()
        
        # Test adding jobs
        test_jobs = [
            {"id": "job_1", "input": {"test": "data1"}},
            {"id": "job_2", "input": {"test": "data2"}}
        ]
        
        # Add jobs
        for job in test_jobs:
            jobs_progress.add(job)
        
        # Verify initial state
        assert len(jobs_progress) == 2
        job_list = jobs_progress.get_job_list()
        assert job_list is not None
        assert "job_1" in job_list
        assert "job_2" in job_list
        
        # Test removing jobs
        jobs_progress.remove(test_jobs[0])
        
        # Verify removal
        assert len(jobs_progress) == 1
        
        # Get remaining job
        remaining_list = jobs_progress.get_job_list()
        assert remaining_list is not None
        assert "job_1" not in remaining_list
        assert "job_2" in remaining_list
        
        # Test clearing jobs
        jobs_progress.clear()
        
        # Verify clearing
        assert len(jobs_progress) == 0
        assert jobs_progress.get_job_list() is None
    
    def test_heartbeat_reads_job_progress(self):
        """Test that Heartbeat can read jobs from JobsProgress."""
        # Add jobs to progress
        jobs_progress = JobsProgress()
        jobs_progress.add("job_1")
        jobs_progress.add("job_2")
        
        # Create heartbeat instance
        heartbeat = Heartbeat()
        
        # Mock the session.get method to capture the ping parameters
        with patch.object(heartbeat, '_session') as mock_session:
            mock_response = MagicMock()
            mock_response.url = "http://test.com/ping"
            mock_response.status_code = 200
            mock_session.get.return_value = mock_response
            
            # Send a ping
            heartbeat._send_ping()
            
            # Verify the ping was sent with job_ids
            mock_session.get.assert_called_once()
            call_args = mock_session.get.call_args
            
            # Check that job_id parameter contains our jobs
            params = call_args[1]['params']
            assert 'job_id' in params
            job_ids = params['job_id']
            assert "job_1" in job_ids
            assert "job_2" in job_ids
    
    def test_multiprocess_heartbeat_state_access(self):
        """Test that heartbeat process can access job state from main process."""
        # Add jobs in main process
        main_jobs = JobsProgress()
        main_jobs.add("main_job_1")
        main_jobs.add("main_job_2")
        
        # Simulate what happens in the heartbeat process
        # The process_loop creates a new Heartbeat instance
        heartbeat = Heartbeat()
        
        # Mock the session to capture ping data
        with patch.object(heartbeat, '_session') as mock_session:
            mock_response = MagicMock()
            mock_response.url = "http://test.com/ping"
            mock_response.status_code = 200
            mock_session.get.return_value = mock_response
            
            # Send ping - this should read from the persisted state
            heartbeat._send_ping()
            
            # Verify the ping includes jobs from main process
            call_args = mock_session.get.call_args
            params = call_args[1]['params']
            job_ids = params['job_id']
            
            assert "main_job_1" in job_ids
            assert "main_job_2" in job_ids
    
    @pytest.mark.asyncio
    async def test_end_to_end_job_lifecycle(self):
        """Test complete job lifecycle: add -> process -> remove -> ping."""
        # Mock job data
        test_jobs = [{"id": "lifecycle_job", "input": {"test": "data"}}]
        
        async def mock_jobs_fetcher(session, count):
            return test_jobs[:count]
        
        async def mock_job_handler(session, config, job):
            await asyncio.sleep(0.1)  # Simulate processing
        
        config = {
            "handler": lambda x: x,
            "jobs_fetcher": mock_jobs_fetcher,
            "jobs_handler": mock_job_handler,
            "jobs_fetcher_timeout": 1
        }
        
        # Create instances
        job_scaler = JobScaler(config)
        heartbeat = Heartbeat()
        jobs_progress = JobsProgress()
        
        # Track ping calls
        ping_calls = []
        
        def capture_ping(*args, **kwargs):
            job_ids = kwargs.get('params', {}).get('job_id', '')
            ping_calls.append(job_ids)
            mock_response = MagicMock()
            mock_response.url = "http://test.com/ping"
            mock_response.status_code = 200
            return mock_response
        
        with patch.object(heartbeat, '_session') as mock_session:
            mock_session.get.side_effect = capture_ping
            
            # Start job processing
            session = AsyncMock()
            
            # Add job
            await job_scaler.jobs_queue.put(test_jobs[0])
            jobs_progress.add(test_jobs[0]["id"])
            
            # Send ping with job active
            heartbeat._send_ping()
            
            # Process job (this should remove it from progress)
            await job_scaler.handle_job(session, test_jobs[0])
            
            # Send ping after job completion
            heartbeat._send_ping()
            
            # Verify ping behavior
            assert len(ping_calls) == 2
            assert "lifecycle_job" in ping_calls[0]  # Job was active
            assert ping_calls[1] is None  # Job completed, no active jobs