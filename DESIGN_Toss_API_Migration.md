# 설계: 토스증권 Open API 기반 재설계 (v2)

> 작성일: 2026-08-19
> 배경 문서: [ISSUE_Kiwoom_Global_Limit.md](./ISSUE_Kiwoom_Global_Limit.md) (v0 → v1 전환)
> 후속 문서: [DESIGN_Trading_and_Dashboard.md](./DESIGN_Trading_and_Dashboard.md) (Phase 2 자동매매 · Phase 3 대시보드)
> 참조: https://developers.tossinvest.com/docs · OpenAPI JSON `https://openapi.tossinvest.com/openapi-docs/latest/openapi.json` (v1.2.14)

## 1. 요약

키움 OpenAPI 포기의 근본 원인이었던 **①국내/해외 API 파편화 ②32비트 강제 ③해외 잔고 조회 부재**가 토스증권 Open API에서 모두 해소되었습니다.

| 항목 | 키움 OpenAPI (v0, 폐기) | yfinance 하이브리드 (v1, 현재) | 토스 Open API (v2, 제안) |
|---|---|---|---|
| 국내+해외 통합 | ❌ 모듈 분리 | ✅ | ✅ 단일 REST API |
| 실행 환경 | 32bit Python + OCX | 64bit | 64bit, HTTP만 (SDK 불필요) |
| 보유 수량 | 자동(국내만) | ❌ 전량 수기 | ✅ 자동 (토스 계좌분) |
| 평균 매수가 | 자동(국내만) | ❌ 수기 | ✅ 자동 |
| 수수료·세금 반영 손익 | ❌ | ❌ | ✅ `amountAfterCost` |
| 전일 대비 손익 | ❌ | ❌ | ✅ `dailyProfitLoss` |
| 환율 | ❌ | yfinance `KRW=X` | ✅ `/exchange-rate` |
| 수급·지수·랭킹 | 제한적 | ❌ | ✅ |

**핵심 판단: yfinance를 걷어내고 토스 API를 단일 데이터 소스로 삼되, 수기 입력은 "폐지"가 아니라 "축소 후 잔존"시킨다.** 아래 3절 참조.

---

## 2. 토스 API에서 확인한 사실 (설계 근거)

### 2.1 인증
```
POST https://openapi.tossinvest.com/oauth2/token
Content-Type: application/x-www-form-urlencoded
grant_type=client_credentials&client_id=...&client_secret=...
→ { "access_token": "...", "token_type": "Bearer", "expires_in": 3600 }
```
- OAuth 2.0 **Client Credentials** — 사용자 리다이렉트 없음. 배치 실행에 이상적.
- **refresh token 없음.** 만료 시 동일 엔드포인트 재발급.
- ⚠️ **client당 유효 토큰은 1개.** 재발급하면 이전 토큰이 *즉시* 무효화됨 → 토큰 캐시가 선택이 아니라 필수. 같은 client_id로 두 프로세스를 동시에 돌리면 서로를 로그아웃시킴.

### 2.2 계좌 헤더
계좌·자산·주문 API는 `Authorization: Bearer` **+** `X-Tossinvest-Account: {accountSeq}` 를 함께 요구.
`accountSeq`는 `GET /api/v1/accounts` 응답의 정수 필드(`{"accountNo":"12345678901","accountSeq":1,"accountType":"BROKERAGE"}`).

### 2.3 보유 주식 — `GET /api/v1/holdings`
현재 `portfolio_manager.py`가 손으로 계산하는 값을 **서버가 전부 내려줍니다.**
```jsonc
{"result": {
  "totalPurchaseAmount": { "krw": "6500000", "usd": "1553" },
  "marketValue":  { "amount": {"krw":"7200000","usd":"1785"},
                    "amountAfterCost": {"krw":"7050000","usd":"1771.43"} },
  "profitLoss":   { "amount": {...}, "amountAfterCost": {...},
                    "rate": "0.1179", "rateAfterCost": "0.0983" },
  "dailyProfitLoss": { "amount": {...}, "rate": "0.0141" },
  "items": [{
    "symbol":"005930", "name":"삼성전자", "marketCountry":"KR", "currency":"KRW",
    "quantity":"100", "lastPrice":"72000", "averagePurchasePrice":"65000",
    "marketValue": {"purchaseAmount":"6500000","amount":"7200000","amountAfterCost":"7050000"},
    "profitLoss":  {"amount":"700000","rate":"0.1077","rateAfterCost":"0.0846"},
    "dailyProfitLoss": {"amount":"100000","rate":"0.0141"},
    "cost": {"commission":"14400","tax":"135600"}
  }]
}}
```
설계상 중요한 두 가지:
1. **모든 금액이 문자열(string)** → `float`가 아니라 `decimal.Decimal`로 파싱해야 함. 현재 코드의 float 누적 오차 문제도 같이 해결됨.
2. **`krw`/`usd` 총계가 통화별로 분리**되어 있고 합산 원화 총액 필드는 없음 → 통합 KRW 총액은 `/exchange-rate`로 우리가 직접 합산해야 함. (`profitLoss.rate`만 원화 환산 기준으로 제공)

