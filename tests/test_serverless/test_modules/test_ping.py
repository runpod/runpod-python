import os
import pytest
from unittest.mock import MagicMock, patch
import requests

from runpod.serverless.modules.rp_ping import Heartbeat


class TestHeartbeat:
    """Test suite for the Heartbeat class"""

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Reset class state before and after each test"""
        # Store original state
        original_process_started = Heartbeat._process_started
        
        # Reset before test
        Heartbeat._process_started = False
        
        yield
        
        # Reset after test
        Heartbeat._process_started = original_process_started

    @pytest.fixture
    def mock_env(self):
        """Fixture to set up environment variables"""
        env_vars = {
            "RUNPOD_WEBHOOK_PING": "https://test.com/ping/$RUNPOD_POD_ID",
            "RUNPOD_AI_API_KEY": "test_api_key",
            "RUNPOD_POD_ID": "test_pod_id",
            "RUNPOD_PING_INTERVAL": "5000"
        }
        with patch.dict(os.environ, env_vars):
            yield env_vars

    @pytest.fixture
    def mock_worker_id(self):
        """Mock the WORKER_ID constant"""
        with patch("runpod.serverless.modules.rp_ping.WORKER_ID", "test_worker_123"):
            yield "test_worker_123"

    @pytest.fixture
    def mock_session(self):
        """Mock the SyncClientSession"""
        with patch("runpod.serverless.modules.rp_ping.SyncClientSession") as mock:
            session_instance = MagicMock()
            mock.return_value = session_instance
            yield session_instance

    @pytest.fixture
    def mock_jobs(self):
        """Mock the JobsProgress instance"""
        with patch("runpod.serverless.modules.rp_ping.JobsProgress") as mock:
            instance = mock.return_value
            instance.get_job_list.return_value = "job1,job2,job3"
            yield mock

    @pytest.fixture
    def mock_logger(self):
        """Mock the logger"""
        with patch("runpod.serverless.modules.rp_ping.log") as mock:
            yield mock

    def test_heartbeat_initialization(self, mock_env, mock_worker_id, mock_session):
        """Test Heartbeat initialization with various configurations"""
        heartbeat = Heartbeat()
        
        # Check URL construction
        expected_url = "https://test.com/ping/test_worker_123"
        assert heartbeat.PING_URL == expected_url
        
        # Check interval calculation
        assert heartbeat.PING_INTERVAL == 5  # 5000 // 1000
        
        # Check session setup
        mock_session.headers.update.assert_called_once_with(
            {"Authorization": "test_api_key"}
        )

    def test_heartbeat_initialization_defaults(self, mock_worker_id, mock_session):
        """Test Heartbeat initialization with default values"""
        with patch.dict(os.environ, {}, clear=True):
            heartbeat = Heartbeat()
            
            # Should use default values
            assert heartbeat.PING_URL == "PING_NOT_SET"
            assert heartbeat.PING_INTERVAL == 10  # 10000 // 1000
            
            # Authorization should be None
            mock_session.headers.update.assert_called_once_with(
                {"Authorization": ""}
            )

    def test_start_ping_missing_api_key(self, mock_logger, mock_worker_id):
        """Test start_ping when API key is missing"""
        with patch.dict(os.environ, {"RUNPOD_POD_ID": "test", "RUNPOD_WEBHOOK_PING": "test"}, clear=True):
            with patch("multiprocessing.Process") as mock_process:
                heartbeat = Heartbeat()
                heartbeat.start_ping()
                
                # Process should not be created
                mock_process.assert_not_called()
                mock_logger.debug.assert_called_with(
                    "Not deployed on Runpod serverless, pings will not be sent."
                )

    def test_start_ping_missing_pod_id(self, mock_logger, mock_worker_id):
        """Test start_ping when POD_ID is missing"""
        with patch.dict(os.environ, {"RUNPOD_AI_API_KEY": "test"}, clear=True):
            with patch("multiprocessing.Process") as mock_process:
                heartbeat = Heartbeat()
                heartbeat.start_ping()
                
                # Process should not be created
                mock_process.assert_not_called()
                mock_logger.info.assert_called_with(
                    "Not running on Runpod, pings will not be sent."
                )

    def test_start_ping_missing_webhook_url(self, mock_logger, mock_worker_id):
        """Test start_ping when webhook URL is not set"""
        with patch.dict(os.environ, {"RUNPOD_AI_API_KEY": "test", "RUNPOD_POD_ID": "test"}, clear=True):
            with patch("multiprocessing.Process") as mock_process:
                heartbeat = Heartbeat()
                heartbeat.start_ping()
                
                # Process should not be created
                mock_process.assert_not_called()
                mock_logger.error.assert_called_with(
                    "Ping URL not set, cannot start ping."
                )

    @patch("runpod.serverless.modules.rp_ping.Process")
    @patch("runpod.serverless.modules.rp_ping.SyncClientSession")
    @patch("runpod.serverless.modules.rp_ping.WORKER_ID", "test_worker_123")
    @patch.dict(os.environ, {
        "RUNPOD_WEBHOOK_PING": "https://test.com/ping/$RUNPOD_POD_ID",
        "RUNPOD_AI_API_KEY": "test_api_key",
        "RUNPOD_POD_ID": "test_pod_id",
        "RUNPOD_PING_INTERVAL": "5000"
    })
    def test_start_ping_success(self, mock_session_class, mock_process_class):
        """Test successful start_ping"""
        # Reset the class variable
        Heartbeat._process_started = False
        
        mock_process = MagicMock()
        mock_process_class.return_value = mock_process
        
        heartbeat = Heartbeat()
        heartbeat.start_ping(test=True)
        
        # Verify process was created correctly
        mock_process_class.assert_called_once_with(
            target=Heartbeat.process_loop,
            args=(True,)
        )
        
        # Verify daemon and start
        assert mock_process.daemon is True
        mock_process.start.assert_called_once()
        
        # Verify flag is set
        assert Heartbeat._process_started is True

    def test_start_ping_already_started(self, mock_env, mock_worker_id, mock_session):
        """Test start_ping when process is already started"""
        Heartbeat._process_started = True
        
        with patch("multiprocessing.Process") as mock_process:
            heartbeat = Heartbeat()
            heartbeat.start_ping()
            
            # Process should not be created again
            mock_process.assert_not_called()

    def test_process_loop(self, mock_env, mock_worker_id, mock_session):
        """Test the process_loop static method"""
        with patch.object(Heartbeat, 'ping_loop') as mock_ping_loop:
            Heartbeat.process_loop(test=True)
            
            # Should create new instance and call ping_loop
            mock_ping_loop.assert_called_once_with(True)

    def test_ping_loop_test_mode(self, mock_env, mock_worker_id, mock_session):
        """Test ping_loop in test mode (single iteration)"""
        heartbeat = Heartbeat()
        
        with patch.object(heartbeat, '_send_ping') as mock_send:
            heartbeat.ping_loop(test=True)
            
            # Should send ping once and return
            mock_send.assert_called_once()

    def test_ping_loop_continuous(self, mock_env, mock_worker_id, mock_session):
        """Test ping_loop in continuous mode"""
        heartbeat = Heartbeat()
        
        # Mock time.sleep to break the loop after 3 iterations
        call_count = 0
        def side_effect(interval):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise KeyboardInterrupt()
        
        with patch.object(heartbeat, '_send_ping') as mock_send:
            with patch('time.sleep', side_effect=side_effect):
                with pytest.raises(KeyboardInterrupt):
                    heartbeat.ping_loop(test=False)
                
                # Should have sent 3 pings
                assert mock_send.call_count == 3

    def test_send_ping_success(self, mock_env, mock_worker_id, mock_session, mock_jobs, mock_logger):
        """Test successful ping send"""
        heartbeat = Heartbeat()
        
        # Mock successful response
        mock_response = MagicMock()
        mock_response.url = "https://test.com/ping/test_worker_123"
        mock_response.status_code = 200
        mock_session.get.return_value = mock_response
        
        # Mock version
        with patch("runpod.serverless.modules.rp_ping.runpod_version", "1.0.0"):
            heartbeat._send_ping()
        
        # Verify request was made correctly
        mock_session.get.assert_called_once_with(
            "https://test.com/ping/test_worker_123",
            params={"job_id": "job1,job2,job3", "runpod_version": "1.0.0"},
            timeout=10  # PING_INTERVAL * 2
        )
        
        # Verify debug log
        mock_logger.debug.assert_called_once()

    def test_send_ping_no_jobs(self, mock_env, mock_worker_id, mock_session, mock_logger):
        """Test ping send with no jobs"""
        heartbeat = Heartbeat()
        
        # Mock no jobs
        with patch("runpod.serverless.modules.rp_ping.JobsProgress.get_job_list", return_value=None):
            mock_response = MagicMock()
            mock_response.url = "https://test.com/ping/test_worker_123"
            mock_response.status_code = 200
            mock_session.get.return_value = mock_response
            
            with patch("runpod.serverless.modules.rp_ping.runpod_version", "1.0.0"):
                heartbeat._send_ping()
            
            # Verify request params
            mock_session.get.assert_called_once_with(
                "https://test.com/ping/test_worker_123",
                params={"job_id": None, "runpod_version": "1.0.0"},
                timeout=10
            )

    def test_send_ping_request_exception(self, mock_env, mock_worker_id, mock_session, mock_jobs, mock_logger):
        """Test ping send with request exception"""
        heartbeat = Heartbeat()
        
        # Mock request exception
        mock_session.get.side_effect = requests.RequestException("Connection error")
        
        with patch("runpod.serverless.modules.rp_ping.runpod_version", "1.0.0"):
            heartbeat._send_ping()
        
        # Verify error was logged
        mock_logger.error.assert_called_once_with(
            "Ping Request Error: Connection error, attempting to restart ping."
        )

    def test_custom_pool_connections(self, mock_env, mock_worker_id, mock_session):
        """Test initialization with custom pool connections and retries"""
        heartbeat = Heartbeat(pool_connections=20, retries=5)
        
        # Should still initialize properly
        assert heartbeat.PING_URL == "https://test.com/ping/test_worker_123"

    @patch("requests.adapters.HTTPAdapter")
    def test_http_adapter_configuration(self, mock_adapter, mock_env, mock_worker_id, mock_session):
        """Test that HTTP adapter is configured correctly"""
        mock_adapter_instance = MagicMock()
        mock_adapter.return_value = mock_adapter_instance
        
        Heartbeat(pool_connections=15, retries=4)
        
        # Verify adapter was created
        assert mock_adapter.called
        
        # Verify it was called with expected pool settings
        call_kwargs = mock_adapter.call_args[1]
        assert call_kwargs['pool_connections'] == 15
        assert call_kwargs['pool_maxsize'] == 15
        assert 'max_retries' in call_kwargs
        
        # Verify adapter was mounted on both protocols
        assert mock_session.mount.call_count == 2
        mock_session.mount.assert_any_call("http://", mock_adapter_instance)
        mock_session.mount.assert_any_call("https://", mock_adapter_instance)
