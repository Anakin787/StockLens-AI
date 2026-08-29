"""Run many strategy configurations and write the results up as Markdown.

One backtest tells you a number. The question this project keeps running into
is which of several plausible rules is better, and that only ever comes from
running them side by side against the same money over the same window - the
2026-08-27 comparison changed a conclusion, and so did the warm-up fix. This
script is that comparison, automated, so a new idea costs one line rather than
an afternoon.

    python -m scripts.strategy_lab                    # every group
    python -m scripts.strategy_lab --group budget     # just one
    python -m scripts.strategy_lab --out docs/lab.md

Results are appended to the output file as each case finishes, so a run that
is still going is still readable, and a run that dies leaves what it learned.
"""

import argparse
import sys
import traceback
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace", line_buffering=True)
    except (AttributeError, ValueError):
        pass

from src.backtest.benchmarks import dca_curve, summarize_curve
from src.backtest.engine import Backtester, BacktestConfig
from src.backtest.fills import ContributionSchedule, FillModel
from src.backtest.tax import CapitalGainsTax
from src.config import load_config
from src.data.cache import BarCache
from src.data.loader import HistoryLoader
from src.execution.risk import RiskLimits
from src.strategy.bucket_dca import BucketDcaParams, BucketDcaStrategy
from src.strategy.momentum_dca import MomentumDcaParams, MomentumDcaStrategy
from src.strategy.universe import BUCKET_CORE, BUCKET_GROWTH, BUCKET_SAFE, DEFAULT_UNIVERSE

D = Decimal
BENCHMARK = "QQQ"
END = date(2026, 8, 28)
CONTRIBUTION_KRW = 750_000
INITIAL_KRW = 1_000_000

#: Starts chosen to land on a trading day. They overlap heavily - four views
#: of one market, not four independent samples - which is why every table
#: below reports all of them rather than the flattering one.
STARTS = {
    "2014": date(2014, 1, 2),
    "2016": date(2016, 1, 4),
    "2018": date(2018, 1, 2),
    "2020": date(2020, 1, 2),
    "2022": date(2022, 1, 3),
}

#: The daily-notional limit in config was sized for a strategy that only ever
#: bought. A rotating one moves whole positions, and the configured 5,000,000
#: KRW rejected 3,920 sells in the first run here - the gate deciding the
#: strategy rather than backstopping it. Raised for the lab so what is being
#: measured is the strategy; see the write-up's open questions for the live
#: figure, which is a decision, not a default.
LAB_DAILY_NOTIONAL_KRW = 100_000_000


def _fmt_pct(value):
    return f"{value:.2%}" if value is not None else "n/a"


def _fmt_krw(value):
    return f"{value:,.0f}" if value is not None else "n/a"


