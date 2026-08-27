# 작업일지

프로젝트 진행 기록. 최신 항목이 위에 옵니다.

---

## 2026-08-27 — 리포트: 보유 종목별 뉴스

리포트의 "📰 Economic News"가 `config.yaml`에 손으로 적은 매크로 키워드(반도체·환율)만 검색했다. 실제 보유 종목 관련 기사는 없었다.

`NewsFetcher`는 이미 키워드별 섹션을 처리할 수 있었고(`news_data["keywords"]`), Notion 렌더링도 키워드마다 `News: {keyword}` 서브헤딩을 이미 그리고 있었다 — 빠진 건 "보유 종목 이름을 키워드에 넣어주는 것"뿐이었다.

`src/news.py`에 `portfolio_keywords(snapshot)` 추가 — 보유 포지션의 표시 이름(티커가 아니라 이름: "005930"보다 "삼성전자"가 Google News에서 훨씬 잘 걸림)을 중복 없이 뽑는다. `main.py`가 이걸 `config.news_keywords`에 이어붙여 `NewsFetcher`에 넘긴다.

**검증**: 실제 계좌로 실행 — IONX(2x IonQ ETF), TSLL(2x TSLA) 둘 다 관련 기사가 잡혔다(TSLL은 테슬라 주주 구조 기사, IONX는 디파이언스 ETF 기사). 신규 테스트 4개 포함 전체 324개 통과.

**다음 후보**: AI Analyst 프롬프트(`src/analyst.py`)는 아직 `news_data["general"]`만 보고 종목별 뉴스는 안 읽는다 — 원하면 같이 넣을 수 있음.

---

## 2026-08-27 — 매매전략 Opus 재검토 반영 (LIVE 전 필수 6건)

전략을 실거래에 붙이기 전 Opus 어드바이저로 한 번 더 검토했다. "반드시 고칠 것" 6건 중 4건 수정, 1건은 문서화로 결론, 1건은 검증 과제로 남김.

### 1. 디슬로케이션 쿨다운이 라이브에서 죽어 있었다

급락 시 예산 2배 매수("디슬로케이션") 후 5일 쿨다운을 거는 로직이, 라이브에서는 마지막 디슬로케이션 매수를 **한 번도 인식하지 못했다.** 라이브 스토어(`store.repo.save_decision`)는 `signal.meta`를 dict 그대로 `payload` 키에 저장하는데, `_entry_meta`는 그걸 JSON 문자열로 보고 `json.loads(dict)` → TypeError → `{}`로 떨어뜨렸다. 백테스트는 `meta` 키를 써서 이 경로를 안 탄다 — 즉 백테스트가 검증하던 코드와 라이브가 실행하던 코드가 달랐다. `payload`가 이미 dict면 그대로 반환하도록 수정.

### 2. `ctx.recent` 크기가 백테스트/라이브 불일치

라이브는 `store.recent_signals(limit=50)`, 백테스트는 무제한 `recent_log`를 넘겼다. 쿨다운의 "로그가 충분히 안 옛날까지 닿으면 fail-closed" 분기는 50개 창에서만 의미가 있는데, 백테스트에선 절대 안 걸렸다. 백테스트도 `recent_log[-50:]`로 맞춤.

### 3. 추세 이탈 청산이 매일 중복 발행

`_exit_signals`는 매 실행마다 백지에서 다시 판단한다. 하락 추세가 며칠 이어지면 매일 `position.quantity` 전량 매도를 새로 냈다. 라이브는 T+1~T+2 정산 지연 동안 포지션·매도가능수량이 그대로라, 리스크 게이트도 이 중복을 못 막는다. `exit_cooldown_days`(기본 3일) 파라미터와 `_exit_in_flight()` 가드 추가 — 최근 청산 신호가 승인된 적 있으면 재발행 억제. `ctx.recent`가 순수 전략이 읽을 수 있는 유일한 in-flight 상태다.

### 4. 리밸런스 요일이 KST 벽시계 기준

`ctx.now.date().weekday()`로 "월요일 리밸런스"를 판정했다. 미국장 마감은 KST 화요일 새벽 — 마감 후 배치가 돌면 이미 화요일이라 그 주 리밸런스를 **통째로 건너뛴다.** `today`를 `benchmark_history.last_date`(= 미국 세션 날짜)에 고정. 백테스트에선 `ctx.now`의 날짜와 항상 같아 무변화. `now`를 데이터 마지막 봉과 분리해 놨던 모멘텀 테스트 ~8개를 마지막 봉이 실제로 월요일에 떨어지도록 재작성.

### 5. OCO 손절은 켜지 않는다 — 문서로 명시 (결론)

[9]에서 만든 reconciler의 OCO 등록은 `momentum-dca` 매수에 대해 **한 번도 실행되지 않는다.** 전략이 `stop_loss_price`/`take_profit_price`를 신호에 안 싣기 때문. 손절 폭 자체가 계획서 8단계 백테스트가 정해야 할 숫자라, 검증 안 된 값(예: -15%)을 넣으면 손실을 막기보다 멀쩡한 포지션을 조기 청산할 위험이 더 크다. 지금은 켜지 않고, 현 상태를 세 곳에 분명히 적었다:

- `momentum_dca.py` 모듈 docstring — "per-position 손절 없음, 추세 필터가 유일한 하방 방어, 봇이 돌 때 + 벤치마크 전체 추세가 꺾일 때만 작동" + "8단계 나오면 재검토"
- `reconciler.py` 모듈 docstring — "'리컨실러가 OCO를 건다'를 '라이브 매수가 손절로 보호된다'로 읽지 말 것 — 현재는 아니다"
- `_arm_bracket` early-return 주석

### 6. USD 매수 여력 — 검증 과제 (미해결)

`_budget`이 `ctx.buying_power["USD"]`를 읽는데, 토스 계좌가 원화만 보유하고 자동 환전이 안 되면 예산이 영원히 0 → 전략이 조용히 아무 주문도 안 낸다. 실제 잔고로 PAPER 1회 실행해 확인 필요. 로그인 정보가 있어야 해서 이번엔 못 함.

**검증**: 신규 3개 포함 전체 315개 통과.

### 다음

