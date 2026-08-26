# 설계: 자동매매 엔진 및 대시보드 (Phase 2–3)

> 작성일: 2026-08-19
> 선행 문서: [DESIGN_Toss_API_Migration.md](./DESIGN_Toss_API_Migration.md) (Phase 1)
> 참조: OpenAPI v1.2.14 — `https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`

## 0. 로드맵 상 위치

| Phase | 목표 | 상태 |
|---|---|---|
| **1. 리포트** | 토스 API로 보유자산 조회 → Notion 일일 리포트 | 설계 완료 |
| **2. 자동매매** | 사용자 정의 로직으로 신호 생성 → 주문 실행 | 본 문서 |
| **3. 대시보드** | 자산 추이·전략 성과·주문 현황 화면 | 본 문서 |

**Phase 1의 "읽기 전용 가드"는 본 문서에서 철회합니다.** 대신 *쓰기 권한을 별도 모듈 경계 뒤에 격리하고, 기본값을 실행 불가 상태로 두는* 방식으로 대체합니다 (3.2절).

---

## 1. 자격증명 관리 (Phase 2 진입 전 필수)

Phase 1에서는 `client_secret`이 유출돼도 조회 권한뿐이었지만, **Phase 2부터는 동일한 키가 주문 실행 권한**입니다. 취급 등급을 올려야 합니다.

```python
# src/config.py
secret = os.environ.get("TOSS_CLIENT_SECRET")   # 1순위
if not secret:
    secret = cfg.get("toss", {}).get("client_secret")  # 2순위 (deprecated 경고)
if not secret:
    raise ConfigError("TOSS_CLIENT_SECRET 미설정")
```

- `.env` 파일 사용 (`python-dotenv`), **`.gitignore`에 `.env`, `.toss_token.json`, `*.db` 추가**
- 로그·예외 메시지에 secret이 실려나가지 않도록 `TossClient.__repr__` 마스킹
- 키가 노출된 정황이 있으면 **즉시 WTS에서 재발급** — 재발급 시 이전 토큰도 함께 무효화되므로 사고 차단이 즉시 이루어짐

---

## 2. 주문 API 스펙 (확인된 사실)

### 2.1 주문 생성 — `POST /api/v1/orders`
```jsonc
// 국내 지정가 매수
{ "clientOrderId":"my-order-001", "symbol":"005930",
  "side":"BUY", "orderType":"LIMIT", "quantity":"10", "price":"70000" }

// 미국 금액 지정 시장가 매수 (소수점 체결)
{ "symbol":"AAPL", "side":"BUY", "orderType":"MARKET", "orderAmount":"100.5" }

// 미국 LOC (종가 지정가) = LIMIT + timeInForce CLS
{ "symbol":"AAPL", "side":"BUY", "orderType":"LIMIT",
  "timeInForce":"CLS", "quantity":"10", "price":"185.5" }
```
설계에 반영해야 할 제약:
- `quantity` / `orderAmount` **정확히 하나만** 사용
- **소수점 수량은 US `MARKET`+`SELL`에만 허용.** 그 외는 정수
- **`orderAmount`·소수점 수량은 정규장 시작 ~ 정규장 종료 1시간 전까지만** 접수
  → `422 amount-order-outside-regular-hours` / `fractional-quantity-outside-regular-hours`
- 주문 금액 **1억원 이상이면 `confirmHighValueOrder: true`** 필수 (`400 confirm-high-value-required`)
- `clientOrderId`가 **멱등키**. 내용이 다른 재요청은 `422 idempotency-key-conflict`, 처리 중 재요청은 `409 request-in-progress`

### 2.2 사전 검증용 조회
| 엔드포인트 | 응답 | 용도 |
|---|---|---|
| `GET /api/v1/buying-power?currency=KRW\|USD` | `{"cashBuyingPower":"5000000"}` | 매수 전 현금 확인 |
| `GET /api/v1/sellable-quantity` | `{"sellableQuantity":"100"}` | 매도 전 수량 확인 |
| `GET /api/v1/commissions` | KR·US 수수료율 | 손익분기 계산 |
| `GET /api/v1/price-limits` | 상·하한가 | `price-out-of-range` 사전 차단 |
| `GET /api/v1/market-calendar/KR\|US` | 세션별 운영시간 | `order-hours-closed` 사전 차단 |

