"""Deterministic client_order_id."""

import pytest

from src.execution.ids import make_client_order_id


def test_same_inputs_give_the_same_id():
    # The whole point: a re-run derives the id Toss has already seen rather
    # than minting a new one and placing a second order.
    first = make_client_order_id("buy-dips", "005930", "2026-08-26", 1)
    second = make_client_order_id("buy-dips", "005930", "2026-08-26", 1)
    assert first == second == "buy_dips-005930-2026-08-26-1"


def test_different_seq_gives_a_different_id():
    a = make_client_order_id("s", "005930", "2026-08-26", 1)
    b = make_client_order_id("s", "005930", "2026-08-26", 2)
    assert a != b


def test_separator_in_a_name_cannot_shift_the_fields():
    # "a-b" + "c" must not collide with "a" + "b-c".
    a = make_client_order_id("a-b", "c", "2026-08-26", 1)
    b = make_client_order_id("a", "b-c", "2026-08-26", 1)
    assert a != b
    # 3 separators + the 2 inside the date; the hyphen inside a name is gone.
    assert a.count("-") == b.count("-") == 5


def test_iso_timestamp_is_truncated_to_a_date():
    assert make_client_order_id("s", "X", "2026-08-26T09:30:00", 1).endswith(
        "2026-08-26-1"
    )


def test_seq_must_be_a_positive_integer():
    with pytest.raises(ValueError):
        make_client_order_id("s", "X", "2026-08-26", 0)
    with pytest.raises(ValueError):
        make_client_order_id("s", "X", "2026-08-26", "later")


def test_blank_parts_fall_back_rather_than_collapsing():
    generated = make_client_order_id("", "", "2026-08-26", 1)
    assert generated == "strategy-symbol-2026-08-26-1"