- [10] 전에 실계좌로 `python trade.py` 1회 — `ctx.buying_power`에 USD가 잡히는지 확인
- 8단계 백테스트에서 손절 폭 결정 후 OCO 재검토

---

## 2026-08-27 — UI: 대시보드 폭 + 수동 보유분 당일 손익

### 1. Overview가 화면을 안 채움

`main` 안쪽 컨테이너가 `max-w-container-max`(1600px) + `mx-auto`라서 와이드 모니터에서 양옆이 비었다. `w-full`로 교체. 내부 그리드는 원래 `xl:grid-cols-*`라 그대로 늘어난다. `docs/ui/mockup.html`도 동일하게.

### 2. Today's P&L이 수동 보유분에서 항상 +0

원인: 현재 포트폴리오(IONX, TSLL)는 전부 `config.yaml`의 수동 입력. 토스 `/api/v1/holdings`는 `items: []`. 토스는 자기 계좌 종목엔 `dailyProfitLoss`를 계산해 주지만, 수동 보유분은 `ManualSource`가 그 필드를 안 채웠고 채울 수도 없었다 — 토스 시세 API(`/api/v1/prices`)가 `lastPrice`만 주고 전일 종가를 안 준다.

옵션 B(yfinance 전일 종가)로 해결:

- `ManualSource(previous_closes={symbol: Decimal})` 주입 → 라이브 시세가 있을 때 `qty × (last − prev)`로 `daily_profit_loss`/`_rate` 산출. 없으면 `None`(0 아님, 집계에서 제외).
- `PortfolioService(previous_close_fn=...)` 훅. 호출 실패는 전부 `except Exception`으로 삼켜서 스냅샷·대시보드가 절대 안 죽음. **대시보드에만** 연결(리포트·매매 경로는 그대로).
- `src/dashboard/service.py`의 `_make_previous_close_fn()` — 공유 Firestore 봉 캐시(`BarCache`) 우선, 부족분만 yfinance(`staleness_days=1`). 지연 생성이라 import 시점엔 yfinance도 Firestore도 안 건드림. `_exchange_today()`는 뉴욕 날짜 기준(KST 저녁에 "오늘"이 하루 빨리 넘어가 0 되는 것 방지). 전일 종가 = 뉴욕 오늘보다 이전인 마지막 완료 봉.

### 3. `BarCache.bars`가 복합 인덱스를 요구하던 문제

`where(symbol ==) + where(date 범위) + order_by(date)`는 Firestore 복합 인덱스가 필요한데 `quant-81f19` 프로젝트에 없었다(서비스 계정은 인덱스 생성 권한도 없음). `symbol` 등호만 쿼리로 보내고 날짜 범위·정렬은 파이썬에서 — `store.repo`가 자기 범위 스캔을 처리하는 방식과 동일. 이제 복합 인덱스 없이 동작하고, 전략 히스토리 로더도 같이 덕을 본다.

**검증**: `yfinance` 설치 후 실데이터로 확인 — IONX +497 USD(+6.3%), TSLL +53 USD(+0.7%), 당일 합계 **+759,511 KRW (+3.5%)**. 전체 320개 테스트 통과(신규 5).

---

## 2026-08-27 — Phase 2 [9]: reconciler + OCO 손절

체결을 아무도 안 읽는 상태였다. `python trade.py --reconcile`로 미체결 LIVE 주문의 체결을 확인하고, 매수 체결이 확인되면 OCO 손절을 등록한다. 설계 2.3이 "개인 자동매매에서 가장 중요한 기능"이라고 꼽은 부분 — 손절을 토스 서버에 위임해서 봇이 죽어도 손절이 산다.

### 1. 왜 executor가 아니라 reconciler가 OCO를 건다

`Signal.stop_loss_price`/`take_profit_price`는 [6]부터 자리만 잡혀 있었다. 자연스러운 자리는 executor일 것 같지만 — **주문을 낸 시점엔 아직 체결이 아니다.** 미체결 주문에 손절을 거는 건 의미가 없고, 부분체결이면 그 수량만큼만 걸어야 한다. "주문을 냈다"와 "주식을 실제로 가졌다"를 한 자리에서 섞으면 이 둘의 경쟁 상태를 감수해야 한다.

그래서 신호의 손절가가 주문 문서에 그대로 실려 저장되고(`save_order`가 `intent.signal`에서 복사), reconciler가 체결을 확인한 **다음에만** 등록한다.

### 2. 체결 여부를 상태 문자열이 아니라 수량으로 판정

토스가 실제로 어떤 상태 문자열을 쓰는지(`FILLED`? `COMPLETED`? `체결완료`?) 확인된 근거가 없다. 대신 **체결수량 vs 주문수량**을 비교해서 filled/partially_filled를 스스로 계산한다 — 숫자는 철자와 달리 애매할 일이 없다. 상태 문자열은 취소/거부처럼 수량 비교로는 알 수 없는 두 경우에만 보조로 쓴다("cancel"/"취소", "reject"/"거부" 부분 문자열 매칭).

체결수량은 브로커가 **누적치**로 준다고 가정했다. 그래서 두 번째 조회부터는 `이번 누적 - 저장된 누적`만 새 체결로 기록한다 — 그대로 두 번 적으면 체결이 두 배로 잡힌다. 테스트가 이 델타 계산을 직접 확인한다.

### 3. 없는 API를 또 지어낼 뻔했다

`unknown` 상태 주문(주문 시 토스가 준 `orderId`를 못 받은 경우)은 `get_order(orderId)`로 조회가 안 된다. `list_orders()`로 이력을 훑어 `clientOrderId`가 일치하는 항목을 찾는 경로를 새로 만들었다 — 이건 설계 3.1이 명시적으로 "Reconciler ← GET /orders (폴링)"라고 적어둔 엔드포인트라 [8]에서 피했던 것과는 다르다. 다만 정확한 쿼리 파라미터·응답 필드명은 여전히 미확인이라, 여러 철자를 시도하는 방어적 파싱은 그대로 유지했다.

### 4. 호가단위는 일부러 안 넣었다