### 2.4 그 외 사용할 엔드포인트
| 엔드포인트 | 용도 | Rate Limit Group |
|---|---|---|
| `GET /api/v1/prices?symbols=A,B` (최대 200건) | **수기 입력 종목의 현재가** | `MARKET_DATA` 15 TPS |
| `GET /api/v1/stocks?symbols=...` | 종목명·시장·통화 마스터 (수기 종목 이름 자동 채움) | `STOCK` 5 TPS |
| `GET /api/v1/exchange-rate?baseCurrency=USD&quoteCurrency=KRW` | KRW 통합 환산 | `MARKET_INFO` 3 TPS |
| `GET /api/v1/market-calendar/KR`\|`/US` | **휴장일이면 리포트 스킵** | `MARKET_INFO` 3 TPS |
| `GET /api/v1/stocks/{symbol}/warnings` | 보유 종목 투자경고·VI·정리매매 경보 | `STOCK` 5 TPS |
| `GET /api/v1/stocks/{symbol}/investor-trading` | 국내 보유주 외국인·기관 수급 (AI 입력) | `STOCK_TRADING_TREND` 10 TPS |
| `GET /api/v1/market-indicators/prices?symbols=KOSPI,KOSDAQ` | 지수 스냅샷 (AI 입력) | 10 TPS |
| `GET /api/v1/rankings?type=TOP_GAINERS&marketCountry=KR&duration=1d` | 시장 온도 (AI 입력) | `RANKING` 5 TPS |

가장 빡빡한 한도는 `ACCOUNT` **1 TPS**와 `STOCK_ALL` 1 TPS. 일 1회 리포트에는 여유롭지만 클라이언트에 토큰버킷을 두는 편이 안전.

### 2.5 에러 모델
모든 에러가 동일 envelope: `{"error":{"requestId","code","message","data"}}`.
429 시 `Retry-After` 헤더 준수 + 지수 백오프(1→2→4s)+jitter.

---

## 3. 무엇이 자동화되고, 무엇이 여전히 수기인가

사용자 지적대로 **토스증권 계좌에 없는 종목은 여전히 수기**입니다. 정확히는 세 가지가 남습니다.

| 잔존 수기 항목 | 이유 | 완화 방안 |
|---|---|---|
| **타 증권사 보유 종목의 수량·평단** | 토스 API는 토스 계좌만 조회 | 수량·평단만 config에 두고, **현재가·종목명은 토스 `/prices`·`/stocks`로 자동 조회** → v1 대비 유지보수 부담 대폭 감소 |
| **매수 시점 환율** (`avg_exchange_rate`) | ⚠️ 토스 `/holdings`도 **매수 당시 환율은 제공하지 않음** (`averagePurchasePrice`는 USD 원화폐 기준) | config의 선택적 override로 유지. 미입력 시 현재 환율로 대체(= 환차손익 0으로 간주) |
| **토스가 커버하지 않는 자산** (예금·금·코인·해외 ETF 일부) | API 범위 밖 | `manual` 소스에 `price` 직접 기입하는 정적 항목 타입 허용 |

> 즉 v2의 목표는 "수기 제거"가 아니라 **"수기 입력의 표면적을 `(수량, 평단)` 두 값으로 축소하고, 나머지 전부를 API가 채우게 만드는 것"**입니다.

---

## 4. 아키텍처

### 4.1 데이터 흐름
```
config.yaml ──┐
              ├─→ HoldingsAggregator ─→ Position[] (통일 모델, Decimal)
Toss /holdings ┘         │
                         ├─→ 통화별 합산 + /exchange-rate → PortfolioSnapshot
                         ↓
       MarketContext (지수 · 랭킹 · 보유종목 warnings · 수급)
                         ↓
       NewsFetcher (Google News RSS — 변경 없음)
                         ↓
       Analyst (Gemini: 스냅샷 + 전일대비 + 시장맥락 + 뉴스)
                         ↓
       NotionReporter (블록 조립)
```

