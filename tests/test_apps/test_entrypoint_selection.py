"""selection and restoration of local entry hooks."""

import pytest

from runpod.apps.entrypoint import (
    _clear_entrypoints,
    _restore_entrypoints,
    get_entrypoint,
    get_entrypoints,
    local_entrypoint,
    select_entrypoint,
)


@pytest.fixture(autouse=True)
def clean_entrypoints():
    _clear_entrypoints()
    yield
    _clear_entrypoints()


def test_no_entrypoint_is_optional():
    assert select_entrypoint() is None
    assert get_entrypoint() is None


def test_single_hook_is_selected():
    @local_entrypoint
    def main():
        pass

    assert select_entrypoint() is main
    assert select_entrypoint(f"{__name__}:main") is main


def test_multiple_hooks_are_not_silently_selected():
    @local_entrypoint
    def first():
        pass

    @local_entrypoint
    def second():
        pass

    assert get_entrypoint() is second
    with pytest.raises(ValueError, match="multiple.*first.*second"):
        select_entrypoint()
    assert select_entrypoint(f"{__name__}:first") is first


def test_unknown_hook_lists_available_hooks():
    @local_entrypoint
    def main():
        pass

    with pytest.raises(ValueError, match="not found; available:.*:main"):
        select_entrypoint("missing:main")


def test_duplicate_selector_is_ambiguous():
    def main():
        pass

    local_entrypoint(main)
    local_entrypoint(main)
    with pytest.raises(ValueError, match="multiple"):
        select_entrypoint(f"{__name__}:main")


def test_registry_snapshot_can_be_restored():
    @local_entrypoint
    def main():
        pass

    previous = get_entrypoints()
    _clear_entrypoints()
    _restore_entrypoints(previous)
    previous.clear()
    assert get_entrypoints() == [main]