### 2.3 조건주문 — `POST /api/v1/conditional-orders` ⭐
```jsonc
// OCO: 익절 305 / 손절 295 동시 걸기 (하나 체결 시 나머지 자동 취소)
{ "symbol":"005930", "type":"OCO", "quantity":"100", "orderType":"LIMIT",
  "clientOrderId":"oco-001", "expireDate":"2026-09-10",
  "first":  { "orderSide":"SELL", "triggerPrice":"305", "orderPrice":"305" },
  "second": { "orderSide":"SELL", "triggerPrice":"295", "orderPrice":"294.5" } }

// OTO: 290 매수 체결되면 → 320 매도 자동 등록
{ "symbol":"005930", "type":"OTO", "quantity":"100", "orderType":"LIMIT",
  "clientOrderId":"oto-001", "expireDate":"2026-09-10",
  "first":  { "orderSide":"BUY",  "triggerPrice":"290", "orderPrice":"290" },
  "second": { "orderSide":"SELL", "triggerPrice":"320", "orderPrice":"320" } }
```

> **이것이 개인 자동매매에서 가장 중요한 기능입니다.** 손절·익절을 **토스 서버가 감시**하므로 내 PC/봇이 24시간 떠 있을 필요가 없습니다. 폴링 루프로 감시하다가 프로세스가 죽어 손절을 놓치는 개인 봇의 전형적 실패 모드를 구조적으로 제거합니다.

제약: OCO/OTO는 **지정가(LIMIT)만**, **종목당 1개**(`422 duplicate-conditional-order`, SINGLE은 무제한). 설정 가격이 이미 조건 충족이면 `422 condition-already-met`. `expireDate` 필수.

### 2.4 Rate Limit
`ORDER` 10 TPS · `ORDER_HISTORY` 5 TPS · `ORDER_INFO` 6 TPS(**09:00–09:10 피크에는 3 TPS로 감소**) · `CONDITIONAL_ORDER` 5 TPS.
개장 직후 잔고/주문가능금액을 반복 조회하는 전략은 피크 한도에 먼저 걸립니다 — 스냅샷 1회 후 메모리 캐시 사용을 권장.

---

## 3. 자동매매 아키텍처

### 3.1 파이프라인
```
   MarketData ─┐
   Positions  ─┼─→ StrategyEngine ─→ Signal[]
   Snapshot   ─┘        (순수 함수: 상태 → 신호. I/O 없음 → 테스트·백테스트 가능)
                            ↓
                        RiskGate  ──(거부)──→ RejectedSignal (사유 기록)
                            ↓ (통과)
                        OrderIntent
                            ↓
                      OrderExecutor  ──→ Toss POST /orders | /conditional-orders
                            ↓
                       Reconciler ←── GET /orders (폴링)
                            ↓
                      SQLite (orders / fills / signals)
                            ↓
                   Notion 알림 + Dashboard
```

**핵심 원칙: `StrategyEngine`은 I/O를 하지 않습니다.** 입력(포지션·시세·지표)을 받아 `Signal`을 반환하는 순수 함수로 두면, 동일 코드를 과거 데이터에 흘려 백테스트할 수 있고 단위 테스트가 가능합니다. 주문 실행은 전략이 아니라 `OrderExecutor`의 책임입니다.

### 3.2 모듈 구조
```text
src/
├── toss/
│   ├── client.py          # (Phase 1)
│   ├── market.py          # (Phase 1)
│   ├── account.py         # (Phase 1)
│   └── trading.py         # ★ 쓰기 전용 모듈. 주문·조건주문·정정·취소
├── strategy/
│   ├── base.py            # Strategy 인터페이스: evaluate(ctx) -> list[Signal]
│   └── <내_전략>.py        # 사용자 로직
├── execution/
│   ├── risk.py            # RiskGate: 한도·시간·잔고 검증
│   ├── executor.py        # 멱등 발주, 재시도, 에러코드 분기
│   └── reconciler.py      # 주문 상태 동기화 → fills
├── store/
│   ├── schema.sql
│   └── repo.py            # SQLite 접근
└── dashboard/
    ├── api.py             # FastAPI
    └── static/
```

**`toss/trading.py`가 유일한 쓰기 경로**입니다. 이 모듈은 생성자에서 `TradingMode`를 요구하며, 기본값은 `PAPER`입니다.

### 3.3 안전장치 (모두 필수 구현)