한국 주식 호가단위는 가격대별 계단식(2천원 미만 1원 ~ 50만원 이상 1,000원)이고, 확인된 근거 없이 규칙을 지어 넣으면 **틀린 가격을 자신 있게 보내는** 꼴이 된다. 손절 주문가를 트리거보다 살짝 낮추는 슬리피지 계산(`oco_stop_loss_slippage`, 기본 0.5%)만 하고, 원/센트 단위로만 반올림한다. 틀리면 `invalid-tick-size`로 드러난다 — 조용한 오류보다 이름 붙은 거부가 낫다.

### 5. OCO 등록 실패는 실행을 막지 않는다

`idempotency-key-conflict`(executor 쪽)와 다르게, `duplicate-conditional-order` 같은 OCO 등록 실패는 **이 종목 하나의 문제**이지 ID 생성 로직이 깨졌다는 신호가 아니다. 그래서 `ExecutorBug`처럼 올리지 않고, 실패를 기록한 뒤 다음 주문으로 넘어간다. 체결 기록 자체는 이미 저장된 뒤라 잃지 않는다.

### 6. PAPER는 reconciler를 아예 안 만든다

`Reconciler.__init__`이 LIVE가 아닌 TradingApi를 거부한다. PAPER 주문은 브로커에 전송된 적이 없어 폴링할 대상이 없다 — 가짜 체결을 시뮬레이션하는 건 백테스트 엔진(다른 세션이 이미 만든 `src/backtest/`)의 몫이지 여기 몫이 아니다.

**`--live`는 여전히 거부된다.** reconciler와 OCO는 이제 있지만 설계 [10]이 요구하는 "최소 수량 1주 실거래 검증"은 별개 단계라 메시지만 바꿨다.

**검증**: 신규 33개 포함 전체 312개 통과.

### 다음

- [10] LIVE 개방 — 최소 수량 1주부터, `--reconcile`을 스케줄러에 별도 주기로 등록
- OCO 자체의 체결/취소 상태를 확인할 조회 엔드포인트가 없음 — 확인되는 대로 추가
- 호가단위 규칙 — 실제 API 응답으로 밴드를 확인하면 `_round_to_tick`을 정확한 계단식으로 교체

---

## 2026-08-26 — Phase 2 [6] 잔여분: 모멘텀 DCA 전략 + 백테스트 러너

첫 실전 전략을 채웠다: `src.strategy.momentum_dca:MomentumDcaStrategy`. 조건은
시드가 작고(월 50~100만원 적립), 공격적이되 규칙 기반 — 상위 1~2종목 모멘텀
집중 배분, 2배 지수 ETF까지만, 추세 필터로 레버리지만 게이트.

### 1. 콜드스타트 함정

`max_position_weight` 검사는 자산이 0일 때만 건너뛰었다. 그런데 전략의
**생애 첫 매수는 정의상 비중 100%** — 시드가 작은 이번 케이스에선 엣지 케이스가
아니라 1개월차에 바로 첫 주문이 거부된다. `weight_check_min_equity_krw` 면제선과
`max_position_weight_overrides`(종목별 예외) 두 개를 게이트에 추가해서 풀었다.
전역 기본은 0.20을 그대로 두고, 집중을 허용할 종목만 config에 명시한 사실로
남긴다 — 코드가 아니라 config가 그 예외를 갖고 있어야 조용히 넓어지지 않는다.

### 2. `StrategyContext`에 필드 두 개만 추가

`history`(symbol → PriceHistory)와 `recent`(최근 신호 로그) — 둘 다 기본값과
함께 뒤에 붙여서 `risk.py`를 포함한 기존 생성 지점은 전부 그대로 돈다. 지표는
전략이 이 데이터로 직접 계산하고(`src/strategy/indicators.py`, stdlib+Decimal),
컨텍스트에 조회 가능한 콜백을 두지 않았다 — 콜백은 이름만 다른 I/O 탈출구다.

### 3. 레버리지 정책은 조건문이 아니라 `Instrument.__post_init__`

3배 이상, 인버스, 개별종목 레버리지 ETF(NVDL·TSLL류)는 유니버스에 넣는 순간
예외가 난다. config에서 읽은 값도 같은 생성자를 거치므로 정책이 코드에 있지
"조심하기로 한 약속"으로 남지 않는다.

### 4. 백테스트는 진짜 `RiskGate`를 문자 그대로 통과한다

`SimPortfolio.snapshot()`이 진짜 `PortfolioSnapshot`/`Position`을 만들기
때문에 — 목이 아니라. 리밸런스는 주 1회(월요일) 기본, 급락(−3%대 하락 +
20일 낙폭 8%+ + 쿨다운)에만 일 단위 개입. `TWR`(전략 성과)과
`IRR`(계좌 실현 성과, XIRR)을 둘 다 보고한다 — 매월 적립이 있으면 둘이 크게
갈라지고, 하나만 "CAGR"이라고 부르면 둘 중 하나는 틀린 질문에 답한 게 된다.
MDD는 원자산이 아니라 TWR 인덱스 기준 — 안 그러면 매월 입금이 낙폭을 가린다.

`python -m scripts.backtest --refresh`로 yfinance 시세를 SQLite에 캐시하고,
`--offline`이면 그 캐시만 쓴다 — 재현성이 목적이라 오프라인 모드는 구멍이
있어도 절대 네트워크를 부르지 않는다.

### 다음

- `config.yaml`에 `trading.universe`/`strategy_params`/`max_position_weight_overrides`를
  채우고 `python -m scripts.backtest --refresh --offline`로 인/아웃오브샘플
  검증 (파라미터가 ~20개라 과최적화 위험이 실재함 - 시도한 조합은 여기 기록할 것)
- PAPER로 몇 주 돌리며 같은 날짜의 백테스트 재생과 신호를 diff — 불일치는
  순수성 버그
- 개별 손절매는 이번에 파라미터만 두고 OFF — 백테스트로 넣을지 결정

## 2026-08-26 — Phase 2 [8]: PAPER 모드 주문 실행

`python trade.py`로 신호 → 리스크 게이트 → DB까지 한 번에 흐른다. **HTTP는 한 건도 나가지 않는다.**

### 1. 캘린더 파서를 공유 모듈로 — `src/toss/calendar.py`