### 4.2 모듈 구조
```text
src/
├── toss/
│   ├── client.py       # TossClient: 토큰 캐시·자동갱신, 레이트리밋, 429 백오프,
│   │                   #   envelope 언랩(result 추출), TossApiError(code) 예외화
│   ├── market.py       # prices / stocks / exchange_rate / market_calendar
│   │                   #   / warnings / investor_trading / indicators / rankings
│   └── account.py      # accounts() → accountSeq 해석, holdings()
├── sources/
│   ├── base.py         # HoldingSource 인터페이스: fetch() -> list[Position]
│   ├── toss_source.py  # /holdings 파싱 → Position (수량·평단·손익 전부 API값)
│   └── manual_source.py# config 수량·평단 + /prices 시세 → Position
├── portfolio.py        # HoldingsAggregator: 소스 병합, 중복 심볼 합산,
│                       #   통화별 집계 → KRW 환산 → PortfolioSnapshot
├── market_context.py   # AI/리포트용 시장 맥락 수집 (실패해도 리포트는 진행)
├── news.py             # 변경 없음
├── analyst.py          # 프롬프트 확장 (전일대비·경보·수급·지수)
└── notion.py           # 리포트 블록 확장
```

### 4.3 통일 데이터 모델
```python
@dataclass(frozen=True)
class Position:
    symbol: str                  # 토스 표기: "005930", "AAPL"  (.KS 접미사 없음)
    name: str
    market_country: str          # "KR" | "US"
    currency: str                # "KRW" | "USD"
    quantity: Decimal
    last_price: Decimal
    avg_purchase_price: Decimal
    source: str                  # "toss" | "manual"
    # 아래는 toss 소스에서만 채워지고, manual은 계산으로 채움
    profit_loss: Decimal | None
    profit_loss_after_cost: Decimal | None
    daily_profit_loss: Decimal | None
    avg_exchange_rate: Decimal | None   # config override 전용
```
`PortfolioSnapshot`은 `positions`, `by_currency{krw,usd}`, `total_krw`, `total_profit_krw`,
`total_profit_rate`, `daily_profit_krw`, `daily_profit_rate`, `exchange_rate`, `warnings[]`를 보유.

### 4.4 KRW 환산 규칙 (v1 로직 계승)
```
평가액_KRW  = Σ(KRW종목 평가액) + Σ(USD종목 평가액) × 현재환율
매입액_KRW  = Σ(KRW종목 매입액) + Σ(USD종목 매입액) × (avg_exchange_rate ?? 현재환율)
```
`avg_exchange_rate`를 지정한 종목만 환차손익이 수익률에 반영됩니다. 미지정 시 주가 손익만 반영되며, 리포트에 *"환차손익 미반영"* 배지를 함께 표기해 수치 해석을 오도하지 않게 합니다.

### 4.5 `TossClient` 세부
- **토큰 캐시**: `.toss_token.json` (gitignore 대상, 0600 권장). `expires_in`에서 60초 마진 차감 후 저장. 만료 전이면 재발급하지 않음 — 2.1의 "토큰 1개" 제약 때문.
- **레이트리밋**: 그룹별 토큰버킷. 응답 헤더 `X-RateLimit-Remaining` 을 읽어 선제 감속.
- **재시도**: 429 → `Retry-After` 준수, 5xx → 지수 백오프 3회. 4xx(401/403/404)는 즉시 예외.
- **읽기 전용 가드**: Phase 1 동안 클라이언트는 GET과 `/oauth2/token`만 허용. 주문·조건주문은 Phase 2에서 별도 모듈(`src/toss/trading.py`)로 격리해 추가합니다 — [DESIGN_Trading_and_Dashboard.md](./DESIGN_Trading_and_Dashboard.md) 3.2절 참조.

### 4.6 설정 파일 (`config.yaml`)
```yaml
toss:
  client_id: "c_xxx"
  client_secret: "s_xxx"
  account_no: "12345678901"   # 선택. 미지정 시 /accounts 첫 계좌 사용
  token_cache: ".toss_token.json"

portfolio:
  # 토스 계좌 밖의 자산만 기입. 이름·현재가는 토스 API가 채움.
  manual:
    - symbol: "000660"          # SK하이닉스 (타 증권사 보유)
      qty: 10
      avg_price: 180000
    - symbol: "AAPL"
      qty: 5
      avg_price: 180.0
      avg_exchange_rate: 1300.0 # 선택: 환차손익 반영용
    - name: "금 현물"            # symbol 없는 정적 항목
      qty: 1
      avg_price: 5000000
      price: 5400000            # 시세 자동조회 불가 → 직접 기입
      currency: "KRW"

report:
  skip_on_market_holiday: true  # /market-calendar 확인 후 휴장일 스킵
  include_market_context: true

news:
  keywords: ["반도체", "환율"]

google_ai:
  api_key: "..."
  model: "gemini-2.5-flash"
notion:
  token: "..."
  database_id: "..."
```

> **심볼 표기 변경**: yfinance `005930.KS` → 토스 `005930`. `.KS`/`.KQ` 접미사는 로더에서 자동 스트립하여 기존 config를 그대로 읽을 수 있게 합니다.

