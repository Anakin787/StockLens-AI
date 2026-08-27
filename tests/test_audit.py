"""Change detection: what counts as a change, and what is deliberately silent."""

from decimal import Decimal

from src.audit import (
    ACTOR_AI,
    ACTOR_HUMAN,
    diff,
    entries_from_diff,
    fingerprint,
    record_config_changes,
    review_entries,
)
from src.config import TradingConfig
from src.strategy.universe import Instrument, Universe
from src.universe_review import Candidate, UniverseReview, Veto


def universe_of(*symbols, max_weight="0.35"):
    return Universe(
        tuple(
            Instrument(symbol=s, name=s, max_weight=Decimal(max_weight)) for s in symbols
        )
    )


def trading(**overrides):
    base = dict(
        strategies=["src.strategy.momentum_dca:MomentumDcaStrategy"],
        strategy_params={"top_n": 2},
        limits={"max_orders_per_day": 10},
    )
    base.update(overrides)
    return TradingConfig(**base)


class FakeStore:
    """Just the four audit methods, so these tests need no emulator."""

    def __init__(self, stored=None):
        self.stored = stored
        self.entries = []

    def audit_fingerprint(self):
        return self.stored

    def save_audit_fingerprint(self, settings, ts=None):
        self.stored = settings

    def save_audit_entries(self, entries):
        self.entries.extend(entries)
        return len(entries)


# ----------------------------------------------------------- fingerprinting


def test_the_fingerprint_is_all_strings_so_decimals_compare_equal():
    printed = fingerprint(trading(), universe_of("AAPL"))

    assert printed["universe"]["AAPL"]["max_weight"] == "0.35"
    assert printed["strategy_params"]["top_n"] == "2"
    # A round trip through storage must not look like a change.
    assert diff(printed, fingerprint(trading(), universe_of("AAPL"))) == {}


# -------------------------------------------------------------------- diff


def test_an_added_symbol_is_reported():
    before = fingerprint(trading(), universe_of("AAPL"))
    after = fingerprint(trading(), universe_of("AAPL", "MSFT"))

    changes = diff(before, after)["universe"]
    assert changes == [{"target": "MSFT", "before": None, "after": "추가"}]


def test_a_removed_symbol_is_reported():
    before = fingerprint(trading(), universe_of("AAPL", "MSFT"))
    after = fingerprint(trading(), universe_of("AAPL"))

    assert diff(before, after)["universe"][0]["after"] == "제거"


def test_a_changed_weight_names_the_field_not_just_the_symbol():
    before = fingerprint(trading(), universe_of("AAPL", max_weight="0.35"))
    after = fingerprint(trading(), universe_of("AAPL", max_weight="0.40"))

    change = diff(before, after)["universe"][0]
    assert change["target"] == "AAPL.max_weight"
    assert (change["before"], change["after"]) == ("0.35", "0.40")


def test_a_parameter_change_is_reported_with_both_values():
    before = fingerprint(trading(), universe_of("AAPL"))
    after = fingerprint(trading(strategy_params={"top_n": 3}), universe_of("AAPL"))

    assert diff(before, after)["strategy_params"] == [
        {"target": "top_n", "before": "2", "after": "3"}
    ]


def test_a_risk_limit_change_is_reported():
    before = fingerprint(trading(), universe_of("AAPL"))
    after = fingerprint(trading(limits={"max_orders_per_day": 4}), universe_of("AAPL"))

    assert diff(before, after)["limits"][0]["after"] == "4"


def test_an_unchanged_run_reports_nothing():
    printed = fingerprint(trading(), universe_of("AAPL"))
    assert diff(printed, printed) == {}


def test_the_first_ever_run_records_a_baseline_silently():
    # 39 symbols "added" on the first run would be true and useless.
    after = fingerprint(trading(), universe_of("AAPL", "MSFT"))
    assert diff(None, after) == {}


# ----------------------------------------------------------------- entries


def test_entries_carry_the_actor_and_a_readable_summary():
    changes = diff(
        fingerprint(trading(), universe_of("AAPL")),
        fingerprint(trading(), universe_of("AAPL", "MSFT")),
    )
    entry = entries_from_diff(changes, trading(), actor="jiun@box")[0]

    assert entry["actor_kind"] == ACTOR_HUMAN
    assert entry["actor"] == "jiun@box"
    assert "MSFT" in entry["summary"]
    # Both timestamps: when the setting changed, and when we noticed.
    assert entry["detected_at"]
    assert entry["changed_by_method"] in ("git", "mtime", None)