새로 쓰지 않았다. `dashboard/service.py`에 이미 시장별로 제각각인 응답을 재귀로 훑는 파서가 있었고, 리스크 게이트가 필요로 하는 답이 정확히 같은 것이었다. 4개 함수를 `toss/calendar.py`로 올리고 대시보드는 import만 바꿨다.

새로 추가한 건 `regular_window()` 하나다. 기존 `live_session()`은 "지금 어느 세션인가"만 돌려주고 **시각을 버린다.** 게이트의 "정규장 종료 1시간 전" 규칙은 종료 시각 자체가 필요하다.

> **타임존이 조용한 지뢰였다.** `parse_dt`는 aware datetime을 준다. `ctx.now`가 naive면 `ctx.now >= cutoff`에서 `TypeError`가 난다 — 그것도 *금액 주문이 마감 1시간 안에 들어왔을 때만*. 컨텍스트 빌더가 `datetime.now().astimezone()`으로 양쪽을 맞추고, 테스트가 이 성질을 못박는다.

### 2. `client_order_id` — 시퀀스를 DB에서 세면 안 된다

처음엔 `Store.next_order_seq()`가 그날 그 전략·종목의 주문 행을 세서 +1 하도록 짰다. **테스트가 바로 잡아냈다.**

배치를 두 번 돌리면 두 번째 실행은 행이 1개 있으니 seq=2를 받는다 → **새 ID → 재발주.** 결정론적 ID가 존재하는 이유를 정확히 반대로 뒤집은 것이다.

시퀀스는 **런 내부의 순서**여야 한다. executor가 메모리에 카운터를 들고 매 실행 0에서 시작한다. 같은 배치는 같은 ID를 만들고, store가 그걸 알아본다. 이건 **전략이 순수하다는 성질에 기댄다** — 같은 컨텍스트면 같은 신호가 같은 순서로 나온다. `next_order_seq`는 지웠다.

### 3. PAPER의 이중 안전장치

`build_trading_api`가 PAPER에는 **`allow_write=False` 클라이언트**를 준다. executor의 모드 분기가 깨져 `place_order`가 호출돼도 `TossClient.request()`가 `TossWriteBlockedError`를 던진다. 안전 성질이 분기 하나의 정확성에 걸려 있지 않고 **한 층 아래, HTTP를 실제로 보내는 자리에서** 강제된다. 테스트가 이걸 직접 증명한다.

`OrderExecutor`는 자기 모드와 `TradingApi`의 모드가 다르면 생성 자체를 거부한다. 둘이 어긋날 수 있으면 그게 설계 7절이 말하는 PAPER/LIVE 혼동이다.

### 4. 에러 정책 — 거의 아무것도 재시도하지 않는다

핵심은 재시도 목록이 아니라 **재시도하지 않는다는 기본값**이다. 거부는 계좌나 시장에 대한 정보이고, 같은 요청을 반복하면 같은 거부를 레이트리밋 비용만 더 써서 받는다.

예외는 둘뿐이다.

- **`price-out-of-range`** — 밴드 안으로 당겨서 **딱 1회**. 밴드는 에러 봉투에서 먼저 찾고, 없으면 게이트가 이미 읽어둔 것을 쓴다. **둘 다 없으면 재시도하지 않는다** — 지어낸 가격으로 재전송하는 건 실패보다 나쁘다
- **`request-in-progress`** — 재전송은 포지션을 두 배로 만들 수 있으니 불가. 그런데 조회도 불가능하다: `GET /orders/{orderId}`는 토스가 부여한 ID가 필요한데 그게 바로 못 받은 것이다. 확인된 엔드포인트 목록(설계 2.2)에 clientOrderId로 찾는 경로가 없어서 **없는 API를 지어내지 않았다.** `unknown`으로 남기고 [9] reconciler에 넘긴다

`idempotency-key-conflict`는 **예외로 올려 실행을 중단**한다. 같은 ID에 다른 내용이 실렸다는 건 ID 생성이 깨졌다는 뜻이고, 그렇다면 그 뒤 모든 주문의 멱등성을 믿을 수 없다.

**모르는 에러 코드도 terminal로 취급한다.** 인식하지 못한 에러는 안전하게 재시도할 만큼 이해한 에러가 아니다.

### 5. 조회 실패는 통과가 아니라 거부 — `src/execution/context.py`

리포트 파이프라인과 **반대 방향**이다. 거기서는 조회 실패가 리포트를 degrade시킨다(`_collect_warnings`가 종목을 건너뛴다). 여기서는 실패한 필드를 **비워두고**, strict 모드가 그걸 "확인 불가"로 읽어 거부한다.

특히 주문가능금액이 None일 때 **0으로 채우지 않는다.** 0은 "현금 없음"이라는 틀렸지만 살아남는 답이고, 진짜 답은 "모름"이다.

레이트리밋: 종목별 `sellable-quantity`는 보유 종목당 1회만 돌고 컨텍스트에 캐시된다(`ORDER_INFO`는 개장 직후 3 TPS). `account_seq`도 `PortfolioService`가 이미 해석한 것을 재사용한다 — `ACCOUNT`는 1 TPS다.

### 6. `--live`는 만들었지만 열지 않았다

플래그를 받으면 배너가 아니라 **거부하고 종료한다**(exit 4). reconciler([9]) 없이는 제출한 주문이 실제로 어떻게 됐는지 알 방법이 없고, 대사되지 않은 실주문은 추적 불가능한 포지션이다. 설계 [10]이 별도 단계인 이유다.

`trading.enabled`는 기본 false. 매매 코드가 트리에 있다는 사실만으로 주문이 나가면 안 된다.

**검증**: 신규 48개 포함 전체 195개 통과. 네트워크 접근 0 — e2e 테스트가 실제로 토스 토큰 엔드포인트를 때리는 걸 한 번 잡아서 `build_trading_api`까지 스텁했다.

### 다음

- [9] reconciler — `GET /orders` 폴링으로 `fills` 채우기. `unknown` 상태 주문의 정산도 여기서
- [9] OCO 조건주문 — `Signal.stop_loss_price`/`take_profit_price`는 자리를 잡아뒀고 아직 읽지 않는다. 손절을 토스 서버에 위임하는 것이 설계가 꼽은 가장 중요한 기능
- [10] LIVE 개방 — 최소 수량 1주부터
- 전략 작성 (사용자)

