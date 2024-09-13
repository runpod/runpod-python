""" Unit testing for runpod.cli.utils.rp_info.py """

from unittest.mock import patch

import pytest

from runpod.cli.utils.rp_info import get_pod_ssh_ip_port


class TestGetPodSSHIpPort:
    """Unit testing for get_pod_ssh_ip_port"""

    def test_get_pod_ssh_ip_port_normal(self):
        """Test get_pod_ssh_ip_port normal"""
        with patch("runpod.cli.utils.rp_info.get_pod") as mock_get_pod:
            mock_get_pod.return_value = {
                "desiredStatus": "RUNNING",
                "runtime": {
                    "ports": [
                        {"privatePort": 22, "ip": "127.0.0.1", "publicPort": 2222}
                    ]
                },
            }

            ip, port = get_pod_ssh_ip_port("pod_id")
            assert ip == "127.0.0.1"
            assert port == 2222

    def test_get_pod_ssh_ip_port_timeout(self):
        """Test get_pod_ssh_ip_port timeout"""
        with patch("runpod.cli.utils.rp_info.get_pod") as mock_get_pod:
            mock_get_pod.return_value = {"desiredStatus": "RUNNING", "runtime": None}

            with pytest.raises(TimeoutError):
                get_pod_ssh_ip_port("pod_id", timeout=0.1)

            mock_get_pod.return_value = {"desiredStatus": "NOT_RUNNING"}

            with pytest.raises(TimeoutError):
                get_pod_ssh_ip_port("pod_id", timeout=0.1)

            mock_get_pod.return_value = {}

            with pytest.raises(TimeoutError):
                get_pod_ssh_ip_port("pod_id", timeout=0.1)
