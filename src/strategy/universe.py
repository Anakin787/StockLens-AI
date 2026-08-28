"""What this strategy is allowed to buy, and what it is never allowed to buy.

The leverage policy lives here as executable rules rather than as a list of
tickers someone remembered to keep clean. Two products are refused outright:

*Above 2x.* A daily-reset product's volatility drag grows with the square of
its multiple, so a 3x fund does not lose 1.5x what a 2x fund loses in a choppy
market - it loses more than twice as much, and the gap widens the longer the
chop lasts. Paired with monthly contributions, that produces a position which
keeps receiving new money on the way into a hole it cannot climb out of.

*Leveraged single-stock funds.* A 2x index fund is levered exposure to a
diversified basket; a 2x single-stock fund is levered exposure to one earnings
call. The index's worst day is bounded by the fact that its members do not all
gap 20% together. One company's is not.

Both rules are enforced in ``Instrument.__post_init__``, which means an entry
added to config cannot violate them quietly - a bad row fails at load, loudly,
in the same way ``src/config.py`` treats an unknown key.
"""

from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation

from src.models import ZERO
from src.toss.errors import TossConfigError

ONE = Decimal("1")

#: The hard ceiling. Not a default - there is no config key that raises it.
MAX_LEVERAGE = Decimal("2")

#: Which part of the portfolio a holding belongs to. The strategy allocates a
#: target share of equity to each bucket and fills it independently, so a bad
#: year in one does not quietly redirect money into another.
#:
#: SAFE is not ranked - it is held because it is meant to be there when the
#: rest falls, and ranking it by momentum would sell it in exactly the weeks
#: it exists for. CORE and GROWTH are ranked, separately, so a speculative
#: name never competes for a core slot on a hot quarter.
BUCKET_SAFE = "SAFE"
BUCKET_CORE = "CORE"
BUCKET_GROWTH = "GROWTH"
BUCKETS = (BUCKET_SAFE, BUCKET_CORE, BUCKET_GROWTH)

KIND_STOCK = "STOCK"
KIND_INDEX_ETF = "INDEX_ETF"
KIND_SINGLE_STOCK_ETF = "SINGLE_STOCK_ETF"
KINDS = frozenset({KIND_STOCK, KIND_INDEX_ETF, KIND_SINGLE_STOCK_ETF})


class UniverseError(ValueError):
    """An instrument the leverage policy refuses to hold."""


def _decimal(value, field_name, symbol):
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise UniverseError(
            f"{symbol}의 '{field_name}' 값을 숫자로 읽을 수 없습니다: {value!r}"
        ) from None


@dataclass(frozen=True)
class Instrument:
    """One tradable security, with the strategy's own cap on it.

    ``max_weight`` is the *strategy's* opinion, deliberately tighter than the
    risk gate's. The gate is a backstop against a bug; this is the plan.
    """

    symbol: str
    name: str
    kind: str = KIND_STOCK
    currency: str = "USD"
    country: str = "US"
    leverage: Decimal = ONE

    #: The single security this product tracks, if any. Set for NVDL/TSLL-class
    #: funds; None for QQQ/SOXX/QLD, which track a basket.
    underlying: str | None = None

    max_weight: Decimal = Decimal("0.25")
    enabled: bool = True

    #: Which bucket's target weight this instrument competes for.
    bucket: str = BUCKET_CORE

    def __post_init__(self):
        if not self.symbol:
            raise UniverseError("symbol이 비어 있습니다.")
        if self.bucket not in BUCKETS:
            raise UniverseError(
                f"{self.symbol}: bucket은 {sorted(BUCKETS)} 중 하나여야 합니다: "
                f"{self.bucket!r}"
            )
        if self.kind not in KINDS:
            raise UniverseError(
                f"{self.symbol}: kind는 {sorted(KINDS)} 중 하나여야 합니다: {self.kind!r}"
            )

        object.__setattr__(
            self, "leverage", _decimal(self.leverage, "leverage", self.symbol)
        )
        object.__setattr__(
            self, "max_weight", _decimal(self.max_weight, "max_weight", self.symbol)
        )

        if self.leverage > MAX_LEVERAGE:
            raise UniverseError(
                f"{self.symbol}: {self.leverage}배 상품은 허용하지 않습니다 "
                f"(상한 {MAX_LEVERAGE}배). 일일 리셋 상품의 변동성 손실은 배수의 "
                "제곱에 비례해 커지고, 적립식 매수와 결합하면 회복 불가능한 "
                "구간이 생깁니다."
            )
        if self.leverage < ONE:
            raise UniverseError(
                f"{self.symbol}: leverage는 1 이상이어야 합니다 "
                f"(인버스·부분배수 미지원): {self.leverage}"
            )
        if self.leverage > ONE and (
            self.kind == KIND_SINGLE_STOCK_ETF or self.underlying
        ):
            raise UniverseError(
                f"{self.symbol}: 개별종목 레버리지 ETF는 허용하지 않습니다 "
                f"(기초자산 {self.underlying or '단일종목'}). 지수와 달리 분산이 "
                "없어 단일 종목의 갭다운이 그대로 배수로 실현됩니다."
            )

        if not (ZERO < self.max_weight <= ONE):
            raise UniverseError(
                f"{self.symbol}: max_weight는 0 초과 1 이하여야 합니다: {self.max_weight}"
            )

    @property
    def is_leveraged(self):
        return self.leverage > ONE