---

## 2026-08-26 — Phase 2 [6][7]: 전략 레이어와 리스크 게이트

주문 코드는 아직 한 줄도 없다. 설계 6절이 `execution/risk.py`를 `toss/trading.py`보다 **먼저** 두라고 한 이유 그대로다 — 주문이 먼저 돌기 시작하면 "안전장치는 나중에"가 너무 쉬워진다. 이번 커밋 시점에서 리스크 게이트는 우회할 주문 경로 자체가 없다.

### 1. `src/strategy/base.py` — 제안, 아직 주문이 아니다

`Signal`은 전략의 *제안*이다. 잔고도, 장 운영시간도, 가격 밴드도 여기서는 보지 않는다. 그건 리스크 게이트의 일이고, 둘을 갈라놓아야 전략을 **리스크 규칙을 모른 채** 쓸 수 있다.

`Signal.__post_init__`에서 막는 것들:

| 규칙 | 이유 |
|---|---|
| `quantity` XOR `amount` | API가 둘 다 실린 body를 거부한다. 둘 중 **엉뚱한 쪽으로 조용히 체결되는 것**이 여기서 죽는 것보다 나쁘다 |
| LIMIT에 `limit_price` 필수 | 가격 없는 지정가는 주문이 될 수 없다 |
| `reason` 필수 (공백 불가) | 감사 로그가 존재하는 이유가 "왜 샀는가"다. 사후에 차트 보고 재구성한 이유는 사실이 아니라 이야기다 |

`Strategy`는 client도 store도 config도 받지 않는다. **네트워크에 닿을 수 없는 전략은 실수로 주문을 낼 수 없고, 테스트에서와 운영에서 다르게 동작할 수도 없다.** 같은 `evaluate`를 과거 컨텍스트에 그대로 흘리면 그게 백테스트다.

`StrategyContext` 하나를 전략과 리스크 게이트가 **공유**한다. 같은 순간의 같은 세계를 읽으므로 쪼개봐야 API 응답 한 벌로 객체 두 개를 만드는 일밖에 안 된다. 전략은 계좌 필드를 그냥 안 볼 뿐이다.

### 2. `src/execution/risk.py` — 유일한 `OrderIntent` 생산자

`OrderIntent`는 `RiskGate`만 만든다. 승인된 신호는 `OrderIntent`로, 거부된 신호는 **규칙 이름이 붙은 `Rejection`**으로 나온다.

게이트는 **I/O를 하지 않는다.** 주문가능금액·매도가능수량·장 운영시간·오늘 사용량은 호출자가 읽어서 컨텍스트에 실어준다. 덕분에 모든 규칙이 인자의 함수이고, 브로커를 목킹하지 않고 컨텍스트만 만들어 테스트한다. 유일한 예외인 킬 스위치 파일 확인은 자유 함수 `kill_switch_active()`로 빼뒀다.

규칙은 **싸고 절대적인 것부터** 돈다. 킬 스위치 → 일일 건수 → 소수점 수량 → 세션 → 가격 밴드 → 금액 산정 → 예산/비중 → 잔고 → 고액. 거부는 **첫 번째 규칙 하나만** 이름을 댄다 — 목록이 아니라, 고쳐야 할 그 하나다.

설계 3.3표에서 옮겨온 것들 중 판단이 필요했던 부분:

- **비중 한도는 "주문이 만들어낼 포지션"에 건다.** 주문 하나만 보면 작은 매수 열 번이 큰 포지션 하나를 조용히 쌓는다. 매도는 비중을 줄이기만 하므로 검사하지 않는다.
- **고액 주문(1억↑)의 `confirmHighValueOrder`를 자동으로 켜지 않는다.** 1억을 통과시킬 수 있는 봇은 버그 하나 거리에 있다. `allow_high_value`를 사람이 명시적으로 켠 경우에만 통과한다.
- **금액 지정·소수점 수량은 정규장 종료 1시간 전이 마감이다.** 장이 열려 있어도 거부된다(설계 2.1). 주식 수 주문은 영향 없다.
- **`RiskLimits.strict`**(기본 True) — 검증할 데이터가 없을 때의 태도. 시세도 캘린더도 주문가능금액도 없으면 **거부**한다. 확인 못 한 주문은 안전한 주문이 아니고, 422로 알게 되는 건 설계 3.3-5가 하지 말라는 바로 그것이다. 참조 데이터가 아직 안 붙은 백테스트·초기 PAPER 실행은 `strict=False`로 확인 가능한 것만 본다. 이 모드에서도 킬 스위치와 일일 한도는 그대로 산다.

`client_order_id`는 게이트가 비워둔다. 일일 시퀀스는 [8]의 executor에 있다.

### 3. 스키마 — `signals` / `rejections` / `orders` / `fills`

거부된 신호도 승인된 신호와 **같은 테이블**에 들어간다. "전략은 사고 싶어 했고 리스크 게이트가 막았다, 규칙은 이것이다"가 그날 하루를 설명하는 대부분이고, 안 적어두면 복구가 불가능하다.

`orders.notional_krw`는 비정규화해서 들고 있다. 일일 금액 한도를 `SUM` 한 번으로 끝내기 위해서다 — 재계산하려면 그날의 환율을 되찾아와야 한다.

**검증**: 신규 51개 포함 전체 147개 통과. 네트워크 접근 0.

### 다음

- [8] `toss/trading.py` + executor — PAPER 모드만. `RiskLimits`를 config에 배선하는 것도 여기서 (지금 넣으면 읽는 곳이 없는 설정이 된다)
- 결정론적 `client_order_id` 발급과 일일 시퀀스
- [9] reconciler + OCO 조건주문

---

## 2026-08-20 — 일일 자동 실행 + Gemini SDK 이관

### 1. Windows 작업 스케줄러 등록

`run_report.bat`을 그대로 등록하면 안 됐다. 마지막 줄이 `pause`라 스케줄러가 띄운 작업이 키 입력을 기다리며 영원히 끝나지 않고, 다음 날 실행은 `MultipleInstances` 규칙에 막힌다. 배치를 두 갈래로 나눴다.

