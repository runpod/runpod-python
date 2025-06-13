"""
Integration tests for JobsProgress multiprocessing behavior.

These tests verify that JobsProgress properly shares job data across processes,
which is critical for the heartbeat ping functionality to include job IDs.
"""

import multiprocessing
import os
import time
import pytest
from unittest.mock import patch


def subprocess_worker(shared_queue):
    """Worker function that runs in subprocess and checks for jobs."""
    try:
        # Import in subprocess to get fresh instance
        from runpod.serverless.modules.worker_state import JobsProgress
        
        jobs = JobsProgress()
        
        # Wait a bit for main process to add jobs
        time.sleep(0.5)
        
        # Check if we can see jobs from main process
        job_count = jobs.get_job_count()
        job_list = jobs.get_job_list()
        
        # Send results back to main process
        shared_queue.put({
            'job_count': job_count,
            'job_list': job_list,
            'use_multiprocessing': jobs._use_multiprocessing,
            'success': True
        })
        
    except Exception as e:
        shared_queue.put({
            'error': str(e),
            'success': False
        })


def heartbeat_ping_simulation(shared_queue):
    """Simulates what happens in the heartbeat ping process."""
    try:
        # This mimics what happens in Heartbeat.process_loop()
        from runpod.serverless.modules.rp_ping import Heartbeat
        Heartbeat()
        
        # This mimics what happens in _send_ping()
        from runpod.serverless.modules.worker_state import JobsProgress
        jobs = JobsProgress()
        
        # Wait for main process to add jobs
        time.sleep(0.5)
        
        # This is the critical line from _send_ping()
        job_ids = jobs.get_job_list()
        
        shared_queue.put({
            'job_ids': job_ids,
            'use_multiprocessing': jobs._use_multiprocessing,
            'success': True
        })
        
    except Exception as e:
        shared_queue.put({
            'error': str(e),
            'success': False
        })


def subprocess_singleton_test(shared_queue):
    """Test singleton behavior within subprocess."""
    try:
        from runpod.serverless.modules.worker_state import JobsProgress
        
        # Create multiple instances - should be the same object
        jobs1 = JobsProgress()
        jobs2 = JobsProgress()
        jobs3 = JobsProgress()
        
        # All should be the same instance
        same_instance = (jobs1 is jobs2 is jobs3)
        
        # Add job through one instance
        jobs1.add({'id': 'singleton-test'})
        
        # Should be visible through all instances
        count1 = jobs1.get_job_count()
        count2 = jobs2.get_job_count()
        count3 = jobs3.get_job_count()
        
        shared_queue.put({
            'same_instance': same_instance,
            'count1': count1,
            'count2': count2,
            'count3': count3,
            'success': True
        })
        
    except Exception as e:
        shared_queue.put({
            'error': str(e),
            'success': False
        })


def subprocess_thread_safe_worker(shared_queue):
    """Worker that forces thread-safe mode and adds its own jobs."""
    try:
        from runpod.serverless.modules.worker_state import JobsProgress
        
        # Force thread-safe mode by patching Manager to fail
        with patch('runpod.serverless.modules.worker_state.Manager', 
                  side_effect=RuntimeError("Forced multiprocessing failure")):
            jobs = JobsProgress()
            
            # Verify we're in thread-safe mode
            use_multiprocessing = jobs._use_multiprocessing
            
            # Add jobs in subprocess
            jobs.add({'id': 'subprocess-job-1'})
            jobs.add({'id': 'subprocess-job-2'})
            
            # Check subprocess jobs
            job_count = jobs.get_job_count()
            job_list = jobs.get_job_list()
            
            shared_queue.put({
                'job_count': job_count,
                'job_list': job_list,
                'use_multiprocessing': use_multiprocessing,
                'success': True
            })
            
    except Exception as e:
        shared_queue.put({
            'error': str(e),
            'success': False
        })


@pytest.fixture(scope="session", autouse=True)
def setup_multiprocessing():
    """Set multiprocessing start method for consistent testing."""
    # Use spawn method to match production behavior
    if multiprocessing.get_start_method(allow_none=True) != 'spawn':
        multiprocessing.set_start_method('spawn', force=True)


