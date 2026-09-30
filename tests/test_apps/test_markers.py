import pytest

from runpod.apps.markers import delete, get, patch, post, put


@pytest.mark.parametrize("marker", [get, post, put, delete, patch])
@pytest.mark.parametrize("path", ["/ping", "/execute", "/_runpod/sync"])
def test_runtime_control_paths_are_rejected(marker, path):
    with pytest.raises(ValueError):
        marker(path)