| 호출 | 동작 |
|---|---|
| `run_report.bat` | 콘솔에 출력하고 `pause` — 손으로 실행할 때 |
| `run_report.bat --scheduled` | `logs\report_YYYY-MM-DD.log`에 기록하고 즉시 종료 |

배치가 `PYTHONIOENCODING=utf-8`을 지정한다. 리다이렉트된 stdout은 콘솔 코드페이지(cp949)를 따라가서, 종목명 한 글자에 로그가 통째로 깨진다. `%DATE%`도 로캘마다 형식이 달라 파일명에 못 쓰므로 PowerShell로 형식을 고정했다.

등록 설정과 근거:

| 설정 | 이유 |
|---|---|
| 매일 10:00 | 미국장 마감(서머타임 06:00 KST) 이후 + 사용 시작(09:00) 이후. 아래 `Interactive` 항목과 묶어서 읽어야 한다 |
| `StartWhenAvailable` | PC가 꺼져 있어 놓친 실행을 다음 부팅 때 따라잡는다. 스냅샷은 소급 생성이 불가능하다 |
| `MultipleInstances=IgnoreNew` | 토스는 client당 유효 토큰이 1개다. 겹쳐 돌면 서로를 로그아웃시킨다 |
| `ExecutionTimeLimit` 30분 | 외부 API가 응답을 안 할 때 무한정 매달리지 않는다 |
| `LogonType=Interactive` | 비밀번호를 저장하지 않는다. 대신 **로그온 상태에서만** 돈다 — 그래서 트리거를 사용 시간대(09:00~) 안쪽인 10:00로 잡았다. 처음엔 08:00로 뒀는데, 그 시각엔 로그온 전이라 매일 `StartWhenAvailable` 따라잡기에 의존하게 된다 |

**검증**: 스케줄러로 실제 1회 실행 → `LastTaskResult=0`, 로그 정상, 스냅샷 2행째 적재(24,573,137원, −29.05%), Notion 리포트 생성. 배치 양쪽 분기와 exit code 전파(7 → 7)는 파이썬 호출만 스텁으로 바꿔 따로 확인했다. `logs/`는 gitignore에 추가.

### 2. `google-generativeai` → `google-genai`

구 SDK는 지원이 종료돼 import할 때마다 FutureWarning을 뿜었다. 모델도 `gemini-1.5-flash`로 이미 폐기된 세대였다.

| | 이전 | 이후 |
|---|---|---|
| 패키지 | `google-generativeai` (지원 종료) | `google-genai` 2.19.0 |
| 호출 | `genai.GenerativeModel(...).generate_content(p)` | `client.models.generate_content(model=..., contents=p)` |
| 모델 | `gemini-1.5-flash` | `gemini-3.7-flash` |

**`thinking_level`을 `low`로 고정했다.** Gemini 3.x는 답하기 전에 생각하고 기본값이 `medium`이다. 세 문단짜리 일일 요약에 그만큼 쓸 이유가 없고, 매일 도는 배치라 비용·지연이 그대로 누적된다. `config.yaml`의 `google_ai.thinking_level`로 올릴 수 있고, `"off"`면 필드 자체를 안 보낸다(`minimal`은 3.7이 거부하므로 우회로 대신 미전송).

`response.text`가 `None`일 수 있다(안전성 차단 등). 그대로 반환하면 리포트에 `"None"`이 박히므로 명시적 문구로 대체했다.

Interactions API(신규 GA)로 가지 않았다. `generate_content`는 "legacy"로 분류됐을 뿐 **여전히 완전 지원**이고, 단발성 프롬프트 하나에 실행 스텝 구조를 파싱할 이유가 없다. 신규 모델·기능이 Interactions에 먼저 붙으므로 Phase 2에서 재검토한다.

`scripts/smoke_test.py`에 **7) Gemini** 단계를 추가했다. 토큰 한 개짜리 질문을 실제로 던진다 — 키가 틀리거나 모델명이 폐기되면 리포트 안에 사과 한 줄로만 남아 놓치기 쉽다.

**검증**: 테스트 83개 통과, `google.generativeai`가 `sys.modules`에 없음을 단언, 스케줄 실행 1회 정상 완료.

### 3. `GOOGLE_AI_API_KEY` 입력 + 그 과정에서 잡은 버그 2개

키를 채우고 `scripts/smoke_test.py` 7번 단계 통과(`generate_content: OK`). 실제 포트폴리오·뉴스로 드라이런(저장 없음)까지 돌려 보니 AI 섹션이 5.2초/약 1,000자로 생성된다. 그런데 그 출력에서 두 가지가 드러났다.

**① 종목명이 티커로만 나가서 AI가 다른 회사를 분석했다.**

`/api/v1/stocks`는 미국 종목의 `name`에 티커를 그대로 돌려주고, 실제 이름은 `englishName`에 담는다(한국 종목만 `name`에 한글명이 있다). 코드가 `name`만 읽고 `englishName`을 버려서 프롬프트에 `IONX`가 맨 티커로 들어갔다.

그 결과 첫 드라이런에서 모델이 IONX를 **IonQ 보통주**로 읽고 "지지선 확인 후 반등 시 일부 현금화" 같은, 보통주에나 맞는 조언을 냈다. 실제로는 `DEFIANCE DAILY TARGET 2X LONG IONQ ETF` — **2배 레버리지 ETF**다. 같은 답변에서 TSLL은 티커가 널리 알려진 덕에 음의 복리 효과를 정확히 지적했다. 즉 모델이 아는 티커냐 아니냐에 따라 조언 품질이 갈리고 있었다.

`src/models.py`에 `display_name()`을 두고 두 소스가 함께 쓰게 했다. `name`이 비었거나 심볼과 같으면 `englishName`으로 내려간다 — 한글명이 있으면 그대로 이긴다. 수정 후 재실행하니 모델이 두 종목 모두를 2배 레버리지로 인식하고 양쪽에 변동성 잠식을 경고한다.

> 포트폴리오가 우연히 전부 미국 ETF라 전 종목이 이 경로였다. 대시보드·Notion 리포트의 표시 이름도 같이 고쳐진다.

**② SDK가 매 실행 로그에 쓰지도 않는 기능의 경고를 남겼다.**

