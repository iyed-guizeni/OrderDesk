"""Tests for parsing review CLI changes without running the script."""

import pytest

from orderdesk.services.review import parse_changes


def test_code_and_quantity_merge_for_same_line() -> None:
    assert parse_changes(["1=PF60376"], ["1=3"], []) == [
        {"line_index": 1, "code": "PF60376", "quantity": 3.0}
    ]


def test_remove_maps_to_review_line_change() -> None:
    assert parse_changes([], [], ["2"]) == [{"line_index": 2, "remove": True}]


@pytest.mark.parametrize(
    ("codes", "qtys", "removes"),
    [
        (["x=Y"], [], []),
        ([], ["1=abc"], []),
        (["1="], [], []),
        ([], [], ["x"]),
    ],
)
def test_bad_changes_raise_clear_value_error(codes: list[str], qtys: list[str], removes: list[str]) -> None:
    with pytest.raises(ValueError):
        parse_changes(codes, qtys, removes)
