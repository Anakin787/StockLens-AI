"""Who changed what, and when the system first noticed.

Nobody remembers to write an audit entry. So nothing here asks them to:
every run fingerprints the settings that decide what the engine may trade -
the universe, the strategy list, its parameters, the risk limits - compares
that against the fingerprint the last run stored, and records the difference.
A change made by hand in ``config.yaml`` shows up on the next run without the
person who made it doing anything at all.

Two honesty constraints shape the schema.

**``detected_at``, not ``changed_at``.** This can only know when a process
first saw the new value. Somebody who edits config.yaml on Saturday and runs
the report on Monday gets a Monday timestamp, and the field name has to say
so rather than implying a precision that does not exist.

**The actor is a guess for humans and a fact for the AI.** A veto is written
by the review that raised it, so ``ai`` is exact. A config edit is attributed
to the OS user of whichever process noticed - which is usually right on a
single-user machine and is never a security claim. This dashboard has no
authentication; an audit log here is a record for the person keeping it, not
evidence against anyone.
"""

import getpass
import socket
from datetime import datetime

#: Settings whose change alters what the engine is allowed to do. Anything
#: outside this list (Notion token, news keywords, display names) is not
#: audited - not because it does not matter, but because an audit log that
#: logs everything gets skimmed instead of read.
CATEGORIES = ("universe", "strategies", "strategy_params", "limits")

ACTOR_HUMAN = "human"
ACTOR_AI = "ai"


def _text(value):
    return None if value is None else str(value)


