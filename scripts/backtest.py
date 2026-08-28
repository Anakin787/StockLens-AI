"""Backtest CLI. Terminal text summary, in the house style.

    python -m scripts.backtest --start 2016-01-01 --end 2026-08-01 --offline
    python -m scripts.backtest --refresh          # fetch/update the bar cache only

``--refresh`` is a separate step, on purpose: a backtest run must never
silently become a network operation, and the two are easy to conflate if they
share a code path.
"""

import argparse
import sys
from datetime import date, timedelta

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

from src.backtest.benchmarks import dca_curve, summarize_curve
from src.backtest.engine import Backtester, BacktestConfig
from src.backtest.fills import ContributionSchedule, FillModel
from src.config import load_config
from src.data.cache import BarCache
from src.data.loader import HistoryLoader
from src.data.yahoo import YahooBarSource
from src.execution.risk import RiskLimits
from src.strategy.loader import load_strategy
from src.strategy.momentum_dca import MomentumDcaStrategy
from src.strategy.universe import parse_universe
from src.toss.errors import TossConfigError

DEFAULT_STRATEGY = "src.strategy.momentum_dca:MomentumDcaStrategy"

#: USD/KRW daily series. Every deposit in this backtest is KRW converted to
#: USD, so a constant rate quietly prices 2012 dollars at today's won - the
#: won moved from ~1150 to ~1380 over the span, and that move lands in the
#: reported return as if the strategy had earned it.
FX_SYMBOL = "KRW=X"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="M7 Terminal 백테스트")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--contribution", type=int, default=750000, help="월 적립액(원)")
    parser.add_argument("--initial", type=int, default=1000000, help="초기 시드(원)")
    parser.add_argument(
        "--offline", action="store_true", help="캐시된 데이터만 사용 (네트워크 호출 없음)"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="시세 캐시만 갱신하고 종료 (백테스트 실행 안 함)"
    )
    parser.add_argument("--split", default=None, help="인/아웃오브샘플 분리 기준일 (YYYY-MM-DD)")
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=None,
        help="거래 시작 전에 전략이 읽을 과거 봉의 기간(일). 기본은 전략의 "
        "required_bars에서 계산합니다. 0이면 워밍업 없이(예전 동작) 돕니다.",
    )
    parser.add_argument(
        "--fx",
        default=FX_SYMBOL,
        help=f"환율 시계열 심볼 (기본 {FX_SYMBOL}). 'none'이면 상수 환율을 씁니다.",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="전략을 안 썼을 때(균등 DCA·벤치마크 DCA)와의 비교를 생략합니다",
    )
    return parser.parse_args(argv)


def _print_report(label, result):
    m = result.metrics
    print(f"--- {label} ---")
    if not result.equity_curve:
        print("    데이터 없음")
        return
    print(f"    기간: {result.equity_curve[0].date} ~ {result.equity_curve[-1].date}")
    print(f"    최종 자산: {m['final_equity_krw']:,.0f}원 (누적 입금 {m['total_contributed_krw']:,.0f}원)")
    twr = m["twr_cagr"]
    irr = m["irr"]
    print(f"    TWR CAGR(전략 성과): {twr:.2%}" if twr is not None else "    TWR CAGR: 계산 불가")
    print(f"    IRR(계좌 실현 성과): {irr:.2%}" if irr is not None else "    IRR: 계산 불가")
    print(f"    MDD: {m['mdd']:.2%}" if m["mdd"] is not None else "    MDD: 계산 불가")
    print(f"    거래 {m['trade_count']}건", end="")
    if m["win_rate"] is not None:
        # A run with no losing trade (or no winning one) leaves the matching
        # average at None - all-winners is a real outcome for a strategy that
        # only ever sells on a trend break, not a missing number to hide.
        avg_win = f"{m['avg_win_usd']:.1f}" if m["avg_win_usd"] is not None else "-"
        avg_loss = f"{m['avg_loss_usd']:.1f}" if m["avg_loss_usd"] is not None else "-"
        print(f" · 승률 {m['win_rate']:.1%} · 평균익 {avg_win} · 평균손 {avg_loss}")
    else:
        print()
    if m["rejections_by_rule"]:
        print(f"    거부 사유: {m['rejections_by_rule']}")
    if m["attribution_by_mode"]:
        print("    모드별 기여도:")
        for mode, bucket in m["attribution_by_mode"].items():
            print(
                f"      {mode}: {bucket['trade_count']}건, "
                f"{bucket['pnl_usd']:.1f} USD, 종목 {bucket['symbols']}"
            )


