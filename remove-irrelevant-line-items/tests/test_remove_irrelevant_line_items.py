import sys
from types import ModuleType, SimpleNamespace

import pytest

from remove_irrelevant_line_items import (
    find_matching_prefix_length,
    find_matching_prefix_length_for_targets,
    rossum_hook_request_handler,
)

MISSING = object()


class FakeField:
    def __init__(self, headers=None, amounts=()):
        for field_id, value in (headers or {}).items():
            if value is not MISSING:
                setattr(self, field_id, value)
        self.line_items = [
            SimpleNamespace(id=index + 1, item_amount_total=amount)
            for index, amount in enumerate(amounts)
        ]


class FakeTransaction:
    def __init__(self, amount_due=MISSING, amounts=(), headers=None):
        if headers is None:
            headers = {"amount_due": amount_due}
        self.field = FakeField(headers, amounts)
        self._original_rows = list(self.field.line_items)

    def hook_response(self):
        retained_ids = {row.id for row in self.field.line_items}
        operations = [
            {"op": "remove", "id": row.id}
            for row in self._original_rows
            if row.id not in retained_ids
        ]
        return {"messages": [], "operations": operations}


@pytest.fixture(autouse=True)
def fake_txscript(monkeypatch):
    module = ModuleType("txscript")

    class FakeTxScript:
        @staticmethod
        def from_payload(payload):
            return payload["transaction"]

    module.TxScript = FakeTxScript
    module.is_empty = lambda value: value is None or value == ""
    monkeypatch.setitem(sys.modules, "txscript", module)


def run_handler(amount_due, amounts, *, settings=MISSING, headers=None):
    transaction = FakeTransaction(amount_due, amounts, headers)
    payload = {"transaction": transaction}
    if settings is not MISSING:
        payload["settings"] = settings
    response = rossum_hook_request_handler(payload)
    remaining = [row.item_amount_total for row in transaction.field.line_items]
    return transaction, response, remaining


def test_first_row_matches_and_all_following_rows_are_removed():
    _, response, remaining = run_handler("10.00", [10, 20, 30])

    assert remaining == [10]
    assert response == {
        "messages": [],
        "operations": [{"op": "remove", "id": 2}, {"op": "remove", "id": 3}],
    }


def test_several_leading_rows_reach_amount_due():
    _, response, remaining = run_handler(60, [10, 20, 30, 999])

    assert remaining == [10, 20, 30]
    assert response["operations"] == [{"op": "remove", "id": 4}]


def test_decimal_values_are_compared_at_two_decimal_precision():
    assert find_matching_prefix_length([0.1, 0.2, 5], 0.3) == 2


def test_first_prefix_matching_any_target_wins():
    assert find_matching_prefix_length_for_targets(
        [10, 20, 30, 999], [60, 30]
    ) == 2


@pytest.mark.parametrize(
    ("amount_due", "amounts"),
    [
        (25, [10, 20, 30]),
        (100, [20, 30]),
    ],
)
def test_no_exact_match_preserves_all_rows_silently(amount_due, amounts):
    _, response, remaining = run_handler(amount_due, amounts)

    assert remaining == amounts
    assert response == {"messages": [], "operations": []}


@pytest.mark.parametrize(
    ("amount_due", "amounts"),
    [
        (None, [10, 20]),
        ("", [10, 20]),
        (10, []),
        (10, [None, 10]),
        (10, ["invalid", 10]),
    ],
)
def test_empty_or_invalid_input_preserves_all_rows(amount_due, amounts):
    _, response, remaining = run_handler(amount_due, amounts)

    assert remaining == amounts
    assert response == {"messages": [], "operations": []}


def test_missing_amount_due_field_preserves_all_rows():
    transaction = FakeTransaction(MISSING, [10, 20])

    response = rossum_hook_request_handler({"transaction": transaction})

    assert [row.item_amount_total for row in transaction.field.line_items] == [10, 20]
    assert response == {"messages": [], "operations": []}


def test_empty_settings_keep_the_default_amount_due_target():
    _, response, remaining = run_handler(30, [10, 20, 999], settings={})

    assert remaining == [10, 20]
    assert response["operations"] == [{"op": "remove", "id": 3}]


def test_configured_subtotal_can_be_used_instead_of_amount_due():
    _, response, remaining = run_handler(
        MISSING,
        [10, 20, 999],
        settings={"target_field_ids": ["subtotal"]},
        headers={"subtotal": 30},
    )

    assert remaining == [10, 20]
    assert response["operations"] == [{"op": "remove", "id": 3}]


def test_amount_due_and_subtotal_are_both_checked():
    _, response, remaining = run_handler(
        MISSING,
        [10, 20, 30, 999],
        settings={"target_field_ids": ["amount_due", "subtotal"]},
        headers={"amount_due": 60, "subtotal": 30},
    )

    assert remaining == [10, 20]
    assert response["operations"] == [
        {"op": "remove", "id": 3},
        {"op": "remove", "id": 4},
    ]


def test_missing_or_empty_configured_target_is_ignored():
    _, response, remaining = run_handler(
        MISSING,
        [10, 20, 999],
        settings={"target_field_ids": ["missing_total", "subtotal"]},
        headers={"subtotal": 30, "missing_total": ""},
    )

    assert remaining == [10, 20]
    assert response["operations"] == [{"op": "remove", "id": 3}]


@pytest.mark.parametrize(
    "settings",
    [
        {"target_field_ids": []},
        {"target_field_ids": "subtotal"},
        {"target_field_ids": ["", None]},
        "invalid",
    ],
)
def test_invalid_target_field_configuration_preserves_all_rows(settings):
    _, response, remaining = run_handler(
        MISSING,
        [10, 20, 999],
        settings=settings,
        headers={"subtotal": 30},
    )

    assert remaining == [10, 20, 999]
    assert response == {"messages": [], "operations": []}


def test_match_on_final_row_returns_no_removals():
    _, response, remaining = run_handler(60, [10, 20, 30])

    assert remaining == [10, 20, 30]
    assert response == {"messages": [], "operations": []}


def test_negative_discount_row_participates_in_total():
    _, response, remaining = run_handler(100, [120, -20, 50])

    assert remaining == [120, -20]
    assert response["operations"] == [{"op": "remove", "id": 3}]


def test_first_exact_match_wins_before_zero_value_rows():
    _, response, remaining = run_handler(10, [10, 0, 25])

    assert remaining == [10]
    assert response["operations"] == [
        {"op": "remove", "id": 2},
        {"op": "remove", "id": 3},
    ]


def test_invalid_trailing_row_is_removed_after_an_exact_match():
    _, response, remaining = run_handler(10, [10, None, 25])

    assert remaining == [10]
    assert response["operations"] == [
        {"op": "remove", "id": 2},
        {"op": "remove", "id": 3},
    ]


def test_handler_is_idempotent_after_cleanup():
    transaction, first_response, _ = run_handler(30, [10, 20, 99])
    transaction._original_rows = list(transaction.field.line_items)

    second_response = rossum_hook_request_handler({"transaction": transaction})

    assert first_response["operations"] == [{"op": "remove", "id": 3}]
    assert second_response == {"messages": [], "operations": []}
