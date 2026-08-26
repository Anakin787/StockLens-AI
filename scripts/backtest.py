"""Backtest CLI. Terminal text summary, in the house style.

    python -m scripts.backtest --start 2016-01-01 --end 2026-08-01 --offline
    python -m scripts.backtest --refresh          # fetch/update the bar cache only

``--refresh`` is a separate step, on purpose: a backtest run must never
silently become a network operation, and the two are easy to conflate if they
share a code path.
"""

import argparse
import sys
from datetime import date

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="M7 Terminal 백테스트")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--contribution", type=int, default=750000, help="월 적립액(원)")
    parser.add_argument("--initial", type=int, default=1000000, help="초기 시드(원)")
    parser.add_argument("--db-path", default=None)
    parser.add_argument(
        "--offline", action="store_true", help="캐시된 데이터만 사용 (네트워크 호출 없음)"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="시세 캐시만 갱신하고 종료 (백테스트 실행 안 함)"
    )
    parser.add_argument("--split", default=None, help="인/아웃오브샘플 분리 기준일 (YYYY-MM-DD)")
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
        print(f" · 승률 {m['win_rate']:.1%} · 평균익 {m['avg_win_usd']:.1f} · 평균손 {m['avg_loss_usd']:.1f}")
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


def run(argv=None):
    args = parse_args(argv)
    config = load_config()
    db_path = args.db_path or config.db_path

    cache = BarCache(db_path)
    source = YahooBarSource()
    loader = HistoryLoader(cache, source=source, offline=args.offline)

    strategy = load_strategy(args.strategy, config.trading)
    universe = getattr(strategy, "universe", None) or parse_universe(config.trading.universe)
    symbols = set(universe.symbols())
    benchmark = getattr(strategy.params, "benchmark", "QQQ") if hasattr(strategy, "params") else "QQQ"
    symbols.add(benchmark)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    if args.refresh or not args.offline:
        print(f">>> 시세 캐시 갱신 중... ({sorted(symbols)})")
        added = loader.refresh(sorted(symbols), start, end)
        print(f"    추가된 봉 수: {added}")
        if args.refresh:
            return 0

    history = loader.load(sorted(symbols), start, end)
    missing = symbols - set(history)
    if missing:
        print(f"!!! 캐시에 없는 심볼(건너뜀): {sorted(missing)}")
    if benchmark not in history:
        print(f"!!! 벤치마크 {benchmark}의 데이터가 없어 백테스트를 진행할 수 없습니다.")
        return 1

    backtest_config = BacktestConfig(
        initial_krw=args.initial,
        contribution=ContributionSchedule(amount_krw=args.contribution, day_of_month=1),
        fills=FillModel(),
        # Same configured limits as the live gate, with only `strict` flipped -
        # otherwise the backtest would validate a strategy the live gate
        # would refuse to run.
        limits=RiskLimits(**{**config.trading.limits, "strict": False}),
        benchmark=benchmark,
    )

    print("=" * 56)
    print("  M7 Terminal · 백테스트")
    print(f"  전략: {strategy.name} · 기간: {start} ~ {end}")
    print("=" * 56)

    result = Backtester(strategy, history, backtest_config).run()
    _print_report("전체 구간", result)

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