def _print_comparison(result, history, backtest_config, benchmark, fx_history=None):
    """The strategy against the same money with no strategy at all.

    Printed by default, not behind a flag, because a strategy's own CAGR is
    not evidence of anything on a universe assembled with hindsight - only
    the gap to buying that same universe outright is.

    The benchmark curves run over the *traded* dates and on the *same* FX
    series as the strategy. Both matter: warm-up bars handed to a benchmark
    would have it investing a year before the strategy is allowed to, and a
    constant rate here against a real series there would put a decade of
    currency move on one curve only.
    """
    dates = history[benchmark].dates
    if backtest_config.trade_from is not None:
        dates = [day for day in dates if day >= backtest_config.trade_from]
    fx_rate = backtest_config.fx_rate
    if fx_history is not None:
        def fx_rate(day, _series=fx_history, _fallback=backtest_config.fx_rate):
            last = _series.as_of(day).last()
            return last.close if last is not None else _fallback
    rows = [
        (f"유니버스 {len(history)}종목 균등 DCA", sorted(history)),
        (f"{benchmark} DCA", [benchmark]),
    ]
    print()
    print("--- 전략을 안 썼다면 (같은 기간·같은 적립) ---")
    for label, symbols in rows:
        stats = summarize_curve(
            dca_curve(
                history,
                symbols,
                dates,
                backtest_config.contribution,
                backtest_config.initial_krw,
                fx_rate,
            )
        )
        twr = f"{stats['twr_cagr']:.2%}" if stats["twr_cagr"] is not None else "계산 불가"
        mdd = f"{stats['mdd']:.2%}" if stats["mdd"] is not None else "계산 불가"
        print(f"    {label}: TWR {twr} · MDD {mdd} · 최종 {stats['final_equity_krw']:,.0f}원")
    strategy_twr = result.metrics["twr_cagr"]
    if strategy_twr is not None:
        print(f"    (전략: TWR {strategy_twr:.2%} · MDD {result.metrics['mdd']:.2%})")


def run(argv=None):
    args = parse_args(argv)
    config = load_config()

    cache = BarCache()
    source = YahooBarSource()
    loader = HistoryLoader(cache, source=source, offline=args.offline)

    strategy = load_strategy(args.strategy, config.trading)
    universe = getattr(strategy, "universe", None) or parse_universe(config.trading.universe)
    symbols = set(universe.symbols())
    benchmark = getattr(strategy.params, "benchmark", "QQQ") if hasattr(strategy, "params") else "QQQ"
    symbols.add(benchmark)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    # Warm-up. The strategy needs `required_bars` sessions of history before
    # it can rank anything; loading only [start, end] means it holds cash for
    # that first stretch while every comparison curve is fully invested, and
    # the gap between them then measures which year the strategy sat out.
    # Bars before `start` are read but never traded on.
    warmup_days = args.warmup_days
    if warmup_days is None:
        required = getattr(getattr(strategy, "params", None), "required_bars", 0)
        # Sessions -> calendar days (252 sessions a year), plus a month of slack
        # so a long holiday stretch cannot leave the window one bar short.
        warmup_days = int(required * 365 / 252) + 30 if required else 0
    load_start = start - timedelta(days=warmup_days)
    trade_from = start if warmup_days else None

    fx_symbol = None if args.fx.lower() == "none" else args.fx

    if args.refresh or not args.offline:
        print(f">>> 시세 캐시 갱신 중... ({sorted(symbols)})")
        added = loader.refresh(sorted(symbols), load_start, end)
        print(f"    추가된 봉 수: {added}")
        if fx_symbol:
            loader.refresh([fx_symbol], load_start, end)
        if args.refresh:
            return 0

    history = loader.load(sorted(symbols), load_start, end)
    missing = symbols - set(history)
    if missing:
        print(f"!!! 캐시에 없는 심볼(건너뜀): {sorted(missing)}")
    if benchmark not in history:
        print(f"!!! 벤치마크 {benchmark}의 데이터가 없어 백테스트를 진행할 수 없습니다.")
        return 1

    fx_history = None
    if fx_symbol:
        fx_history = loader.load([fx_symbol], load_start, end).get(fx_symbol)
        if fx_history is None:
            print(
                f"!!! 환율 {fx_symbol} 시계열이 캐시에 없습니다. 상수 환율로 진행합니다 "
                f"(--refresh 로 받거나 --fx none 으로 명시하세요)."
            )

    backtest_config = BacktestConfig(
        initial_krw=args.initial,
        contribution=ContributionSchedule(amount_krw=args.contribution, day_of_month=1),
        fills=FillModel(),
        # Same configured limits as the live gate, with only `strict` flipped -
        # otherwise the backtest would validate a strategy the live gate
        # would refuse to run.
        limits=RiskLimits(**{**config.trading.limits, "strict": False}),
        benchmark=benchmark,
        trade_from=trade_from,
    )

    print("=" * 56)
    print("  M7 Terminal · 백테스트")
    print(f"  전략: {strategy.name} · 기간: {start} ~ {end}")
    print("=" * 56)

    result = Backtester(strategy, history, backtest_config, fx_history=fx_history).run()
    _print_report("전체 구간", result)

    if not args.no_compare:
        _print_comparison(result, history, backtest_config, benchmark, fx_history)

    if args.split:
        split_day = date.fromisoformat(args.split)
        in_sample = [p for p in result.equity_curve if p.date < split_day]
        out_sample = [p for p in result.equity_curve if p.date >= split_day]
        if in_sample and out_sample:
            print()
            print("과최적화 경고: 파라미터는 인/아웃오브샘플을 분리해 검증하기 전엔 신뢰하지 마세요.")
            # A full split re-run (separate contexts) is future work; today's
            # split view slices the same run's equity curve, which is honest
            # about drift within one run but not a substitute for two
            # independent fits.

    return 0


if __name__ == "__main__":
    sys.exit(run())
