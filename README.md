# StockLens-AI

토스증권 Open API로 포트폴리오를 읽어 **Notion 일일 리포트**를 만들고, **로컬 대시보드**로 자산 현황을 보여주는 개인용 도구입니다.

국내(KRX)와 미국 주식을 단일 REST API로 조회하며, 보유 수량·평균 매수가·평가손익은 물론 **수수료·세금 차감 후 손익**과 **전일 대비 손익**까지 서버가 계산해 내려줍니다.

```
토스 계좌 ─┐
           ├─→ 통합 포트폴리오 ─┬─→ Notion 리포트 (AI 분석 + 뉴스)
config.yaml ┘  (KRW 환산)       ├─→ SQLite 스냅샷
 (타 증권사)                     └─→ 대시보드 (자산 추이 · 배분 · 경보)
```

---

## 빠른 시작

```bash
# 1. 의존성
pip install -r requirements.txt

# 2. 자격증명 — .env 의 PASTE_... 를 실제 값으로 교체
#    (토스증권 WTS > 설정 > Open API 에서 발급 + 허용 IP 등록)

# 3. 연결 확인 (읽기 전용)
python scripts/smoke_test.py

# 4. 설정 후 리포트 실행
cp config.example.yaml config.yaml   # 보유 종목 · Notion 정보 입력
python main.py

# 5. 대시보드
uvicorn src.dashboard.api:app --host 127.0.0.1 --port 8000
```

Python 3.10 이상 (3.12에서 개발·검증).

---

## 설정

### 1. 토스 Open API 발급

토스증권 WTS → **설정 → Open API**

1. `client_id` / `client_secret` 발급
2. 같은 화면 하단 **허용 IP 관리**에 API를 호출할 공인 IP 등록

> ⚠️ **허용 IP는 필수입니다.** 등록되지 않은 IP의 호출은 `403 edge-blocked`로 전량 차단됩니다. 가정용 회선처럼 IP가 바뀌는 환경에서는 어느 날 갑자기 전체가 실패하므로, 그 경우 재등록이 필요합니다. `smoke_test.py`는 403을 만나면 이 안내를 명시적으로 출력합니다.

### 2. `.env`

자격증명은 `config.yaml`이 아니라 `.env`에 둡니다 (gitignore 대상). 리포지토리에 이미 `.env`가 플레이스홀더로 생성되어 있으니 값만 교체하면 됩니다.

```
TOSS_CLIENT_ID=c_xxxxxxxxxxxx
TOSS_CLIENT_SECRET=tssk_live_xxxxxxxxxxxx
GOOGLE_AI_API_KEY=xxxxxxxxxxxx     # 선택 — 없으면 AI 분석만 생략
```

교체를 잊으면 앱이 *"`.env`의 TOSS_CLIENT_ID가 아직 플레이스홀더입니다"* 라고 알려줍니다.

> **왜 파일이 아니라 환경변수인가**: Phase 2부터 이 키는 조회가 아니라 **주문 실행 권한**을 가집니다. 채팅·이슈·스크린샷에 노출하지 마세요. 노출됐다면 WTS에서 즉시 재발급하면 됩니다 — 재발급은 유출된 토큰도 그 즉시 무효화합니다.

### 3. `config.yaml`

```bash
cp config.example.yaml config.yaml
```

**토스 계좌 밖 보유분**과 Notion 정보만 채우면 됩니다.

```yaml
portfolio:
  manual:
    - symbol: "000660"        # 6자리 코드 (.KS/.KQ 접미사는 자동 제거)
      qty: 10
      avg_price: 180000

    - symbol: "AAPL"
      qty: 5
      avg_price: 180.0
      avg_exchange_rate: 1300.0   # 선택 — 아래 설명 참고

    # 토스가 취급하지 않는 자산 (예금·금 등)
    # - name: "금 현물"
    #   qty: 1
    #   avg_price: 5000000
    #   price: 5400000
    #   currency: "KRW"
```

기존 v1 설정(`portfolio.stocks`, `005930.KS` 표기)도 그대로 읽힙니다 — 자동 변환되며 경고만 출력됩니다.

---