class Lab:
    def __init__(self, out_path, offline=True, contribution_krw=None):
        self.out_path = out_path
        cfg = load_config()
        self.limits = {
            **cfg.trading.limits,
            "max_daily_notional_krw": LAB_DAILY_NOTIONAL_KRW,
            "strict": False,
        }
        self.schedule = ContributionSchedule(
            amount_krw=D(contribution_krw or CONTRIBUTION_KRW), day_of_month=1
        )
        loader = HistoryLoader(BarCache(), source=None, offline=offline)
        symbols = sorted(set(DEFAULT_UNIVERSE.symbols()) | {BENCHMARK})
        warmup = timedelta(days=int(BucketDcaParams().required_bars * 365 / 252) + 30)
        earliest = min(STARTS.values()) - warmup
        self.history = loader.load(symbols, earliest, END)
        self.fx = loader.load(["KRW=X"], earliest, END).get("KRW=X")
        if BENCHMARK not in self.history:
            raise SystemExit(f"{BENCHMARK} 시세가 캐시에 없습니다. --refresh 먼저 돌리세요.")
        self._bench_cache = {}

    # ------------------------------------------------------------- running

    def _rate(self, day):
        if self.fx is None:
            return D("1350")
        last = self.fx.as_of(day).last()
        return last.close if last is not None else D("1350")

    def run_case(self, strategy, start, tax=True):
        config = BacktestConfig(
            initial_krw=D(INITIAL_KRW),
            contribution=self.schedule,
            fills=FillModel(),
            limits=RiskLimits(**self.limits),
            benchmark=BENCHMARK,
            trade_from=start,
            tax=CapitalGainsTax(enabled=tax),
        )
        result = Backtester(
            strategy, self.history, config, fx_history=self.fx
        ).run()
        return self._summarise(result)

    def _summarise(self, result):
        metrics = result.metrics
        approved = [d for d in result.decisions if d.approved]
        sells = [d for d in approved if not d.signal.is_buy]
        rejections = defaultdict(int)
        for decision in result.decisions:
            if not decision.approved:
                rejections[decision.rejection.rule] += 1

        # Concurrent holdings, replayed from the approved decisions.
        quantities = defaultdict(Decimal)
        counts = []
        for decision in approved:
            signal = decision.signal
            if signal.is_buy:
                quantities[signal.symbol] += D("1")
            else:
                quantities[signal.symbol] = ZERO_D
            counts.append(sum(1 for q in quantities.values() if q > ZERO_D))

        return {
            "twr": metrics["twr_cagr"],
            "mdd": metrics["mdd"],
            "final_krw": metrics["final_equity_krw"],
            "sells": len(sells),
            "buys": len(approved) - len(sells),
            "held_max": max(counts) if counts else 0,
            "held_final": counts[-1] if counts else 0,
            "tax_krw": metrics.get("tax_paid_krw"),
            "rejections": dict(rejections),
        }

    # ---------------------------------------------------------- benchmarks

    def benchmarks(self, start, safe_share=D("0.20")):
        key = (start, safe_share)
        if key in self._bench_cache:
            return self._bench_cache[key]
        dates = [d for d in self.history[BENCHMARK].dates if d >= start]
        safe = [
            i.symbol
            for i in DEFAULT_UNIVERSE.by_bucket(BUCKET_SAFE)
            if i.symbol in self.history
        ]
        risky = [s for s in sorted(self.history) if s not in safe]

        def curve(weights=None, symbols=None):
            return summarize_curve(
                dca_curve(
                    self.history,
                    symbols or sorted(self.history),
                    dates,
                    self.schedule,
                    D(INITIAL_KRW),
                    self._rate,
                    weights=weights,
                )
            )

        blend = None
        if safe and risky and safe_share > 0:
            blend = {s: safe_share / D(len(safe)) for s in safe}
            blend.update({s: (D("1") - safe_share) / D(len(risky)) for s in risky})

        out = {
            "전체 균등 DCA": curve(),
            f"같은 배분 균등 DCA (안전 {safe_share:.0%})": curve(weights=blend)
            if blend
            else None,
            f"{BENCHMARK} DCA": curve(symbols=[BENCHMARK]),
        }
        self._bench_cache[key] = out
        return out

    # -------------------------------------------------------------- output

    def write(self, text):
        with open(self.out_path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(text)


ZERO_D = Decimal("0")


# --------------------------------------------------------------- case sets


def base_params(**overrides):
    return BucketDcaParams(**overrides)


def slots(safe=2, core=6, growth=2):
    return ((BUCKET_SAFE, safe), (BUCKET_CORE, core), (BUCKET_GROWTH, growth))


def weights(safe="0.20", core="0.60", growth="0.20"):
    return (
        (BUCKET_SAFE, D(safe)),
        (BUCKET_CORE, D(core)),
        (BUCKET_GROWTH, D(growth)),
    )


#: Every group is (title, why-it-is-being-asked, [(label, params-or-strategy)]).
def build_groups():
    groups = []

    groups.append((
        "1. 주간 배포 상한",
        "고정 달러 상한이 매도 대금 재투자를 막아 현금이 쌓였습니다. "
        "자산 대비 비율로 바꾸면 몇 %가 적정한지.",
        [
            ("고정 $700만 (기존)", base_params(max_deploy_per_week_pct=None)),
            ("상한 없음", base_params(max_deploy_per_week_pct=None, max_deploy_per_week_usd=None)),
            ("자산의 1%", base_params(max_deploy_per_week_pct=D("0.01"))),
            ("자산의 2%", base_params(max_deploy_per_week_pct=D("0.02"))),
            ("자산의 5%", base_params(max_deploy_per_week_pct=D("0.05"))),
            ("자산의 10%", base_params(max_deploy_per_week_pct=D("0.10"))),
            ("자산의 20%", base_params(max_deploy_per_week_pct=D("0.20"))),
        ],
    ))

    groups.append((
        "2. 매도 규칙",
        "무엇을 이유로 파는가. 순위에서 밀려서인가, 종목 자체가 무너져서인가.",
        [
            ("순위 기반 (buffer 2)", base_params(require_absolute_exit=False)),
            ("순위 기반 (buffer 4)", base_params(require_absolute_exit=False, rotation_buffer=4)),
            ("순위 기반 (buffer 8)", base_params(require_absolute_exit=False, rotation_buffer=8)),
            ("절대 조건 (현재 기본)", base_params()),
            ("절대 + 추세 이탈 동시", base_params(exit_requires_trend_break=True)),
            ("교체 매도 끔 (추세이탈 매도만)", base_params(rotation_enabled=False)),
        ],
    ))

    groups.append((
        "3. 슬롯 점유와 교체 마진",
        "보유 종목이 슬롯을 얼마나 지키는가. 종목 수 상한이 실제로 걸리는지.",
        [
            ("점유 없음", base_params(prefer_incumbents=False)),
            ("점유 (교체 불가)", base_params(prefer_incumbents=True)),
            ("점유 + 마진 10%", base_params(prefer_incumbents=True, incumbent_margin=D("0.10"))),
            ("점유 + 마진 25%", base_params(prefer_incumbents=True, incumbent_margin=D("0.25"))),
            ("점유 + 마진 50%", base_params(prefer_incumbents=True, incumbent_margin=D("0.50"))),
        ],
    ))

    groups.append((
        "4. 종목 수 (CORE 슬롯)",
        "10종목은 임의로 정한 숫자입니다. 몇 개가 실제로 나은지.",
        [
            (f"CORE {n} (총 {n + 3}종목)", base_params(bucket_slots=slots(core=n)))
            for n in (2, 3, 4, 6, 8, 10, 14)
        ],
    ))

    groups.append((
        "5. 버킷 비중",
        "안전자산을 얼마나 들 것인가. 수익과 낙폭의 교환 비율.",
        [
            ("안전 0 / 일반 80 / 성장 20", base_params(
                bucket_weights=weights("0", "0.80", "0.20"), bucket_slots=slots(safe=0))),
            ("안전 10 / 일반 70 / 성장 20", base_params(
                bucket_weights=weights("0.10", "0.70", "0.20"))),
            ("안전 20 / 일반 60 / 성장 20 (현재)", base_params()),
            ("안전 30 / 일반 50 / 성장 20", base_params(
                bucket_weights=weights("0.30", "0.50", "0.20"))),
            ("안전 40 / 일반 40 / 성장 20", base_params(
                bucket_weights=weights("0.40", "0.40", "0.20"))),
        ],
    ))

    groups.append((
        "6. 부분 매도",
        "나갈 때 전량인가 일부인가. 실현이익이 줄면 세금도 줄어듭니다.",
        [
            ("전량 (현재)", base_params()),
            ("70%씩", base_params(exit_fraction=D("0.7"))),
            ("50%씩", base_params(exit_fraction=D("0.5"))),
            ("30%씩", base_params(exit_fraction=D("0.3"))),
        ],
    ))

    groups.append((
        "7. 배분 방식",
        "목표 비중까지 채우는가(리밸런싱), 새 돈만 비중대로 나누는가(흐름).",
        [
            ("target (현재)", base_params()),
            ("flow", base_params(weight_mode="flow")),
            ("target + 점유 없음", base_params(prefer_incumbents=False)),
            ("flow + 점유 없음", base_params(weight_mode="flow", prefer_incumbents=False)),
        ],
    ))

    groups.append((
        "8. 모멘텀 점수",
        "순위를 매기는 신호 자체. 이전 세션에서 손대지 않기로 한 항목들입니다.",
        [
            ("기본 (63/126/252, 변동성 조정)", base_params()),
            ("변동성 조정 끔", base_params(vol_adjust=False)),
            ("단기 (21/63)", base_params(lookbacks=((21, D("0.6")), (63, D("0.4"))))),
            ("장기 (126/252)", base_params(lookbacks=((126, D("0.5")), (252, D("0.5"))))),
            ("최근 1주 제외 안 함", base_params(skip_days=0)),
        ],
    ))

    groups.append((
        "9. 리밸런스 주기",
        "언제 사는가. 요일 자체에는 근거가 없었습니다.",
        [
            ("월요일 (현재)", base_params()),
            ("화요일", base_params(rebalance_weekday=1)),
            ("수요일", base_params(rebalance_weekday=2)),
            ("목요일", base_params(rebalance_weekday=3)),
            ("금요일", base_params(rebalance_weekday=4)),
        ],
    ))

    groups.append((
        "10. 급락 추가매수(dislocation)",
        "14년간 2번 발동한 기능입니다. 켜고 끄는 차이가 실제로 있는지.",
        [
            ("켬 (현재)", base_params()),
            ("끔", base_params(dislocation_enabled=False)),
            ("배수 3배", base_params(dislocation_budget_multiple=D("3"))),
        ],
    ))

    groups.append((
        "11. 절대 모멘텀 문턱 (min_score)",
        "'수익률이 이보다 낮으면 아예 사지 않는다'의 경계. 실질적인 하방 방어 "
        "두 가지 중 하나입니다(다른 하나는 추세 이탈 매도).",
        [
            ("0 — 상승 중이면 다 후보 (현재)", base_params()),
            ("0.05", base_params(min_score=D("0.05"))),
            ("0.15", base_params(min_score=D("0.15"))),
            ("0.30", base_params(min_score=D("0.30"))),
        ],
    ))

    groups.append((
        "12. 추세 필터 기간 (trend_sma)",
        "레버리지 상품을 사고 파는 기준선. 200일은 관습적인 값이고 검증된 적은 없습니다.",
        [
            ("100일", base_params(trend_sma=100)),
            ("150일", base_params(trend_sma=150)),
            ("200일 (현재)", base_params()),
            ("250일", base_params(trend_sma=250)),
        ],
    ))

    groups.append((
        "13. 현금 유보 (cash_reserve)",
        "매수 여력 중 남겨두는 몫. 남겨둔 만큼은 투자되지 않습니다.",
        [
            ("0%", base_params(cash_reserve=D("0"))),
            ("5% (현재)", base_params()),
            ("10%", base_params(cash_reserve=D("0.10"))),
            ("20%", base_params(cash_reserve=D("0.20"))),
        ],
    ))

    groups.append((
        "14. 못 채운 버킷의 몫을 어디로",
        "GROWTH는 종목이 부족해 슬롯이 빕니다. 그 몫을 어느 버킷이 가져가는가.",
        [
            ("CORE로 (현재)", base_params()),
            ("SAFE로", base_params(unfilled_weight_to=BUCKET_SAFE)),
            ("GROWTH 슬롯을 1개로 줄임", base_params(bucket_slots=slots(growth=1))),
        ],
    ))

    return groups


def build_round_two(best=None):
    """Combinations and robustness, run once the one-factor tables are in.

    Single-factor tables say which knob helps on its own; they do not say
    whether two that help separately still help together. The first build
    here found exactly that trap - incumbency and flow each looked fine and
    were worse combined - so the promising settings get run as combinations
    rather than assumed to add up.
    """
    best = best or {}
    groups = []

    groups.append((
        "15. 유력 조합",
        "1~10에서 각각 좋았던 설정을 함께 걸었을 때. 따로 좋은 것이 같이도 좋다는 "
        "보장은 없습니다 — 슬롯 점유와 flow가 각각은 괜찮았지만 합치면 나빴던 전례가 "
        "있습니다.",
        [
            ("기본값 (현재 코드)", base_params()),
            ("보수: 점유 + 절대매도 + 상한 5%", base_params(
                prefer_incumbents=True, require_absolute_exit=True,
                max_deploy_per_week_pct=D("0.05"))),
            ("적극: 점유없음 + 상한 20%", base_params(
                prefer_incumbents=False, max_deploy_per_week_pct=D("0.20"))),
            ("저회전: 점유 + 마진50% + 추세이탈 동시", base_params(
                prefer_incumbents=True, incumbent_margin=D("0.50"),
                exit_requires_trend_break=True)),
            ("무매도: 점유 + 교체 매도 끔", base_params(
                prefer_incumbents=True, rotation_enabled=False)),
            ("집중: CORE 3슬롯 + 점유", base_params(
                bucket_slots=slots(core=3), prefer_incumbents=True)),
            ("분산: CORE 10슬롯 + 점유", base_params(
                bucket_slots=slots(core=10), prefer_incumbents=True)),
        ],
    ))

    groups.append((
        "16. 세금을 뺀 같은 표",
        "회전이 많은 설정일수록 세금이 성과를 갉아먹습니다. 세전/세후를 나란히 보면 "
        "그 설정이 실제로 무엇을 벌었는지 분리됩니다.",
        [
            ("기본값", base_params()),
            ("점유 없음 (회전 많음)", base_params(prefer_incumbents=False)),
            ("순위 매도 (회전 가장 많음)", base_params(require_absolute_exit=False)),
            ("교체 매도 끔 (회전 0)", base_params(rotation_enabled=False)),
        ],
    ))

    groups.append((
        "17. 기존 전략과 나란히",
        "momentum-dca(집중·무매도)와 bucket-dca(분산·안전자산)의 직접 비교. "
        "같은 창, 같은 적립, 같은 환율.",
        [
            ("momentum-dca (기존)", MomentumDcaStrategy()),
            ("bucket-dca (신규 기본값)", base_params()),
            ("bucket-dca 안전 0%", base_params(
                bucket_weights=weights("0", "0.80", "0.20"), bucket_slots=slots(safe=0))),
        ],
    ))

    return groups


def build_round_three():
    """Everything the first two rounds pointed at, tried together.

    The single-factor tables each hold one knob against the same default. Two
    of them moved a lot on their own - unfilled GROWTH weight going to SAFE
    rather than CORE, and turning volatility adjustment off - and neither has
    been seen next to the other.
    """
    groups = []

    conservative = dict(
        exit_requires_trend_break=True,
        unfilled_weight_to=BUCKET_SAFE,
        trend_sma=150,
    )

    groups.append((
        "18. 3차: 방어 조합",
        "낙폭을 낮추는 쪽으로 좋았던 설정들을 함께. 추세이탈 동시 조건 + 못 채운 "
        "몫을 SAFE로 + 추세필터 150일.",
        [
            ("기본값", base_params()),
            ("추세이탈 동시 조건만", base_params(exit_requires_trend_break=True)),
            ("못 채운 몫 SAFE로만", base_params(unfilled_weight_to=BUCKET_SAFE)),
            ("추세필터 150일만", base_params(trend_sma=150)),
            ("셋 다", base_params(**conservative)),
            ("셋 다 + 안전 30%", base_params(
                **conservative, bucket_weights=weights("0.30", "0.50", "0.20"))),
        ],
    ))

    groups.append((
        "19. 3차: 공격 조합 (변동성 조정 끔)",
        "수익이 가장 크게 오른 설정. 낙폭이 얼마나 따라 오르는지, 그리고 방어 "
        "설정으로 그걸 되돌릴 수 있는지.",
        [
            ("변동성 조정 끔만", base_params(vol_adjust=False)),
            ("끔 + 추세이탈 동시", base_params(vol_adjust=False, exit_requires_trend_break=True)),
            ("끔 + 방어 조합 셋", base_params(vol_adjust=False, **conservative)),
            ("끔 + 방어 셋 + 안전 30%", base_params(
                vol_adjust=False, **conservative,
                bucket_weights=weights("0.30", "0.50", "0.20"))),
            ("끔 + 방어 셋 + 안전 40%", base_params(
                vol_adjust=False, **conservative,
                bucket_weights=weights("0.40", "0.40", "0.20"))),
            ("끔 + CORE 10슬롯", base_params(vol_adjust=False, bucket_slots=slots(core=10))),
        ],
    ))

    groups.append((
        "20. 3차: 교체 마진 재측정",
        "3번 표의 마진 행들은 자리를 뺏긴 종목을 팔지 않던 버그 상태에서 쟀습니다. "
        "커밋 b4e767c 이후 다시 잽니다.",
        [
            ("점유 (교체 불가)", base_params()),
            ("점유 + 마진 10%", base_params(incumbent_margin=D("0.10"))),
            ("점유 + 마진 25%", base_params(incumbent_margin=D("0.25"))),
            ("점유 + 마진 50%", base_params(incumbent_margin=D("0.50"))),
            ("점유 + 마진 100%", base_params(incumbent_margin=D("1.00"))),
        ],
    ))

    return groups


def build_round_four():
    """The middle ground between shape and return.

    Turning rotation off wins by 5-8 points a year but lets the roster drift
    to nineteen names. The roster only grows when a held name drops out of
    the ranking and a challenger takes the freed slot, so fewer slots should
    mean slower drift - the question is whether a no-sell portfolio can be
    held near ten names by construction rather than by selling.
    """
    groups = []

    groups.append((
        "21. 4차: 안 팔면서 종목 수 묶기",
        "교체 매도를 끈 채로 슬롯을 줄이면 종목이 덜 늘어납니다. 상한과 수익을 "
        "얼마나 함께 가질 수 있는지.",
        [
            ("교체 매도 끔 · CORE 6 (기준)", base_params(rotation_enabled=False)),
            ("교체 매도 끔 · CORE 4", base_params(
                rotation_enabled=False, bucket_slots=slots(core=4))),
            ("교체 매도 끔 · CORE 3", base_params(
                rotation_enabled=False, bucket_slots=slots(core=3))),
            ("교체 매도 끔 · CORE 2", base_params(
                rotation_enabled=False, bucket_slots=slots(core=2))),
            ("교체 매도 켬 · CORE 6 (현재 기본값)", base_params()),
        ],
    ))

    groups.append((
        "22. 4차: 안 팔면서 낙폭 줄이기",
        "무매도의 약점은 낙폭(2014년 33.68%)입니다. 방어 설정으로 되돌릴 수 있는지.",
        [
            ("교체 매도 끔 (기준)", base_params(rotation_enabled=False)),
            ("끔 + 못 채운 몫 SAFE로", base_params(
                rotation_enabled=False, unfilled_weight_to=BUCKET_SAFE)),
            ("끔 + 안전 30%", base_params(
                rotation_enabled=False, bucket_weights=weights("0.30", "0.50", "0.20"))),
            ("끔 + 안전 40%", base_params(
                rotation_enabled=False, bucket_weights=weights("0.40", "0.40", "0.20"))),
            ("끔 + 안전 30% + 못 채운 몫 SAFE로", base_params(
                rotation_enabled=False, unfilled_weight_to=BUCKET_SAFE,
                bucket_weights=weights("0.30", "0.50", "0.20"))),
        ],
    ))

    groups.append((
        "23. 4차: 안 팔면서 수익 더 내기",
        "무매도에 변동성 조정 끔을 얹으면. 두 설정 모두 수익을 크게 올렸는데 "
        "함께 걸면 낙폭이 감당 가능한지.",
        [
            ("교체 매도 끔 (기준)", base_params(rotation_enabled=False)),
            ("끔 + 변동성 조정 끔", base_params(rotation_enabled=False, vol_adjust=False)),
            ("끔 + 변동성 조정 끔 + 안전 30%", base_params(
                rotation_enabled=False, vol_adjust=False,
                bucket_weights=weights("0.30", "0.50", "0.20"))),
            ("끔 + 변동성 조정 끔 + 안전 40%", base_params(
                rotation_enabled=False, vol_adjust=False,
                bucket_weights=weights("0.40", "0.40", "0.20"))),
        ],
    ))

    return groups


def build_mdd40():
    """Maximise return under the constraint the account owner actually gave.

    Safe assets fixed at 20%, drawdown allowed to 40%, holding count free.
    That last freedom matters: every earlier table held the slot count near
    ten because that was the stated shape, and the slot count turned out to
    be the main lever on concentration - and therefore on both return and
    drawdown. With the ceiling raised to 40% the question stops being "how do
    we keep drawdown near 20" and becomes "what is the most return available
    at 40, and does anything stay under it in every window".

    The binding window is 2014: it holds the largest drawdown in almost every
    configuration measured, so a row that clears 40% there clears it
    everywhere.
    """
    groups = []

    groups.append((
        "25. 낙폭 40% 예산 · 무매도 계열",
        "안전 20% 고정. 매도를 하지 않는 쪽에서 수익이 가장 높았으므로 거기서 "
        "출발해, 낙폭 40%를 넘기는 설정을 방어 장치로 끌어내립니다.",
        [
            ("무매도 + 변동성조정 끔 (기준)", base_params(
                rotation_enabled=False, vol_adjust=False)),
            ("+ 추세이탈 매도 허용", base_params(
                vol_adjust=False, exit_requires_trend_break=True)),
            ("+ 추세필터 150일", base_params(
                rotation_enabled=False, vol_adjust=False, trend_sma=150)),
            ("+ 못 채운 몫 SAFE로", base_params(
                rotation_enabled=False, vol_adjust=False,
                unfilled_weight_to=BUCKET_SAFE)),
            ("무매도 + 변동성조정 켬", base_params(rotation_enabled=False)),
        ],
    ))

    groups.append((
        "26. 낙폭 40% 예산 · 종목 수를 풀었을 때",
        "종목 수 상한이 없어졌으므로 슬롯을 넓게 훑습니다. 슬롯이 적을수록 집중되고, "
        "집중될수록 수익과 낙폭이 함께 오릅니다 — 40% 예산을 어디서 다 쓰는지.",
        [
            (f"무매도 · 변동성조정 끔 · CORE {n}", base_params(
                rotation_enabled=False, vol_adjust=False, bucket_slots=slots(core=n)))
            for n in (2, 3, 4, 6, 10, 14, 20)
        ],
    ))

    groups.append((
        "27. 낙폭 40% 예산 · 매도하는 계열",
        "매도는 연 5~8%p를 깎지만 낙폭도 함께 낮춥니다. 40% 예산 안에서 그 교환이 "
        "값어치가 있는지.",
        [
            ("변동성조정 끔 + 추세이탈 동시", base_params(
                vol_adjust=False, exit_requires_trend_break=True)),
            ("+ 추세필터 150일", base_params(
                vol_adjust=False, exit_requires_trend_break=True, trend_sma=150)),
            ("+ CORE 3슬롯", base_params(
                vol_adjust=False, exit_requires_trend_break=True,
                bucket_slots=slots(core=3))),
            ("+ CORE 10슬롯", base_params(
                vol_adjust=False, exit_requires_trend_break=True,
                bucket_slots=slots(core=10))),
            ("(참고) momentum-dca", MomentumDcaStrategy()),
        ],
    ))

    return groups


def build_ratio():
    """Most return per unit of drawdown, not most return under a ceiling.

    The 40% figure is what the account owner can survive at worst, not a
    budget to spend down: 30% return at 20% drawdown beats 40% at 40%, and
    paying twenty points of drawdown for ten of return is a bad trade at any
    ceiling. So the search runs around the configuration that currently wins
    on that ratio - the defensive combination - rather than around the ones
    that merely returned most.
    """
    A = dict(
        exit_requires_trend_break=True,
        unfilled_weight_to=BUCKET_SAFE,
        trend_sma=150,
    )
    groups = []

    groups.append((
        "28. 낙폭당 수익 · 종목 수 축",
        "A(방어 조합)를 기준으로 슬롯을 넓게 훑습니다. 종목 수 상한이 풀렸으므로 "
        "집중과 분산 어느 쪽이 낙폭당 수익을 높이는지.",
        [(f"A + CORE {n}", base_params(**A, bucket_slots=slots(core=n)))
         for n in (2, 3, 4, 6, 8, 10, 14)],
    ))

    groups.append((
        "29. 낙폭당 수익 · 매도와 점수 축",
        "A에서 한 축씩만 바꿉니다. 낙폭을 20%대로 유지하면서 수익을 더 낼 여지가 "
        "있는지.",
        [
            ("A (기준)", base_params(**A)),
            ("A + 교체매도 끔", base_params(**A, rotation_enabled=False)),
            ("A + 변동성조정 끔", base_params(**A, vol_adjust=False)),
            ("A + 점유 없음", base_params(**A, prefer_incumbents=False)),
            ("A + 상한 10%", base_params(**A, max_deploy_per_week_pct=D("0.10"))),
            ("A + 상한 20%", base_params(**A, max_deploy_per_week_pct=D("0.20"))),
            ("A + min_score 0.15", base_params(**A, min_score=D("0.15"))),
        ],
    ))

    groups.append((
        "30. 낙폭당 수익 · 방어를 더 얹으면",
        "A보다 낙폭을 더 낮출 수 있는지, 그리고 그만큼 수익이 깎이는지.",
        [
            ("A (기준)", base_params(**A)),
            ("A + 추세필터 100일", base_params(
                exit_requires_trend_break=True,
                unfilled_weight_to=BUCKET_SAFE, trend_sma=100)),
            ("A + 추세필터 200일", base_params(
                exit_requires_trend_break=True,
                unfilled_weight_to=BUCKET_SAFE, trend_sma=200)),
            ("A + 부분매도 70%", base_params(**A, exit_fraction=D("0.7"))),
            ("A + 리밸런스 금요일", base_params(**A, rebalance_weekday=4)),
        ],
    ))

    return groups


def build_refine():
    """Close in on the leader: A with the weekly cap opened up.

    Raising the deployment cap from 5% to 20% of equity lifted return in all
    five windows for about a point of drawdown - the cap was still throttling
    even after it stopped being a fixed dollar figure. Where that stops being
    true is the remaining question, along with whether the holdings count and
    the trend filter want re-picking now that the cap has moved.
    """
    A20 = dict(
        exit_requires_trend_break=True,
        unfilled_weight_to=BUCKET_SAFE,
        trend_sma=150,
        max_deploy_per_week_pct=D("0.20"),
    )
    groups = []

    groups.append((
        "31. 배포 상한의 무릎",
        "5% → 20%가 공짜에 가까웠습니다. 어디까지 열어도 낙폭이 안 오르는지.",
        [
            ("A + 상한 5% (기존 기본값)", base_params(
                exit_requires_trend_break=True, unfilled_weight_to=BUCKET_SAFE,
                trend_sma=150)),
            ("A + 상한 15%", base_params(
                exit_requires_trend_break=True, unfilled_weight_to=BUCKET_SAFE,
                trend_sma=150, max_deploy_per_week_pct=D("0.15"))),
            ("A + 상한 20%", base_params(**A20)),
            ("A + 상한 30%", base_params(
                exit_requires_trend_break=True, unfilled_weight_to=BUCKET_SAFE,
                trend_sma=150, max_deploy_per_week_pct=D("0.30"))),
            ("A + 상한 50%", base_params(
                exit_requires_trend_break=True, unfilled_weight_to=BUCKET_SAFE,
                trend_sma=150, max_deploy_per_week_pct=D("0.50"))),
            ("A + 상한 없음", base_params(
                exit_requires_trend_break=True, unfilled_weight_to=BUCKET_SAFE,
                trend_sma=150, max_deploy_per_week_pct=None,
                max_deploy_per_week_usd=None)),
        ],
    ))

    groups.append((
        "32. 상한 20% 위에서 나머지 축 재확인",
        "상한이 움직였으니 종목 수와 추세필터도 다시 골라야 할 수 있습니다.",
        [
            ("A20 (기준)", base_params(**A20)),
            ("A20 + CORE 8", base_params(**A20, bucket_slots=slots(core=8))),
            ("A20 + CORE 10", base_params(**A20, bucket_slots=slots(core=10))),
            ("A20 + 추세필터 200일", base_params(
                exit_requires_trend_break=True, unfilled_weight_to=BUCKET_SAFE,
                trend_sma=200, max_deploy_per_week_pct=D("0.20"))),
            ("A20 + 못 채운 몫 CORE로", base_params(
                exit_requires_trend_break=True, trend_sma=150,
                max_deploy_per_week_pct=D("0.20"))),
            ("A20 + 추세이탈 조건 끔", base_params(
                unfilled_weight_to=BUCKET_SAFE, trend_sma=150,
                max_deploy_per_week_pct=D("0.20"))),
        ],
    ))

    return groups


def build_finalists():
    """The three surviving configurations, head to head.

    Everything else in this file compares one knob against a default. These
    are the three whole answers, and the only remaining question about them
    is whether their order holds up when the money going in changes size -
    the deployment cap is a share of equity, so contribution size and cap
    interact, and a ranking that flips at a different deposit is a ranking
    that was measuring the deposit.
    """
    return [(
        "24. 최종 3안 head-to-head",
        "A 보수 / B 균형 / C 공격. 적립금 규모를 바꿔가며 순위가 유지되는지.",
        [
            ("A 보수 (방어 조합 셋)", base_params(
                exit_requires_trend_break=True,
                unfilled_weight_to=BUCKET_SAFE,
                trend_sma=150)),
            ("B 균형 (무매도 + 안전 30)", base_params(
                rotation_enabled=False,
                unfilled_weight_to=BUCKET_SAFE,
                bucket_weights=weights("0.30", "0.50", "0.20"))),
            ("C 공격 (무매도 + 변동성조정 끔 + 안전 40)", base_params(
                rotation_enabled=False, vol_adjust=False,
                bucket_weights=weights("0.40", "0.40", "0.20"))),
            ("(참고) 현재 기본값", base_params()),
            ("(참고) momentum-dca", MomentumDcaStrategy()),
        ],
    )]


# ----------------------------------------------------------------- report


def run_group(lab, title, why, cases, starts, tax=True):
    """One table. ``tax`` may be overridden per group via a title marker.

    A group titled "세금을 뺀" that still charges tax is worse than no group
    at all - it invites the reader to compare two identical columns and
    conclude tax costs nothing. The marker keeps the intent and the run in
    the same place.
    """
    if "세금을 뺀" in title:
        tax = False
    lab.write("")
    lab.write(f"### {title}")
    lab.write("")
    lab.write(why)
    lab.write("")

    header = "| 설정 | " + " | ".join(
        f"{name} TWR | {name} MDD" for name in starts
    ) + " | 매도 | 동시보유 | 세금(원) |"
    divider = "|---|" + "---|" * (len(starts) * 2 + 3)
    lab.write(header)
    lab.write(divider)

    for label, params in cases:
        cells = []
        sells = held = tax_krw = None
        failed = None
        for name, start in starts.items():
            try:
                strategy = (
                    params
                    if isinstance(params, (MomentumDcaStrategy, BucketDcaStrategy))
                    else BucketDcaStrategy(params=params)
                )
                row = lab.run_case(strategy, start, tax=tax)
            except Exception as exc:  # noqa: BLE001 - one bad case must not stop the sweep
                failed = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
                cells.extend(["오류", "오류"])
                continue
            cells.extend([_fmt_pct(row["twr"]), _fmt_pct(row["mdd"])])
            sells, held, tax_krw = row["sells"], row["held_final"], row["tax_krw"]
        note = f" — {failed}" if failed else ""
        lab.write(
            f"| {label} | " + " | ".join(cells) +
            f" | {sells if sells is not None else '-'}"
            f" | {held if held is not None else '-'}"
            f" | {_fmt_krw(tax_krw)}{note} |"
        )


def write_benchmarks(lab, starts):
    """The no-strategy curves, printed once, above everything else.

    A table of strategy variants with no baseline on the page invites reading
    the best row as good. In this window simply buying the universe returned
    about 29% a year, so most of what any row shows is the decade, not the
    rule being tested.
    """
    lab.write("")
    lab.write("### 0. 기준선 — 전략을 안 썼다면")
    lab.write("")
    lab.write(
        "아래 모든 표는 이 숫자들과 비교해서 읽어야 합니다. "
        "**같은 배분 균등 DCA**가 진짜 비교 대상입니다 — 안전자산 비중까지 "
        "전략과 똑같이 맞춘 무전략 곡선입니다."
    )
    lab.write("")
    header = "| 기준선 | " + " | ".join(
        f"{name} TWR | {name} MDD" for name in starts
    ) + " |"
    lab.write(header)
    lab.write("|---|" + "---|" * (len(starts) * 2))

    labels = None
    rows = {}
    for name, start in starts.items():
        stats = lab.benchmarks(start)
        if labels is None:
            labels = [k for k, v in stats.items() if v is not None]
        for label in labels:
            rows.setdefault(label, []).extend(
                [_fmt_pct(stats[label]["twr_cagr"]), _fmt_pct(stats[label]["mdd"])]
            )
    for label in labels or []:
        lab.write(f"| {label} | " + " | ".join(rows[label]) + " |")


def main(argv=None):
    parser = argparse.ArgumentParser(description="전략 비교 실험실")
    parser.add_argument("--out", default="docs/STRATEGY_LAB.md")
    parser.add_argument("--group", default=None, help="번호 또는 제목 일부")
    parser.add_argument("--starts", default="2014,2016,2018,2020,2022")
    parser.add_argument("--no-tax", action="store_true")
    parser.add_argument(
        "--round-two", action="store_true", help="조합·강건성 묶음만 돌립니다"
    )
    parser.add_argument(
        "--round-three", action="store_true", help="3차 조합 묶음만 돌립니다"
    )
    parser.add_argument(
        "--round-four", action="store_true", help="4차 (무매도 변형) 묶음만 돌립니다"
    )
    parser.add_argument(
        "--finalists", action="store_true", help="최종 3안 head-to-head"
    )
    parser.add_argument(
        "--mdd40", action="store_true", help="낙폭 40% 예산 안에서 수익 최대화"
    )
    parser.add_argument(
        "--ratio", action="store_true", help="낙폭당 수익 최대화 (권장 목적함수)"
    )
    parser.add_argument("--refine", action="store_true", help="선두 설정 주변 정밀 탐색")
    parser.add_argument(
        "--contribution", type=int, default=CONTRIBUTION_KRW, help="월 적립액(원)"
    )
    args = parser.parse_args(argv)

    starts = {k: STARTS[k] for k in args.starts.split(",") if k in STARTS}
    lab = Lab(args.out, contribution_krw=args.contribution)
    if args.contribution != CONTRIBUTION_KRW:
        lab.write("")
        lab.write(f"> 월 적립액 {args.contribution:,}원으로 실행한 표입니다.")

    if not args.group:
        write_benchmarks(lab, starts)

    if args.refine:
        groups = build_refine()
    elif args.ratio:
        groups = build_ratio()
    elif args.mdd40:
        groups = build_mdd40()
    elif args.finalists:
        groups = build_finalists()
    elif args.round_four:
        groups = build_round_four()
    elif args.round_three:
        groups = build_round_three()
    elif args.round_two:
        groups = build_round_two()
    else:
        groups = build_groups()
    if args.group:
        groups = [g for g in groups if args.group in g[0]]

    for title, why, cases in groups:
        run_group(lab, title, why, cases, starts, tax=not args.no_tax)

    lab.write("")
    lab.write(f"_이 절은 `python -m scripts.strategy_lab` 이 {date.today()}에 생성했습니다._")
    return 0


if __name__ == "__main__":
    sys.exit(main())