| # | 장치 | 구현 |
|---|---|---|
| 1 | **모드 분리** | `PAPER`(기본) / `LIVE`. `PAPER`는 주문 요청을 조립·검증까지만 하고 HTTP를 보내지 않은 채 DB에 `simulated` 기록. LIVE는 config가 아니라 **CLI 플래그 `--live`로만** 진입 |
| 2 | **킬 스위치** | `KILL_SWITCH` 파일 존재 시 모든 발주 중단. 대시보드에서 토글 |
| 3 | **멱등 주문 ID** | `clientOrderId = f"{strategy}-{symbol}-{date}-{seq}"` 결정론적 생성. 배치 중복 실행·재시작 시 재발주 차단 |
| 4 | **일일 한도** | 최대 주문 건수 / 일일 총 주문금액 / 종목당 최대 비중(%) — 초과 시 `RiskGate` 거부 |
| 5 | **사전 잔고 검증** | 발주 전 `buying-power`·`sellable-quantity` 확인. 실패를 422로 배우지 않음 |
| 6 | **장 운영시간 확인** | `market-calendar`로 세션 확인. 소수점/금액 주문은 "정규장 종료 1시간 전" 규칙까지 반영 |
| 7 | **가격 범위 확인** | `price-limits`로 상·하한가 내인지 검증 |
| 8 | **고액 주문 이중 확인** | 1억 이상은 `confirmHighValueOrder` 자동 세팅 금지. **명시적 config 허용 시에만** 통과 |
| 9 | **전량 감사 로그** | 모든 signal·intent·주문·거부를 사유와 함께 SQLite 기록. "왜 샀는지" 추적 가능 |
| 10 | **손절 서버 위임** | 진입 체결 시 OCO 조건주문 동시 등록 → 봇 중단 시에도 손절 유효 |

### 3.4 에러코드 분기 정책
| 에러 | 처리 |
|---|---|
| `insufficient-buying-power`, `insufficient-sellable-quantity` | 재시도 금지. 신호 폐기 + 알림 |
| `order-hours-closed`, `amount-order-outside-regular-hours` | 재시도 금지. 다음 세션 큐로 이월(선택) |
| `price-out-of-range`, `invalid-tick-size` | 가격 재계산 후 **1회** 재시도 |
| `opposite-pending-order-exists` | 기존 주문 취소 여부를 전략에 위임 |
| `request-in-progress`, `already-processing` | 백오프 후 상태 조회로 확인 (재발주 금지) |
| `idempotency-key-conflict` | **버그 신호.** 즉시 중단 + 알림 |
| `stock-restricted`, `account-restricted`, `prerequisite-required` | 해당 종목/계좌 블랙리스트 + 알림 |
| `429` | `Retry-After` 준수 |

---

## 4. 영속 계층 (대시보드의 전제)

현재 프로젝트에는 저장소가 전혀 없어 "어제 대비"조차 계산할 수 없습니다. Phase 1의 `dailyProfitLoss`가 하루치를 메워주지만, **자산 추이 그래프와 전략 성과 분석은 자체 이력 없이는 불가능**합니다. → **SQLite** (단일 파일, 의존성 없음, 개인 규모에 충분).

```sql
-- 일별 자산 스냅샷 (리포트 실행 시마다 기록)
CREATE TABLE snapshots (
  ts TEXT PRIMARY KEY,           -- ISO8601
  total_krw TEXT, purchase_krw TEXT,
  profit_krw TEXT, profit_rate TEXT,
  daily_profit_krw TEXT, exchange_rate TEXT
);
CREATE TABLE position_snapshots (
  ts TEXT, symbol TEXT, name TEXT, currency TEXT,
  quantity TEXT, last_price TEXT, avg_price TEXT,
  market_value TEXT, profit_loss TEXT, source TEXT,
  PRIMARY KEY (ts, symbol)
);
-- 전략 판단 기록 ("왜 샀는가")
CREATE TABLE signals (
  id INTEGER PRIMARY KEY, ts TEXT, strategy TEXT, symbol TEXT,
  side TEXT, reason TEXT, payload TEXT,       -- JSON
  outcome TEXT                                 -- accepted | rejected
);
CREATE TABLE rejections (signal_id INTEGER, rule TEXT, detail TEXT);
-- 주문·체결
CREATE TABLE orders (
  client_order_id TEXT PRIMARY KEY, order_id TEXT, ts TEXT,
  strategy TEXT, symbol TEXT, side TEXT, order_type TEXT,
  quantity TEXT, price TEXT, status TEXT,
  mode TEXT,                                   -- paper | live
  error_code TEXT
);
CREATE TABLE fills (
  order_id TEXT, ts TEXT, quantity TEXT, price TEXT, commission TEXT, tax TEXT
);
```
> 금액은 전부 **TEXT로 저장하고 `Decimal`로 읽습니다.** SQLite `REAL`은 부동소수 오차가 그대로 남습니다.

---

## 5. 대시보드 설계 (Phase 3)

### 5.1 기술 선택
**FastAPI + SQLite + 정적 프론트엔드(Chart.js)** 를 권장합니다.