## 수기 입력은 어디까지 남는가

**토스 계좌 보유분은 완전 자동**입니다. 타 증권사 보유분만 `config.yaml`에 남고, 그것도 **`(수량, 평단)` 두 값**뿐입니다 — 종목명·현재가·통화는 토스 시세 API가 채웁니다.

예외가 하나 있습니다: **매수 시점 환율**. 토스도 이 값은 제공하지 않습니다(`averagePurchasePrice`는 원화폐 기준). 해외 종목의 환차손익까지 수익률에 반영하려면 `avg_exchange_rate`를 직접 입력해야 합니다.

|  | 매입액 환산 | 수익률에 포함되는 것 |
|---|---|---|
| `avg_exchange_rate` 입력 | 매수 시점 환율 | 주가 손익 **+ 환차손익** |
| 미입력 | 현재 환율 | 주가 손익만 |

미입력 종목이 있으면 리포트와 대시보드에 **"환차손익 미반영"** 배지가 붙습니다. 수치를 실제보다 넓게 해석하지 않도록 하기 위한 표시입니다.

---

## 사용법

### 연결 점검

```bash
python scripts/smoke_test.py
```

`/accounts` → `/holdings` → `/prices` → `/exchange-rate` → `/market-calendar`를 순서대로 호출합니다. GET만 사용하므로 주문이 나갈 수 없습니다.

**연속 2회 실행**해 보세요. 2회차에 토큰이 재발급되지 않아야 정상입니다.

> 토스는 **client당 유효 토큰이 1개**이고 재발급 시 이전 토큰을 즉시 무효화합니다. 그래서 토큰 캐시(`.toss_token.json`)는 성능 최적화가 아니라 정합성 요구사항이며, 파일 락으로 보호됩니다. 같은 자격증명으로 두 프로세스를 동시에 돌리면 서로를 로그아웃시킵니다.

### 일일 리포트

```bash
python main.py          # 또는 run_report.bat
```

Notion 페이지를 만들고 SQLite에 스냅샷 1행을 적재합니다. 순서상 **스냅샷 저장이 Notion·Gemini 호출보다 먼저**라, 외부 서비스가 죽어도 자산 이력은 남습니다.

실패 시 **0이 아닌 exit code**를 반환합니다 (`2` = 토스/설정 오류, `3` = 그 외). 스케줄러에서 실패를 감지할 수 있습니다.

### 대시보드

```bash
uvicorn src.dashboard.api:app --host 127.0.0.1 --port 8000
```

| 화면 | 내용 |
|---|---|
| **Overview** | 총자산 · 총손익(수수료·세금 차감) · 오늘 손익 · 매수가능금액, 자산 추이 차트, 배분 도넛, AI 코멘트, 매수 유의사항 경보 |
| **Holdings** | 종목별 수량·평단·현재가·손익·비중. **토스/수기 출처 배지** |
| **Reports** | 생성된 Notion 리포트 이력 |
| **Settings** | 설정 읽기전용 (자격증명 마스킹) |
| **Trading** | Phase 2 예정 — 현재 비활성 |

> ⚠️ **`127.0.0.1` 바인딩을 유지하세요.** 인증이 없고, Phase 2 이후에는 실계좌 제어 화면이 됩니다.

**자산 추이 차트는 과거를 소급 생성할 수 없습니다.** `main.py`를 실행할 때마다 한 점씩 쌓이므로 일찍 시작할수록 볼 것이 많아집니다. 스냅샷이 3개 미만이면 차트 대신 "이력 수집 중" 안내가 표시됩니다.

---

## 프로젝트 구조

