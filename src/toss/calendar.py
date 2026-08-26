"""Reading the market calendar payload.

Extracted from the dashboard, where this first appeared, because the risk
gate needs the same answers: the calendar shape varies by market and nests
sessions arbitrarily deep (KR wraps them in "integrated"; a US session can
start on previousBusinessDay and still be live now in KST), so everything
here recurses through the whole tree rather than assuming a layout.

Every datetime returned is timezone-aware. Mixing an aware close time with a
naive ``now`` raises TypeError at the comparison, so callers building a
StrategyContext must use an aware clock too.
"""

from datetime import datetime

#: Calendar keys that denote a tradable session, mapped to the kind reported
#: to callers. Ordered by precedence: the windows overlap at the boundary (KR
#: afterMarket starts the minute regularMarket ends), and the more significant
#: session should win.
SESSION_KINDS = (
    ("regularMarket", "regular"),
    ("dayMarket", "day"),
    ("preMarket", "pre"),
    ("afterMarket", "after"),
)

REGULAR_KEY = "regularMarket"


def parse_dt(value):
    """Parse an ISO8601 string into an aware datetime, or None."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def session_window(session):
    """The (start, end) of one session node, under any of its key spellings."""
    start = parse_dt(
        session.get("startTime") or session.get("startDateTime") or session.get("start")
    )
    end = parse_dt(
        session.get("endTime") or session.get("endDateTime") or session.get("end")
    )
    return start, end


def contains(session, now):
    """True when ``now`` falls inside this session's window."""
    start, end = session_window(session)
    return bool(start and end and start <= now <= end)


def _walk(calendar, visit):
    """Call ``visit(key, node)`` for every dict-valued key in the tree."""
    def scan(node):
        if isinstance(node, list):
            for item in node:
                scan(item)
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if isinstance(value, dict):
                visit(key, value)
            scan(value)

    scan(calendar)


def live_session(calendar, now=None):
    """Which session is live right now: regular/day/pre/after, or None."""
    if not calendar:
        return None

    now = now or datetime.now().astimezone()
    found = set()
    kinds = dict(SESSION_KINDS)

    def visit(key, node):
        kind = kinds.get(key)
        if kind and contains(node, now):
            found.add(kind)

    _walk(calendar, visit)

    for _, kind in SESSION_KINDS:
        if kind in found:
            return kind
    return None


def regular_window(calendar):
    """The regular session's (start, end), or (None, None).

    The risk gate needs the close specifically: amount orders and fractional
    quantities stop being accepted an hour before it while the market is
    still open, so knowing only *that* a session is live is not enough.

    A calendar can carry several regularMarket nodes (previous business day,
    today). The one that ends latest is the one still ahead of us.
    """
    if not calendar:
        return None, None

    windows = []

    def visit(key, node):
        if key != REGULAR_KEY:
            return
        start, end = session_window(node)
        if start and end:
            windows.append((start, end))

    _walk(calendar, visit)

    if not windows:
        return None, None
    return max(windows, key=lambda window: window[1])