`google-genai`는 tools를 안 넘겨도 자동 함수 호출(AFC)을 기본 활성화하고, `Models.generate_content` 직접 호출에 대해 "AFC는 Chat.send_message를 쓰라"는 경고를 로거에 남긴다. 이 프로젝트는 산문 한 덩어리를 받을 뿐 도구를 선언하지 않으므로 `automatic_function_calling.disable=True`로 껐다. 스케줄 실행 로그에 매일 쌓일 잡음이었다.

`_config()`가 이제 항상 config를 반환한다(thinking이 꺼져 있어도 AFC 설정은 실려야 한다).

**검증**: 테스트 86개 통과(신규 3개 — `englishName` 폴백, 한글명 우선, 심볼 폴백).

### 4. 상품 유형을 프롬프트에 실었다

①의 수정은 이름이 길고 설명적이라 통했을 뿐이다(`... 2X LONG IONQ ETF`). 이름이 짧거나 애매한 상품이 들어오면 모델은 다시 티커로 추측한다. 이름에 기대는 대신 **토스가 이미 내려주는 메타데이터**를 그대로 넘기기로 했다.

`/api/v1/stocks` 응답에 `securityType`("ETF")과 `leverageFactor`("2")가 들어 있는데 둘 다 버려지고 있었다. `Position`에 두 필드를 추가하고 `instrument` 프로퍼티로 렌더한다 — 배수가 음수면 인버스로 읽는다. DB 스키마는 건드리지 않았다(정적 참조 데이터이고, `position_snapshots`는 컬럼을 명시적으로 나열해 쓴다).

프롬프트의 보유 종목 줄이 이렇게 바뀐다:

```
- DEFIANCE DAILY TARGET 2X LONG IONQ ETF [IONX] - 2x leveraged (daily reset) ETF, US: 325 shares, Profit: -22.77%, ...
```

여기에 지시를 하나 붙였다: **주석을 신뢰하고 티커나 이름으로 상품을 추측하지 말 것, 레버리지·인버스 ETF를 기초자산 주식처럼 분석하지 말 것.**

재실행하니 모델이 "2배 레버리지 일일 리셋 ETF"로 먼저 규정하고 양쪽에 변동성 잠식을 설명한 뒤, 기초자산(IonQ·Tesla)은 기초자산으로만 언급한다. ①이 이름 덕에 우연히 맞힌 것을, 이번 변경은 데이터로 보장한다.

**검증**: 테스트 88개 통과(신규 2개 — 레버리지 ETF 라벨링, 인버스·무레버리지 렌더링).

### 참고

구 `google-generativeai` 패키지는 Windows Python에서 **제거하지 않았다**. 이제 아무도 import하지 않아 경고가 뜨지 않고, 같은 인터프리터를 쓰는 다른 프로젝트를 깨뜨릴 이유가 없다.

---

## 2026-08-19 — Phase 1: 토스 Open API 전환 + SQLite + 대시보드

### 배경

기존 v1은 보유 수량·평단을 전부 `config.yaml`에 수기 입력하고 yfinance로 시세만 가져오는 반자동 방식이었다. 산출물은 Notion 리포트 한 장뿐이고 저장소가 없어 "어제 대비"조차 계산할 수 없었다.

토스증권 Open API가 국내(KRX)+미국 주식을 단일 REST API로 제공하면서, 키움 API를 폐기했던 세 가지 이유(①국내/해외 API 파편화 ②32비트 Python 강제 ③해외 잔고 조회 부재)가 모두 해소되었다.

### 결정 사항

| 결정 | 근거 |
|---|---|
| **yfinance 완전 제거** | 수기 종목의 시세도 토스 `/prices`로 커버되므로 데이터 소스를 둘로 유지할 이유가 없음 |
| **손익을 재계산하지 않고 API 값 사용** | `/holdings`가 수수료·세금 차감 손익(`amountAfterCost`)까지 계산해 내려줌. 재계산하면 같은 질문에 답이 두 개 생김 |
| **전 구간 `Decimal`** | 토스가 모든 금액을 문자열로 반환. float은 원 단위 오차가 누적됨 |
| **토큰 캐시 + 파일 락** | 토스는 client당 유효 토큰이 1개이고 재발급 시 이전 토큰을 즉시 무효화. 캐시는 성능이 아니라 정합성 요구사항 |
| **`allow_write=False` 기본값** | 리포팅 단계에서 주문 엔드포인트가 닿을 수 없게 구조적으로 차단 |
| **SQLite 적재를 Phase 1에 포함** | 자산 추이 차트는 과거를 소급 생성할 수 없음. 적재 시작이 늦을수록 손해 |
| **대시보드 서버측 캐시** | `ACCOUNT` 그룹이 1 TPS. 브라우저 탭마다 API를 호출하면 즉시 429 |

### 구현

```
src/
├── config.py            # .env 우선 자격증명, 심볼 정규화(.KS 제거), v1 하위호환
├── toss/
│   ├── client.py        # 토큰 캐시·레이트리밋·429/5xx 백오프·쓰기 가드
│   ├── account.py       # 계좌 · 보유주식 · 매수가능금액
│   ├── market.py        # 시세 · 종목마스터 · 환율 · 캘린더 · 매수유의사항
│   ├── ratelimit.py     # 그룹별 토큰버킷 (X-RateLimit-Limit 관측 반영)
│   ├── errors.py        # 에러코드 → 예외 타입, TERMINAL_CODES
│   └── _filelock.py     # msvcrt/fcntl 크로스 플랫폼 락
├── sources/             # toss_source(자동) · manual_source(config+시세조회)
├── portfolio.py         # 소스 병합 · 중복 심볼 가중평균 · KRW 환산
├── models.py            # Position · PortfolioSnapshot
├── store/               # SQLite (snapshots · position_snapshots · reports)
├── pipeline.py          # 클라이언트~집계 배선 (main.py와 대시보드가 공유)
└── dashboard/           # FastAPI + 정적 프론트엔드
```