```text
StockLens-AI/
├── main.py                  # 일일 리포트 실행
├── .env                     # 자격증명 (gitignore)
├── config.example.yaml      # 설정 템플릿
├── scripts/smoke_test.py    # 읽기전용 연결 점검
├── docs/ui/                 # 대시보드 디자인 원본 (DESIGN.md, mockup.html)
├── tests/                   # 63개 — 네트워크·자격증명 불필요
└── src/
    ├── config.py            # .env 우선 자격증명, 심볼 정규화, v1 하위호환
    ├── toss/
    │   ├── client.py        # 토큰 캐시·레이트리밋·백오프·쓰기 가드
    │   ├── account.py       # 계좌 · 보유주식 · 매수가능금액
    │   ├── market.py        # 시세 · 종목마스터 · 환율 · 캘린더 · 유의사항
    │   ├── ratelimit.py     # 그룹별 토큰버킷
    │   └── errors.py        # 에러코드 → 예외 타입
    ├── sources/             # toss_source(자동) · manual_source(config)
    ├── portfolio.py         # 소스 병합 → KRW 환산 스냅샷
    ├── models.py            # Position · PortfolioSnapshot (전 구간 Decimal)
    ├── store/               # SQLite (스냅샷 · 포지션 · 리포트 이력)
    ├── dashboard/           # FastAPI + 정적 프론트엔드
    ├── news.py              # Google News RSS
    ├── analyst.py           # Gemini 분석
    └── notion.py            # Notion 리포트 생성
```

### 설계상 주의점

- **금액은 전 구간 `Decimal`** — 토스 API가 모든 금액을 문자열로 주고, SQLite에도 `TEXT`로 저장합니다. `float`/`REAL`은 원 단위 오차가 누적됩니다.
- **주문 엔드포인트는 구조적으로 차단** — `TossClient`는 `allow_write=False`가 기본이며, GET 외의 메서드는 `TossWriteBlockedError`를 던집니다. Phase 2의 트레이딩 모듈만 `allow_write=True` 클라이언트를 생성합니다.
- **대시보드는 서버측 캐시 필수** — `ACCOUNT` 그룹이 **1 TPS**라 브라우저 탭마다 API를 호출하면 즉시 429입니다. 모든 탭이 서버가 소유한 캐시 하나를 공유합니다.

---

## 테스트

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

63개 전부 네트워크·자격증명 없이 실행됩니다 (HTTP 모킹). 커버 범위: 토큰 캐시 재사용, 401 재발급 1회 제한, 429 `Retry-After` 준수, 쓰기 가드, KRW 환산·환차손익, 중복 심볼 병합, `Decimal` 정밀도, 대시보드 API·캐시.

---

## 진행 상태

| Phase | 내용 | 상태 |
|---|---|---|
| **1. 리포트 + 대시보드** | 토스 API 조회 → Notion → SQLite → 대시보드 | ✅ 완료 |
| **2. 자동매매** | 전략 엔진 · 리스크 게이트 · 조건주문(OCO) 손절 | 설계 완료 |
| **3. 대시보드 확장** | 전략 성과 · 주문 이력 · 킬 스위치 | Phase 2 이후 |

### 아키텍처 변천

| 항목 | 키움 OpenAPI (v0) | yfinance 하이브리드 (v1) | **토스 Open API (v2)** |
|---|---|---|---|
| 국내+해외 통합 | ❌ 모듈 분리 | ✅ | ✅ 단일 REST API |
| 실행 환경 | 32bit Python + OCX | 64bit | 64bit, HTTP만 |
| 보유 수량·평단 | 국내만 자동 | ❌ 전량 수기 | ✅ 토스 계좌분 자동 |
| 수수료·세금 반영 손익 | ❌ | ❌ | ✅ |
| 전일 대비 손익 | ❌ | ❌ | ✅ |
| 자산 추이 이력 | ❌ | ❌ | ✅ SQLite |

---

## 참고 문서

| 문서 | 내용 |
|---|---|
| [DESIGN_Toss_API_Migration.md](./DESIGN_Toss_API_Migration.md) | Phase 1 설계 — 토스 API 조사 결과와 전환 근거 |
| [DESIGN_Trading_and_Dashboard.md](./DESIGN_Trading_and_Dashboard.md) | Phase 2·3 설계 — 자동매매 안전장치 10종, 대시보드 |
| [ISSUE_Kiwoom_Global_Limit.md](./ISSUE_Kiwoom_Global_Limit.md) | 키움 API 폐기 배경 |
| [FEATURE_AI_Analyst.md](./FEATURE_AI_Analyst.md) | AI 애널리스트 |

토스 Open API 공식 문서: https://developers.tossinvest.com/docs