- Streamlit이 더 빠르게 만들 수 있지만, **킬 스위치·주문 트리거 같은 쓰기 액션**과 인증을 붙이려면 결국 FastAPI가 필요합니다. 두 번 만들지 않으려면 처음부터 FastAPI.
- 이미 `notion-client`, `requests` 등 HTTP 스택을 쓰고 있어 추가 부담이 적습니다.
- **로컬 바인딩(`127.0.0.1`) 기본.** 외부 노출 시 반드시 인증 추가 — 이 화면은 주문 실행 권한을 가집니다.

### 5.2 화면 구성
| 화면 | 내용 | 데이터 출처 |
|---|---|---|
| **개요** | 총자산 KRW, 오늘 손익, 누적 수익률(명목/실질), 환율 | `snapshots` 최신 + `/holdings` |
| **자산 추이** | 일별 평가액·손익 라인차트, 기간 필터 | `snapshots` |
| **포지션** | 종목별 수량·평단·현재가·손익·비중, 토스/수기 배지, ⚠️경보 | `position_snapshots` + `/warnings` |
| **전략 성과** | 전략별 승률·평균손익·누적 PnL, 신호 대비 체결률 | `signals` ⋈ `orders` ⋈ `fills` |
| **주문 이력** | 주문·체결·거부 타임라인, 거부는 **사유(rule)** 표시 | `orders`, `rejections` |
| **조건주문 현황** | 진행 중 OCO/OTO 목록, 감시가 vs 현재가 거리 | `/conditional-orders` (실시간) |
| **제어** | 킬 스위치 토글, PAPER/LIVE 표시, 수동 취소 | 파일 + `/orders/{id}/cancel` |

### 5.3 실시간성
토스 API는 **REST만 제공하며 WebSocket이 없습니다.** 대시보드는 폴링 기반으로 설계합니다.
- 화면 열려 있을 때 5–10초 주기 폴링, `MARKET_DATA` 15 TPS 한도 내에서 여유
- 단, `ACCOUNT` 1 TPS / `ORDER_INFO` 피크 3 TPS는 빡빡 → **계좌·주문 조회는 서버측에서 캐시**하고 여러 브라우저 탭이 같은 캐시를 공유

---

## 6. 구현 순서

```
Phase 1  [1] toss/client.py  [2] account+market  [3] Position 병합  [4] 리포트 배선
            ↓
Phase 2  [5] store/ SQLite + 리포트 실행 시 스냅샷 적재   ← 대시보드의 전제. 빨리 시작할수록 이력이 쌓임
         [6] strategy/base.py + 백테스트 러너 (순수 함수 검증) ✅ momentum_dca 전략 포함, 2026-08-26
         [7] execution/risk.py  (주문 코드보다 리스크 게이트를 먼저)
         [8] toss/trading.py + executor — PAPER 모드로만
         [9] reconciler + 조건주문(OCO) 연동
        [10] LIVE 전환 — 최소 수량 1주로 실거래 검증
            ↓
Phase 3 [11] FastAPI 읽기 전용 화면
        [12] 제어 화면 (킬 스위치)
```

> **[5]번을 가장 먼저 하시길 권합니다.** 대시보드의 자산 추이 그래프는 과거 데이터를 소급 생성할 수 없습니다. 오늘부터 스냅샷을 적재해야 Phase 3 시점에 볼 것이 있습니다. 리포트 파이프라인에 `INSERT` 한 줄 붙이는 수준의 작업입니다.

> **[7]을 [8]보다 먼저** 두었습니다. 주문 코드가 먼저 동작하면 "일단 돌려보고 안전장치는 나중에"가 되기 쉽습니다.

---

## 7. 리스크

| 리스크 | 대응 |
|---|---|
| **허용 IP 미등록 시 403** — 자동매매 중 IP 변경되면 손절 주문 실패 | ①조건주문으로 손절을 서버에 위임 ②403 감지 시 즉시 Notion/푸시 알림 ③고정 IP 환경 권장 |
| **client당 토큰 1개** — 봇과 대시보드가 각자 토큰 발급 시 상호 무효화 | 토큰을 **단일 프로세스가 소유**하고 공유(파일 락), 또는 대시보드를 봇과 같은 프로세스에 통합 |
| PAPER/LIVE 혼동으로 의도치 않은 실주문 | LIVE는 CLI 플래그로만 진입 + 시작 시 배너 출력 + 모든 주문 레코드에 `mode` 기록 |
| 전략 버그로 반복 발주 | 결정론적 `clientOrderId` + 일일 주문 건수 한도 |
| 백테스트 과최적화 | `StrategyEngine` 순수 함수화로 out-of-sample 검증 강제 |
| 대시보드 외부 노출 | `127.0.0.1` 바인딩 기본, 원격 접근은 SSH 터널 권장 |