**변경**: `main.py`(배선 교체 + exit code), `notion.py`(전일대비·실질수익률·환차손익 배지), `analyst.py`(프롬프트 입력 구조)
**삭제**: `portfolio_manager.py`, `requirements.txt`의 `yfinance`
**신규 문서**: `DESIGN_Toss_API_Migration.md`, `DESIGN_Trading_and_Dashboard.md`, `config.example.yaml`, `.env.example`, `docs/ui/`

### 새로 가능해진 것

- **전일 대비 손익** (`dailyProfitLoss`) — v1에서는 이력 DB 없이 불가능했던 지표
- **수수료·세금 차감 후 실질 수익률** (`rateAfterCost`)
- **매수 유의사항 경보** — 정리매매·투자경고·VI 발동을 리포트 상단과 대시보드 배너에 표시
- **자산 추이 시계열** — SQLite 적재 시작
- **실패 감지** — 리포트 실패 시 exit code 2/3 반환 (기존엔 Notion이 실패해도 0)

### 남은 수기 입력

토스 계좌 보유분은 완전 자동. 타 증권사 보유분만 `(수량, 평단)` 두 값이 남는다 — 종목명·현재가·통화는 API가 채운다.

예외는 **매수 시점 환율**. 토스도 제공하지 않으므로(`averagePurchasePrice`는 원화폐 기준) 환차손익을 수익률에 반영하려면 `avg_exchange_rate`를 직접 입력해야 한다. 미입력 시 "환차손익 미반영" 배지를 표시해 수치를 넓게 해석하지 않도록 했다.

### 검증

- **테스트 63개** — 네트워크·자격증명 없이 전부 통과 (HTTP 모킹)
- **환산 정확도** — AAPL 10주 × $178.5 × 1,342.5 = 2,396,362.5원, 매입은 매수환율 1,300원 적용
- **캐시 동작** — 탭 5개가 10회 호출해도 상위 `/holdings` 호출은 1회
- **팔레트** — 도넛 색상(`#00a572`/`#2563eb`)을 CVD 검증기로 확인, 6개 검사 통과(ΔE 26.5)
- **실계좌 스모크 테스트** — 계좌 조회 OK, 허용 IP 통과, USD/KRW 1,397.10 수신
- **토큰 캐시 실증** — 2회차 실행 시 발급 0회, 캐시 파일 mtime 불변, 유효기간 약 24시간

### 구현 중 잡은 버그

1. **범례 색상이 라벨 전체에 칠해짐** — `querySelector("div div")`가 문서 기준으로 매칭돼 스와치 대신 flex 컨테이너를 선택. 클래스 기반 선택자로 교체
2. **수기 종목 P&L이 빈칸** — 손익을 토스 소스만 채우고 있었음. `ManualSource`에서 네이티브 통화 기준으로 계산 추가
3. **해시 딥링크 무반응** — 해시 변경은 same-document navigation이라 `hashchange` 리스너 추가
4. **플레이스홀더 자격증명이 401로 나타남** — `.env`를 안 채우면 API가 401을 반환해 "키가 틀렸나?"로 헤매게 됨. `PASTE_` 접두사를 감지해 어느 키가 미입력인지 알려주도록 수정

### 운영상 주의점

- **허용 IP 등록 필수** — 미등록 IP는 403 `edge-blocked`로 전량 차단. 동적 IP 환경에서는 어느 날 갑자기 실패한다
- **토큰 1개 제약** — 같은 자격증명으로 두 프로세스를 동시에 돌리면 서로를 로그아웃시킨다. 파일 락으로 방어했지만 별도 머신에서 동시에 돌리면 여전히 충돌
- **대시보드는 `127.0.0.1` 바인딩 유지** — 인증이 없고 Phase 2 이후 실계좌 제어 화면이 된다

### 첫 실행 (16:01, exit 0)

`config.yaml` 작성 → `python main.py` 실행. 파이프라인이 끝에서 끝까지 동작했다.

```
Summary: 24,182,741 KRW (P&L -10,452,454, -30.18%)
Snapshot saved (2026-08-19T16:01:01, total 1 rows)
Fetched 5 general news items.
[Notion] Successfully created report
```

| 단계 | 결과 |
|---|---|
| 포트폴리오 조회 | 24,182,741원 (−30.18%), USD/KRW 1,398.4 |
| SQLite 적재 | `snapshots` 1행 · `position_snapshots` 2행 — 자산 추이 기록 시작 |
| 뉴스 | 5건 |
| Notion 리포트 | 생성 완료 (StockLens AI 페이지 하위) |

두 가지가 예상과 달랐다.

- **토스 계좌 보유분이 0건**이었다. 적재된 2행(IONX·TSLL)은 전부 `source=manual`이고, 총액도 수기 종목 시가(17,293.15 USD × 1,398.4)와 원 단위까지 일치한다. 자동 조회 경로 자체는 계좌·시세·환율 응답으로 검증됐지만, **토스 보유분이 실린 스냅샷은 아직 한 번도 없다** — 실제로 매수하기 전까지 그 경로는 미검증으로 둔다.
- **AI 분석은 건너뛰었다.** `GOOGLE_AI_API_KEY`가 플레이스홀더라 리포트에 `AI Analyst is not configured (Missing API Key).`가 그대로 들어갔다. 리포트 생성 자체는 정상.

`daily_profit_krw`는 0이다. 전일 스냅샷이 없어서가 아니라 토스 보유분이 없어 `dailyProfitLoss`를 받을 곳이 없기 때문이다.

### 다음

- **일일 자동 실행 등록** — Windows 작업 스케줄러에 `run_report.bat`. 스냅샷이 매일 쌓여야 Portfolio Value 차트에 선이 그려진다(점 3개부터). Phase 1의 남은 값은 대부분 여기서 나온다
- **`GOOGLE_AI_API_KEY` 입력** — 넣으면 다음 실행부터 AI 섹션이 붙는다
- Phase 2: `src/toss/trading.py`부터. 구현 순서는 **리스크 게이트 → 주문 실행 → 조건주문(OCO 손절)** — 주문 코드가 먼저 동작하면 안전장치가 뒤로 밀린다
- 별건: `google-generativeai` SDK가 deprecated 상태이고(실행마다 경고 출력) 기본 모델도 구형(`gemini-1.5-flash`). 토스 마이그레이션과 무관하므로 분리해서 진행