def test_a_review_logs_vetoes_and_candidates_as_separate_entries():
    review = UniverseReview(
        vetoes=(Veto(symbol="INTC", category="trading_halt", reason="정지", evidence="h"),),
        candidates=(Candidate(symbol="ASML", name="ASML", reason="장비"),),
    )
    entries = review_entries(review)

    assert [e["category"] for e in entries] == ["veto", "candidate"]
    assert all(e["actor_kind"] == ACTOR_AI for e in entries)
    assert entries[0]["changes"][0]["evidence"] == "h"
    # The proposal must not read as something that happened.
    assert "미반영" in entries[1]["summary"]


def test_an_empty_review_logs_nothing():
    assert review_entries(UniverseReview()) == []


# ------------------------------------------------------------- record flow


def test_a_first_run_writes_one_baseline_row_not_one_per_symbol():
    store = FakeStore(stored=None)
    written = record_config_changes(store, trading(), universe_of("AAPL", "MSFT"))

    assert [e["category"] for e in written] == ["baseline"]
    assert "2종목" in written[0]["summary"]
    assert store.stored is not None


def test_recording_writes_one_entry_per_category_that_moved():
    store = FakeStore(stored=fingerprint(trading(), universe_of("AAPL")))
    written = record_config_changes(
        store, trading(strategy_params={"top_n": 3}), universe_of("AAPL", "MSFT")
    )

    assert sorted(e["category"] for e in written) == ["strategy_params", "universe"]
    assert len(store.entries) == 2


def test_a_second_run_with_no_edit_writes_nothing():
    store = FakeStore(stored=fingerprint(trading(), universe_of("AAPL")))
    record_config_changes(store, trading(), universe_of("AAPL"))

    assert store.entries == []


def test_a_broken_store_does_not_stop_the_caller():
    class Exploding(FakeStore):
        def audit_fingerprint(self):
            raise RuntimeError("firestore down")

    # The report must still run; an audit log that can kill the daily job
    # gets deleted the first time it does.
    assert record_config_changes(Exploding(), trading(), universe_of("AAPL")) == []


# ------------------------------------------------------- when it changed


def test_a_config_backed_category_is_dated_from_the_file():
    entry = entries_from_diff(
        {"limits": [{"target": "max_orders_per_day", "before": "10", "after": "4"}]},
        trading(),
    )[0]

    # config.yaml is untracked on purpose (it holds secrets), so git cannot
    # date it and mtime is the honest fallback.
    assert entry["source"] == "config.yaml"
    assert entry["changed_by_method"] == "mtime"
    assert entry["changed_at"]


def test_a_code_backed_universe_is_dated_from_git_with_a_real_author():
    # An empty trading.universe means DEFAULT_UNIVERSE, which is source code -
    # tracked, so the commit gives an exact time and a real name.
    entry = entries_from_diff(
        {"universe": [{"target": "MSFT", "before": None, "after": "추가"}]},
        trading(universe=[]),
    )[0]

    assert entry["source"] == "src/strategy/universe.py"
    assert entry["changed_by_method"] in ("git", "mtime")
    if entry["changed_by_method"] == "git":
        assert entry["actor"] and "@" not in entry["actor"]  # commit author, not user@host


def test_a_universe_declared_in_config_is_dated_from_config():
    entry = entries_from_diff(
        {"universe": [{"target": "MSFT", "before": None, "after": "추가"}]},
        trading(universe=[{"symbol": "MSFT"}]),
    )[0]

    assert entry["source"] == "config.yaml"


def test_an_unreadable_source_still_produces_an_entry():
    from src.audit import change_origin

    origin = change_origin("no/such/file.yaml")

    # detected_at alone still carries the entry; changed_at is simply absent
    # rather than invented.
    assert origin["changed_at"] is None
    assert origin["changed_by_method"] is None


def test_an_ai_entry_needs_no_recovery_because_it_just_happened():
    review = UniverseReview(
        vetoes=(Veto(symbol="INTC", category="trading_halt", reason="정지", evidence="h"),),
    )
    entry = review_entries(review, detected_at="2026-08-27T10:00:00")[0]

    assert entry["changed_at"] == entry["detected_at"]
    assert entry["changed_by_method"] == "direct"