---

## 5. 새로 가능해지는 리포트 기능

v1에서는 구조적으로 불가능했던 것들:

1. **전일 대비 손익** — `dailyProfitLoss`. 별도 이력 DB 없이 "어제보다 +12만원(+1.4%)" 표기 가능. 일일 리포트의 핵심 지표.
2. **수수료·세금 차감 후 실현 기준 손익** — `amountAfterCost`. "명목 수익률 / 실질 수익률" 병기.
3. **보유 종목 리스크 경보** — `/warnings`로 정리매매·투자경고·단기과열·VI 감지 시 리포트 상단에 ⚠️ 콜아웃. **AI 분석보다 우선순위 높은, 사실 기반 알림.**
4. **휴장일 자동 스킵** — `/market-calendar`. `.agent/workflows/run_report.md` 자동 실행 시 주말·공휴일 빈 리포트 방지.
5. **AI 애널리스트 입력 품질 향상** — 기존에는 (보유종목 손익 + 뉴스 제목 5개)뿐이었지만, 이제 KOSPI/KOSDAQ 지수, 상승·하락 랭킹, 보유 종목의 외국인·기관 수급까지 프롬프트에 투입 가능.

---

## 6. 단계별 이행 계획

| 단계 | 작업 | 산출물 | 검증 |
|---|---|---|---|
| **0** | 토스 WTS > 설정 > Open API에서 `client_id`/`client_secret` 발급 + **허용 IP 등록** | 자격증명 | `curl /oauth2/token` 200 |
| **1** | `src/toss/client.py` — 토큰 캐시·레이트리밋·에러 매핑 | 클라이언트 | 토큰 재사용 확인(재발급 안 일어남) |
| **2** | `src/toss/account.py` + `market.py` | 조회 래퍼 | `/accounts`, `/holdings` 실계좌 응답 |
| **3** | `Position` 모델 + `sources/` 2종 + `HoldingsAggregator` | 병합 로직 | 토스+수기 혼합 스냅샷 정상 |
| **4** | `portfolio_manager.py` 제거, `main.py` 배선 교체, `requirements.txt`에서 **yfinance 제거** | v2 파이프라인 | 기존과 동일한 리포트 산출 |
| **5** | `notion.py` — 전일대비·실질수익률·경보 콜아웃 추가 | 리포트 확장 | Notion 페이지 육안 확인 |
| **6** | `market_context.py` + `analyst.py` 프롬프트 확장 | AI 품질 | — |
| **7** | 휴장일 스킵, 실패 시 exit code 반환 | 배치 안정화 | 휴장일 실행 시 no-op |

**4단계까지가 최소 목표(MVP)** — 여기까지만 해도 토스 계좌분 수기 입력이 전부 사라집니다.

---

## 7. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| **허용 IP 등록 필수** (미등록 IP는 403 `edge-blocked`) | 가정용 회선은 동적 IP → 어느 날 갑자기 403 | 403 발생 시 "허용 IP 재등록 필요" 메시지를 명시적으로 출력. 고정 IP 서버/NAS에서 배치 실행 권장 |
| **client당 토큰 1개** | 두 프로세스 동시 실행 시 상호 무효화 | 토큰 캐시 파일 + 파일 락. 수동 실행과 스케줄 실행 동시 발생 주의 |
| **`client_secret` 유출** | 계좌 조회 + **주문 권한**까지 노출 | `config.yaml`은 이미 gitignore. 환경변수(`TOSS_CLIENT_SECRET`) 우선 로드를 추가하고 config는 fallback으로 강등 |
| 토스 미상장 종목 (`stock-not-found`) | 수기 종목 시세 조회 실패 | config `price` 정적값 fallback. (yfinance 재도입은 하지 않음 — 의존성 하나로 유지) |
| API 버전 변경 (현재 v1.2.14) | 필드 변경 | 응답 파싱을 dataclass 경계 한 곳에 격리. 미지 필드는 무시 |
| 문자열 금액을 float로 파싱 | 원 단위 오차 누적 | 경계에서 전부 `Decimal`로 변환, 표시 직전에만 반올림 |

---

## 8. v1 대비 삭제/변경 요약

- **삭제**: `src/portfolio_manager.py`, `requirements.txt`의 `yfinance`
- **신규**: `src/toss/*`, `src/sources/*`, `src/portfolio.py`, `src/market_context.py`
- **변경**: `main.py` 배선, `config.yaml` 스키마(`portfolio.stocks` → `portfolio.manual`), `notion.py` 블록, `analyst.py` 프롬프트
- **유지**: `src/news.py` (Google News RSS는 토스 API 범위 밖이므로 그대로)