@dataclass(frozen=True)
class Universe:
    """The instruments a strategy ranks over, keyed by symbol."""

    instruments: tuple = ()

    def __post_init__(self):
        seen = {}
        for instrument in self.instruments:
            if instrument.symbol in seen:
                raise UniverseError(f"유니버스에 중복된 심볼이 있습니다: {instrument.symbol}")
            seen[instrument.symbol] = instrument
        object.__setattr__(self, "instruments", tuple(self.instruments))
        object.__setattr__(self, "_by_symbol", seen)

    def __len__(self):
        return len(self.instruments)

    def __iter__(self):
        return iter(self.instruments)

    def __contains__(self, symbol):
        return symbol in self._by_symbol

    def get(self, symbol):
        return self._by_symbol.get(symbol)

    def __getitem__(self, symbol):
        instrument = self.get(symbol)
        if instrument is None:
            raise UniverseError(f"유니버스에 없는 심볼입니다: {symbol}")
        return instrument

    def enabled(self):
        return tuple(i for i in self.instruments if i.enabled)

    def symbols(self):
        """Enabled symbols, in declaration order."""
        return tuple(i.symbol for i in self.enabled())

    def by_bucket(self, bucket, allow_leverage=True):
        """Enabled instruments in one bucket, in declaration order."""
        return tuple(
            i
            for i in self.tradable(allow_leverage=allow_leverage)
            if i.bucket == bucket
        )

    def tradable(self, allow_leverage=True):
        """Enabled instruments, optionally with the leveraged ones removed.

        The strategy calls this with ``allow_leverage=False`` when the trend
        filter is down, which is cheaper and clearer than filtering the ranked
        table afterwards.
        """
        picked = self.enabled()
        if not allow_leverage:
            picked = tuple(i for i in picked if not i.is_leveraged)
        return picked

    def audit(self, snapshot):
        """Held symbols that this universe does not cover.

        Existing holdings are not errors - the account predates the strategy,
        and some of it (a 2x single-stock fund, say) is exactly what the policy
        above would now refuse to buy. The strategy will never add to them; the
        report can show them as unmanaged rather than pretending they are not
        there.
        """
        outside = []
        for position in getattr(snapshot, "positions", []) or []:
            if position.symbol and position.symbol not in self:
                outside.append(position.symbol)
        return tuple(outside)


# --- config ---------------------------------------------------------------

_INSTRUMENT_FIELDS = frozenset(
    {
        "symbol",
        "name",
        "kind",
        "currency",
        "country",
        "leverage",
        "underlying",
        "max_weight",
        "enabled",
        "bucket",
    }
)


def parse_universe(rows):
    """Build a Universe from config rows, refusing anything unrecognised.

    An unknown key is an error rather than a shrug: a row carrying
    ``leverage_factor: 3`` instead of ``leverage: 3`` reads, to a person, like
    an enforced limit, and silently ignoring it would make the policy above a
    comment.
    """
    if not rows:
        return DEFAULT_UNIVERSE

    instruments = []
    for row in rows:
        if not isinstance(row, dict):
            raise TossConfigError(f"universe 항목은 매핑이어야 합니다: {row!r}")
        unknown = set(row) - _INSTRUMENT_FIELDS
        if unknown:
            raise TossConfigError(
                f"universe 항목에 알 수 없는 키가 있습니다: {sorted(unknown)} "
                f"(허용: {sorted(_INSTRUMENT_FIELDS)})"
            )
        symbol = row.get("symbol")
        if not symbol:
            raise TossConfigError(f"universe 항목에 symbol이 없습니다: {row!r}")
        data = dict(row)
        data.setdefault("name", symbol)
        try:
            instruments.append(Instrument(**data))
        except UniverseError as exc:
            raise TossConfigError(f"universe 설정 오류: {exc}") from exc

    return Universe(tuple(instruments))


def _stock(symbol, name, max_weight="0.35"):
    return Instrument(
        symbol=symbol,
        name=name,
        kind=KIND_STOCK,
        max_weight=Decimal(max_weight),
    )


def _safe(symbol, name, max_weight="0.15"):
    """A holding whose job is to not fall with the rest.

    Bought and held to a fixed target share, never ranked. Momentum has
    nothing useful to say about an asset held for its behaviour in the weeks
    momentum is wrong.
    """
    return Instrument(
        symbol=symbol,
        name=name,
        kind=KIND_INDEX_ETF,
        bucket=BUCKET_SAFE,
        max_weight=Decimal(max_weight),
    )


