"""cursor pagination and cli errors."""

from unittest.mock import patch

import click
import pytest
import requests

from runpod import error
from runpod.rp_cli import api


class TestApi:
    @pytest.mark.parametrize(
        "failure",
        [error.AuthenticationError("unauthorized"), requests.Timeout("timed out")],
    )
    def test_api_errors_are_click_errors(self, failure):
        with pytest.raises(click.ClickException, match=str(failure)):
            with api.api_errors():
                raise failure

    def test_list_all_follows_cursors(self):
        pages = [
            {
                "pods": [{"id": "a"}],
                "pagination": {"hasNextPage": True, "nextCursor": "c1"},
            },
            {
                "pods": [{"id": "b"}],
                "pagination": {"hasNextPage": False, "nextCursor": None},
            },
        ]
        with patch.object(api, "run_rest_request", side_effect=pages) as request:
            assert api.list_all("/v2/pods", "pods", {"limit": 1}) == [
                {"id": "a"},
                {"id": "b"},
            ]
        assert request.call_args_list[1].kwargs["params"] == {
            "limit": 1,
            "cursor": "c1",
        }