def local_actor():
    """``user@host`` for whoever is running this process, best effort."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name is not a failure
        user = "unknown"
    try:
        host = socket.gethostname()
    except Exception:  # noqa: BLE001
        host = "unknown"
    return f"{user}@{host}"


def fingerprint(trading_config, universe):
    """The audited settings, flattened to plain strings.

    Strings throughout so that a Decimal weight, an int read from YAML and
    the same value read back out of Firestore all compare equal - a diff that
    fires because ``0.35`` came back as ``Decimal('0.35')`` would train the
    reader to ignore this log.
    """
    instruments = {}
    for instrument in getattr(universe, "instruments", ()) or ():
        instruments[instrument.symbol] = {
            "kind": _text(instrument.kind),
            "max_weight": _text(instrument.max_weight),
            "leverage": _text(instrument.leverage),
            "enabled": _text(instrument.enabled),
        }

    return {
        "universe": instruments,
        "strategies": [_text(s) for s in getattr(trading_config, "strategies", []) or []],
        "strategy_params": {
            key: _text(value)
            for key, value in (getattr(trading_config, "strategy_params", {}) or {}).items()
        },
        "limits": {
            key: _text(value)
            for key, value in (getattr(trading_config, "limits", {}) or {}).items()
        },
    }


def _diff_mapping(before, after):
    """``[{target, before, after}]`` for two flat-ish mappings."""
    changes = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        changes.append({"target": key, "before": old, "after": new})
    return changes


def _diff_universe(before, after):
    changes = []
    for symbol in sorted(set(before) | set(after)):
        old, new = before.get(symbol), after.get(symbol)
        if old == new:
            continue
        if old is None:
            changes.append({"target": symbol, "before": None, "after": "추가"})
        elif new is None:
            changes.append({"target": symbol, "before": "보유", "after": "제거"})
        else:
            for field in sorted(set(old) | set(new)):
                if old.get(field) != new.get(field):
                    changes.append(
                        {
                            "target": f"{symbol}.{field}",
                            "before": old.get(field),
                            "after": new.get(field),
                        }
                    )
    return changes


def diff(before, after):
    """``{category: [changes]}`` for the categories that actually moved.

    An empty dict means nothing audited changed, which is the normal result
    of nearly every run.
    """
    before = before or {}
    result = {}
    for category in CATEGORIES:
        old = before.get(category)
        new = after.get(category)
        if category == "universe":
            changes = _diff_universe(old or {}, new or {})
        elif category == "strategies":
            changes = (
                []
                if (old or []) == (new or [])
                else [{"target": "strategies", "before": old, "after": new}]
            )
        else:
            changes = _diff_mapping(old or {}, new or {})
        # A first-ever run has no stored fingerprint at all. Recording the
        # whole universe as "added" on that run would be technically true and
        # completely useless, so no diff is produced - `record_config_changes`
        # writes one `baseline_entry` instead.
        if changes and before:
            result[category] = changes
    return result


def entries_from_diff(changes_by_category, actor=None, source="config.yaml", detected_at=None):
    """Turn a diff into audit entries, one per category."""
    actor = actor or local_actor()
    detected_at = detected_at or datetime.now().isoformat()
    entries = []
    for category, changes in changes_by_category.items():
        entries.append(
            {
                "detected_at": detected_at,
                "actor_kind": ACTOR_HUMAN,
                "actor": actor,
                "source": source,
                "category": category,
                "summary": _summarize(category, changes),
                "changes": changes,
            }
        )
    return entries


def _summarize(category, changes):
    if category == "universe":
        added = [c["target"] for c in changes if c.get("after") == "추가"]
        removed = [c["target"] for c in changes if c.get("after") == "제거"]
        edited = [c["target"] for c in changes if c.get("after") not in ("추가", "제거")]
        parts = []
        if added:
            parts.append(f"추가 {len(added)}종목 ({', '.join(added[:5])}{'…' if len(added) > 5 else ''})")
        if removed:
            parts.append(f"제거 {len(removed)}종목 ({', '.join(removed[:5])}{'…' if len(removed) > 5 else ''})")
        if edited:
            parts.append(f"설정 변경 {len(edited)}건")
        return " · ".join(parts)
    return f"{len(changes)}개 항목 변경: " + ", ".join(c["target"] for c in changes[:5])


def review_entries(review, actor="AI universe review", detected_at=None):
    """Audit entries for what an AI universe review did and proposed.

    Vetoes and candidates are recorded as separate entries because they are
    separate kinds of fact: one changed what the engine will do, the other is
    a suggestion nobody has acted on. Flattening them into one row would
    reproduce, in the audit log, exactly the confusion the report layout
    works to avoid.
    """
    detected_at = detected_at or datetime.now().isoformat()
    entries = []
    vetoes = getattr(review, "vetoes", ()) or ()
    candidates = getattr(review, "candidates", ()) or ()

    if vetoes:
        entries.append(
            {
                "detected_at": detected_at,
                "actor_kind": ACTOR_AI,
                "actor": actor,
                "source": "universe_review",
                "category": "veto",
                "summary": "신규 매수 보류: " + ", ".join(v.symbol for v in vetoes),
                "changes": [
                    {
                        "target": veto.symbol,
                        "before": None,
                        "after": f"[{veto.category}] {veto.reason}",
                        "evidence": veto.evidence,
                    }
                    for veto in vetoes
                ],
            }
        )
    if candidates:
        entries.append(
            {
                "detected_at": detected_at,
                "actor_kind": ACTOR_AI,
                "actor": actor,
                "source": "universe_review",
                "category": "candidate",
                "summary": "편입 후보 제안(미반영): "
                + ", ".join(c.symbol for c in candidates),
                "changes": [
                    {"target": c.symbol, "before": None, "after": c.reason}
                    for c in candidates
                ],
            }
        )
    return entries


def baseline_entry(settings, actor=None, source="config.yaml", detected_at=None):
    """The single entry a first-ever run writes instead of 39 "added" rows.

    Without it the page is empty until somebody happens to change something,
    which reads like the log is broken rather than like nothing has changed
    yet. One row saying what the starting point was fixes that and claims
    nothing untrue: this really is the first state the system ever saw.
    """
    universe = settings.get("universe") or {}
    strategies = settings.get("strategies") or []
    return {
        "detected_at": detected_at or datetime.now().isoformat(),
        "actor_kind": ACTOR_HUMAN,
        "actor": actor or local_actor(),
        "source": source,
        "category": "baseline",
        "summary": (
            f"감사 로그 시작 — 유니버스 {len(universe)}종목 · 전략 {len(strategies)}개 "
            "· 이후 변경분만 기록됩니다"
        ),
        "changes": [
            {"target": "universe", "before": None, "after": f"{len(universe)}종목"},
            {"target": "strategies", "before": None, "after": ", ".join(strategies) or "—"},
        ],
    }


def record_config_changes(store, trading_config, universe, actor=None, source="config.yaml"):
    """Compare settings against the stored fingerprint and log what moved.

    Returns the entries written (empty on an unchanged run). Never raises:
    an audit log that can stop the daily report from running would be traded
    away for the report on the first failure, and then there would be neither.
    """
    try:
        current = fingerprint(trading_config, universe)
        previous = store.audit_fingerprint()
        if previous is None:
            entries = [baseline_entry(current, actor=actor, source=source)]
        else:
            entries = entries_from_diff(
                diff(previous, current), actor=actor, source=source
            )
        if entries:
            store.save_audit_entries(entries)
        if previous != current:
            store.save_audit_fingerprint(current)
        return entries
    except Exception as exc:  # noqa: BLE001 - never fatal
        print(f"경고: 감사 로그를 기록하지 못했습니다 ({exc})")
        return []
