"""Calendar parsing, including the close time the risk gate needs."""

from datetime import datetime, timedelta, timezone

from src.toss.calendar import live_session, parse_dt, regular_window

KST = timezone(timedelta(hours=9))


def window(start, end):
    return {"startTime": start, "endTime": end}


CALENDAR = {
    "integrated": {
        "regularMarket": window("2026-08-26T09:00:00+09:00", "2026-08-26T15:30:00+09:00"),
        "afterMarket": window("2026-08-26T15:30:00+09:00", "2026-08-26T18:00:00+09:00"),
    }
}


def test_regular_window_reaches_into_a_nested_calendar():
    start, end = regular_window(CALENDAR)
    assert start == datetime(2026, 8, 26, 9, 0, tzinfo=KST)
    assert end == datetime(2026, 8, 26, 15, 30, tzinfo=KST)


def test_regular_window_picks_the_later_of_several_sessions():
    # A calendar can carry the previous business day alongside today; the one
    # still ahead of us is the one that ends latest.
    calendar = {
        "previousBusinessDay": {
            "regularMarket": window(
                "2026-08-25T09:00:00+09:00", "2026-08-25T15:30:00+09:00"
            )
        },
        "today": CALENDAR["integrated"],
    }
    _, end = regular_window(calendar)
    assert end.day == 26


def test_regular_window_is_empty_when_unknown():
    assert regular_window(None) == (None, None)
    assert regular_window({"preMarket": window("a", "b")}) == (None, None)


def test_live_session_prefers_the_regular_session_at_a_boundary():
    # 15:30 sits in both regularMarket and afterMarket; the significant one
    # has to win or the gate would think the main session had ended.
    now = datetime(2026, 8, 26, 15, 30, tzinfo=KST)
    assert live_session(CALENDAR, now) == "regular"


def test_live_session_is_none_outside_every_window():
    assert live_session(CALENDAR, datetime(2026, 8, 26, 20, 0, tzinfo=KST)) is None


def test_parse_dt_always_returns_an_aware_datetime():
    # A naive close time compared against an aware now raises TypeError, so
    # this is the property that keeps the risk gate's cutoff working.
    assert parse_dt("2026-08-26T09:00:00").tzinfo is not None
    assert parse_dt("2026-08-26T09:00:00+09:00").tzinfo is not None
    assert parse_dt("not a date") is None
    assert parse_dt(None) is None
