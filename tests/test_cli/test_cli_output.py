"""shared cli rendering and terminal interaction."""

from unittest.mock import patch

import click
import pytest

from runpod import error
from runpod.rp_cli import output


class TestOutputHelpers:
    def test_cell(self):
        assert output.cell(None) == "-"
        assert output.cell(True) == "yes"
        assert output.cell(["a", "b"]) == "a, b"
        assert output.cell(3) == "3"

    def test_print_table_keeps_ids_and_values_whole(self, monkeypatch, capsys):
        from runpod.rp_cli import console as ui

        monkeypatch.setattr(ui.console, "_width", 40)
        rows = [{"id": "cmvfsn771000007jp15tn8esm", "type": "high_performance"}]
        output.print_table(
            rows,
            [
                ("ID", lambda r: r["id"]),
                ("TYPE", lambda r: r["type"]),
                ("PAD", lambda r: "x" * 30),
            ],
            "none",
        )
        out = capsys.readouterr().out
        assert "cmvfsn771000007jp15tn8esm" in out
        assert "…" not in out

    def test_print_fields(self, capsys):
        output.print_fields(
            {"id": "abc", "tags": []},
            [("id", lambda r: r["id"]), ("tags", lambda r: r["tags"])],
        )
        out = capsys.readouterr().out
        assert "id    abc" in out
        assert "tags  -" in out

    def test_pick_requires_tty(self):
        with patch.object(output, "interactive", return_value=False):
            with pytest.raises(click.UsageError, match="pass an ID"):
                output.pick("pick", [("a", "a")], "pass an ID")

    def test_pick_with_no_choices(self):
        with patch.object(output, "interactive", return_value=True):
            with pytest.raises(click.UsageError, match="none found"):
                output.pick("pick", [], "pass an ID")

    def test_pick_uses_fuzzy_prompt(self):
        with (
            patch.object(output, "interactive", return_value=True),
            patch("InquirerPy.inquirer.fuzzy") as fuzzy,
        ):
            fuzzy.return_value.execute.return_value = "b"
            assert output.pick("pick", [("a", "first"), ("b", "second")], "x") == "b"
        choices = fuzzy.call_args.kwargs["choices"]
        assert [(choice.value, choice.name) for choice in choices] == [
            ("a", "first"),
            ("b", "second"),
        ]

    def test_confirm(self):
        output.confirm("sure?", yes=True)
        with patch.object(output, "interactive", return_value=False):
            with pytest.raises(click.UsageError, match="--yes"):
                output.confirm("sure?", yes=False)
        with (
            patch.object(output, "interactive", return_value=True),
            patch("click.confirm") as ask,
        ):
            output.confirm("sure?", yes=False)
        ask.assert_called_once_with("sure?", abort=True)

    def test_completer(self):
        complete = output.completer(lambda: [("abc", "first"), ("xyz", "second")])
        items = complete(None, None, "a")
        assert [(item.value, item.help) for item in items] == [("abc", "first")]

    def test_completer_swallows_errors(self):
        def broken():
            raise error.AuthenticationError("no key")

        assert output.completer(broken)(None, None, "") == []