def _growth(symbol, name, max_weight="0.12"):
    """A young company in an industry that may matter later.

    Capped tighter than anything in CORE, and deliberately short: a name
    belongs here only after a human put it in config, which is the same
    asymmetry ``src/universe_review.py`` enforces - the model may propose,
    only a person may add. Backtests of this bucket prove very little, since
    every candidate is a recent listing chosen with hindsight; that is a
    reason to keep the bucket small, not a reason to pretend otherwise.
    """
    return Instrument(
        symbol=symbol,
        name=name,
        kind=KIND_STOCK,
        bucket=BUCKET_GROWTH,
        max_weight=Decimal(max_weight),
    )


def _index(symbol, name, leverage="1", max_weight="0.30"):
    return Instrument(
        symbol=symbol,
        name=name,
        kind=KIND_INDEX_ETF,
        leverage=Decimal(leverage),
        max_weight=Decimal(max_weight),
    )


#: The agreed default. Editable in config; the policy rules above still apply
#: to whatever config says.
#:
#: Deliberately wider than the seven names anyone would name today. A universe
#: assembled from the winners of the last decade gives a momentum ranking
#: nothing to rank: the 2026-08-27 backtest showed the strategy *losing* to an
#: equal-weight buy of its own twelve-name universe (33.9%/57.7% MDD vs
#: 38.1%/55.6%), and beating equal-weight only once the same list was widened
#: with large caps that were obvious 2012 holdings and turned out not to be
#: winners (31.1% vs 30.3%, and 29.4% vs 27.2% from 2016). The ranking works -
#: it just needs losers in the list to reject. Which of these names is which,
#: over the years ahead, is exactly what is not knowable now.
#:
#: Sector spread is the point, not a view on any one name. Every symbol here
#: has a 2010+ daily history, so the whole list is backtestable over one span.
DEFAULT_UNIVERSE = Universe(
    (
        # --- mega-cap tech, the concentrated core ---
        _stock("AAPL", "Apple"),
        _stock("MSFT", "Microsoft"),
        _stock("NVDA", "NVIDIA"),
        _stock("GOOGL", "Alphabet"),
        _stock("AMZN", "Amazon"),
        _stock("META", "Meta Platforms"),
        _stock("TSLA", "Tesla"),
        # --- semiconductors and hardware ---
        _stock("AVGO", "Broadcom"),
        _stock("AMD", "Advanced Micro Devices"),
        _stock("TXN", "Texas Instruments"),
        _stock("QCOM", "Qualcomm"),
        _stock("INTC", "Intel"),
        _stock("CSCO", "Cisco Systems"),
        # --- software and internet ---
        _stock("ORCL", "Oracle"),
        _stock("CRM", "Salesforce"),
        _stock("ADBE", "Adobe"),
        _stock("NFLX", "Netflix"),
        _stock("IBM", "IBM"),
        # --- financials ---
        _stock("JPM", "JPMorgan Chase"),
        _stock("V", "Visa"),
        _stock("MA", "Mastercard"),
        # --- healthcare ---
        _stock("LLY", "Eli Lilly"),
        _stock("UNH", "UnitedHealth"),
        _stock("JNJ", "Johnson & Johnson"),
        # --- consumer ---
        _stock("COST", "Costco"),
        _stock("HD", "Home Depot"),
        _stock("WMT", "Walmart"),
        _stock("MCD", "McDonald's"),
        _stock("DIS", "Walt Disney"),
        _stock("PG", "Procter & Gamble"),
        _stock("KO", "Coca-Cola"),
        # --- future-growth industries (GROWTH bucket) ---
        # Its history starts 2021, so any backtest reaching further back
        # measures this bucket over a fraction of the span - which is the
        # honest state of a bucket made of young companies, not a defect to
        # paper over.
        _growth("IONQ", "IonQ"),
        # --- energy and industrials ---
        _stock("XOM", "Exxon Mobil"),
        _stock("CVX", "Chevron"),
        _stock("CAT", "Caterpillar"),
        # --- index ETFs ---
        _index("QQQ", "Invesco QQQ Trust", max_weight="0.60"),
        _index("SOXX", "iShares Semiconductor ETF", max_weight="0.30"),
        _index("SMH", "VanEck Semiconductor ETF", max_weight="0.30"),
        # 2x index funds only, and capped tighter than anything else here.
        _index("QLD", "ProShares Ultra QQQ (2x)", leverage="2", max_weight="0.15"),
        _index("SSO", "ProShares Ultra S&P500 (2x)", leverage="2", max_weight="0.15"),
        # --- safe assets (SAFE bucket) ---
        # Two of them, against two different bad weeks. A short-duration
        # Treasury fund barely moves when equities fall; gold can fall with
        # them for a stretch but is the one holding here that does not care
        # what interest rates do. Holding only one would leave the other
        # scenario uncovered, and both have daily history back past 2005, so
        # neither shortens the span a backtest can cover.
        _safe("SHY", "iShares 1-3 Year Treasury Bond ETF"),
        _safe("GLD", "SPDR Gold Shares"),
    )
)