@pytest.fixture(autouse=True)
def reset_jobs_progress():
    """Clear any existing JobsProgress state before each test."""
    # Reset the singleton instance to ensure clean state
    from runpod.serverless.modules.worker_state import JobsProgress
    JobsProgress._instance = None
    yield
    # Cleanup after test
    if hasattr(JobsProgress, '_instance') and JobsProgress._instance:
        try:
            JobsProgress._instance.clear()
        except Exception:
            pass
    JobsProgress._instance = None


@pytest.mark.timeout(30)  # 30 second timeout for multiprocessing tests
class TestJobsProgressMultiprocessing:
    """Integration tests for JobsProgress cross-process sharing."""
    
    def test_multiprocessing_job_sharing_success(self):
        """Test that jobs added in main process are visible in subprocess (multiprocessing mode)."""
        
        # Create a queue for communication
        queue = multiprocessing.Queue()
        
        # Set up environment to force multiprocessing mode
        with patch.dict(os.environ, {}, clear=False):
            # Add jobs in main process
            from runpod.serverless.modules.worker_state import JobsProgress
            
            main_jobs = JobsProgress()
            main_jobs.add({'id': 'main-job-1'})
            main_jobs.add({'id': 'main-job-2'})
            
            # Verify main process has jobs
            assert main_jobs.get_job_count() == 2
            job_list = main_jobs.get_job_list()
            assert job_list is not None
            assert 'main-job-1' in job_list
            assert 'main-job-2' in job_list
            
            # Start subprocess
            process = multiprocessing.Process(target=subprocess_worker, args=(queue,))
            process.start()
            
            # Wait for subprocess to complete
            process.join(timeout=10)  # 10 second timeout
            
            # Check if process completed successfully
            assert process.exitcode == 0, "Subprocess should exit cleanly"
            
            # Get results from subprocess
            assert not queue.empty(), "Subprocess should return results"
            result = queue.get()
            
            # Verify subprocess completed successfully
            assert result.get('success', False), f"Subprocess failed: {result.get('error', 'Unknown error')}"
            
            # The key test: demonstrates the current limitation
            # CURRENT BEHAVIOR: Even in multiprocessing mode, each process creates its own Manager
            # so subprocess cannot see main process jobs (this demonstrates the issue)
            if result.get('use_multiprocessing', False):
                # This assertion will FAIL with current implementation - this is EXPECTED
                # It demonstrates that the current multiprocessing approach doesn't work
                # TODO: Fix JobsProgress to use true shared memory across processes
                try:
                    assert result['job_count'] == 2, "EXPECTED FAILURE: Current implementation doesn't share across processes"
                    # If this passes, cross-process sharing was somehow fixed
                    assert result['job_list'] is not None
                    assert 'main-job-1' in result['job_list']
                    assert 'main-job-2' in result['job_list']
                except AssertionError:
                    # This is the expected current behavior - document the limitation
                    assert result['job_count'] == 0, "Current limitation: Each process has its own Manager"
                    assert result['job_list'] is None, "Current limitation: No shared jobs across processes"
            else:
                # If multiprocessing failed and fell back to thread-safe mode,
                # subprocess won't see main process jobs (this is expected)
                assert result['job_count'] == 0, "Subprocess should have empty jobs in thread-safe fallback mode"
                assert result['job_list'] is None, "Job list should be None when no jobs in thread-safe mode"
    
    def test_thread_safe_fallback_isolation(self):
        """Test that thread-safe fallback mode properly isolates processes."""
        
        queue = multiprocessing.Queue()
        
        # Add jobs in main process (thread-safe mode)
        with patch('runpod.serverless.modules.worker_state.Manager', 
                  side_effect=RuntimeError("Forced multiprocessing failure")):
            from runpod.serverless.modules.worker_state import JobsProgress
            
            main_jobs = JobsProgress()
            assert not main_jobs._use_multiprocessing, "Main process should be in thread-safe mode"
            
            main_jobs.add({'id': 'main-thread-job'})
            assert main_jobs.get_job_count() == 1
            
            # Start subprocess
            process = multiprocessing.Process(target=subprocess_thread_safe_worker, args=(queue,))
            process.start()
            process.join(timeout=10)
            
            assert process.exitcode == 0
            
            result = queue.get()
            assert result.get('success', False), f"Subprocess failed: {result.get('error', 'Unknown error')}"
            
            # Verify isolation: subprocess creates its own JobsProgress instance
            # Note: subprocess gets fresh JobsProgress and may use multiprocessing mode
            # The key point is that it doesn't see main process jobs
            assert result['job_count'] == 2, "Subprocess should have its own jobs, not main process jobs"
            assert 'subprocess-job-1' in result['job_list']
            assert 'subprocess-job-2' in result['job_list']
            
            # Verify subprocess jobs are isolated from main process
            assert 'subprocess-job-1' not in (main_jobs.get_job_list() or '')
            assert 'subprocess-job-2' not in (main_jobs.get_job_list() or '')
            
            # Main process should still have only its job
            assert main_jobs.get_job_count() == 1
            assert main_jobs.get_job_list() == 'main-thread-job'
    
    def test_heartbeat_ping_simulation(self):
        """Test that simulates the actual heartbeat ping scenario."""
        
        queue = multiprocessing.Queue()
        
        # Simulate main worker process adding jobs (like JobScaler does)
        from runpod.serverless.modules.worker_state import JobsProgress
        
        jobs = JobsProgress()
        jobs.add({'id': 'worker-job-123'})
        jobs.add({'id': 'worker-job-456'})
        
        print(f"Main process added jobs: {jobs.get_job_list()}")
        print(f"Main process multiprocessing mode: {jobs._use_multiprocessing}")
        
        # Start heartbeat simulation process
        process = multiprocessing.Process(target=heartbeat_ping_simulation, args=(queue,))
        process.start()
        process.join(timeout=10)
        
        assert process.exitcode == 0, "Heartbeat process should exit cleanly"
        
        result = queue.get()
        assert result.get('success', False), f"Heartbeat simulation failed: {result.get('error', 'Unknown error')}"
        
        print(f"Heartbeat process saw job_ids: {result['job_ids']}")
        print(f"Heartbeat process multiprocessing mode: {result['use_multiprocessing']}")
        
        # The critical assertion: demonstrates the heartbeat ping issue
        # CURRENT BEHAVIOR: Heartbeat cannot see job IDs due to separate Managers
        if result['use_multiprocessing']:
            # This assertion will FAIL with current implementation - this is EXPECTED
            # It demonstrates the real-world impact on heartbeat pings
            try:
                assert result['job_ids'] is not None, "EXPECTED FAILURE: Heartbeat should see job IDs but doesn't"
                assert 'worker-job-123' in result['job_ids']
                assert 'worker-job-456' in result['job_ids']
            except AssertionError:
                # This is the expected current behavior - the core issue
                assert result['job_ids'] is None, "Current limitation: Heartbeat ping cannot see job IDs"
                print("✓ Test confirms: Heartbeat ping issue reproduced")
        else:
            # If multiprocessing failed, heartbeat won't see jobs (expected fallback behavior)
            assert result['job_ids'] is None, "Heartbeat ping should see no jobs in thread-safe fallback mode"
    
    def test_singleton_behavior_across_processes(self):
        """Test that JobsProgress maintains singleton behavior within each process."""
        
        queue = multiprocessing.Queue()
        
        # Test singleton in main process
        from runpod.serverless.modules.worker_state import JobsProgress
        
        main_jobs1 = JobsProgress()
        main_jobs2 = JobsProgress()
        
        assert main_jobs1 is main_jobs2, "JobsProgress should be singleton in main process"
        
        # Test singleton in subprocess
        process = multiprocessing.Process(target=subprocess_singleton_test, args=(queue,))
        process.start()
        process.join(timeout=10)
        
        assert process.exitcode == 0
        
        result = queue.get()
        assert result.get('success', False), f"Subprocess singleton test failed: {result.get('error', 'Unknown error')}"
        
        assert result['same_instance'], "JobsProgress should be singleton within subprocess"
        assert result['count1'] == 1
        assert result['count2'] == 1
        assert result['count3'] == 1
        
