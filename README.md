<p align="center">
  <img src="assets/logo.png" alt="M7 Terminal" width="120"/>
</p>

<h1 align="center">M7 Terminal</h1>

<p align="center">
  토스증권 Open API 기반 포트폴리오 리포트 · 대시보드
</p>

---

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
TOSS_CLIENT_ID=tsck_live_xxxxxxxxxxxx
TOSS_CLIENT_SECRET=tssk_live_xxxxxxxxxxxx
GOOGLE_AI_API_KEY=xxxxxxxxxxxx        # 선택. 없으면 AI 분석만 생략
                                      # 발급: https://aistudio.google.com/apikey
NOTION_TOKEN=ntn_xxxxxxxxxxxx         # 일일 리포트용
NOTION_DATABASE_ID=2f1a8b3c4d5e...    # DB URL 의 ?v= 앞 32자
```

교체를 잊으면 앱이 *"`.env`의 TOSS_CLIENT_ID가 아직 플레이스홀더입니다"* 라고 알려줍니다.

### Notion 토큰 받기

Claude 등의 **MCP 커넥터는 쓸 수 없습니다.** OAuth 방식이라 재사용 가능한
토큰을 내주지 않기 때문에, Internal Integration 을 따로 만들어야 합니다.

1. https://www.notion.so/profile/integrations → **New integration** (Type: Internal)
   → **Internal Integration Secret** 복사 (`ntn_` 또는 예전 계정은 `secret_` 로 시작)
2. ⚠️ **리포트를 넣을 데이터베이스 페이지에서 `⋯` → Connections → 해당 integration 연결.**
   이 단계를 빼먹으면 토큰이 맞아도 API 가 404 를 반환합니다.
3. URL 의 32자 hex 문자열이 ID 입니다.
   ```
   https://app.notion.com/p/워크스페이스/제목-1a30084c658a4e78944080096889dbfb?pvs=28
                                            └──────────── 이 부분 ────────────┘
   ```

**데이터베이스와 일반 페이지 둘 다 지원합니다.**

| 대상 | 리포트 생성 방식 | 요구사항 |
|---|---|---|
| 데이터베이스 (URL 에 `?v=` 있음) | 새 행으로 추가 | `Report`(제목) · `Date`(날짜) 속성 필요 |
| 일반 페이지 (`/p/` 링크) | 하위 페이지로 추가 | 없음 |

`python scripts/smoke_test.py` 가 토큰·연결·대상 종류를 한 번에 점검해 줍니다.

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

`/accounts` → `/holdings` → `/prices` → `/exchange-rate` → `/market-calendar` → Notion → Gemini 를 순서대로 점검합니다. 토스 호출은 GET만 사용하므로 주문이 나갈 수 없습니다.

Gemini 단계는 토큰 한 개짜리 질문을 실제로 던져 봅니다. 키가 틀렸거나 모델명이 폐기되면 리포트 안에 사과 한 줄로만 남아 놓치기 쉬운데, 여기서는 소리 내어 실패합니다.

**연속 2회 실행**해 보세요. 2회차에 토큰이 재발급되지 않아야 정상입니다.

> 토스는 **client당 유효 토큰이 1개**이고 재발급 시 이전 토큰을 즉시 무효화합니다. 그래서 토큰 캐시(`.toss_token.json`)는 성능 최적화가 아니라 정합성 요구사항이며, 파일 락으로 보호됩니다. 같은 자격증명으로 두 프로세스를 동시에 돌리면 서로를 로그아웃시킵니다.

### 일일 리포트

```bash
python main.py          # 또는 run_report.bat
```

Notion 페이지를 만들고 SQLite에 스냅샷 1행을 적재합니다. 순서상 **스냅샷 저장이 Notion·Gemini 호출보다 먼저**라, 외부 서비스가 죽어도 자산 이력은 남습니다.

실패 시 **0이 아닌 exit code**를 반환합니다 (`2` = 토스/설정 오류, `3` = 그 외). 스케줄러에서 실패를 감지할 수 있습니다.

### 자동 실행 (Windows 작업 스케줄러)

```powershell
schtasks /Query /TN "M7 Terminal Daily Report" /V /FO LIST   # 상태 확인
schtasks /Run   /TN "M7 Terminal Daily Report"               # 지금 한 번 실행
schtasks /Delete /TN "M7 Terminal Daily Report" /F           # 등록 해제
```

> 이미 `StockLens-AI Daily Report`라는 이름으로 등록해 두셨다면 위 명령은 그 작업을 찾지 못합니다.
> 기존 작업을 지우고 새 이름으로 다시 등록하세요:
> ```powershell
> schtasks /Delete /TN "StockLens-AI Daily Report" /F
> schtasks /Create /TN "M7 Terminal Daily Report" /TR "<run_report.bat 전체 경로> --scheduled" /SC DAILY /ST 10:00
> ```

매일 **10:00**에 `run_report.bat --scheduled`를 실행합니다. 미국장 마감(서머타임 기준 06:00 KST) 이후이고, **PC를 쓰기 시작하는 09:00보다 뒤**입니다 — 이 작업은 `LogonType=Interactive`라 로그온 상태에서만 돌기 때문에, 사용 시간대 안에 트리거가 들어와야 그날 바로 실행됩니다.

| 설정 | 이유 |
|---|---|
| `--scheduled` 인자 | 이 인자가 없으면 `run_report.bat`은 `pause`로 멈춘다. 스케줄러에서는 작업이 영원히 끝나지 않는다 |
| StartWhenAvailable | PC가 꺼져 있어 놓친 실행을 다음 부팅 때 따라잡는다. 스냅샷은 소급 생성이 불가능하므로 하루를 통째로 잃지 않는 편이 낫다 |
| MultipleInstances=IgnoreNew | 토스는 client당 유효 토큰이 1개다. 겹쳐 돌면 서로를 로그아웃시킨다 |
| ExecutionTimeLimit 30분 | 외부 API가 응답하지 않을 때 무한정 매달리지 않는다 |

로그는 `logs/report_YYYY-MM-DD.log`에 날짜별로 쌓입니다(gitignore 대상). 스케줄러가 기록하는 **Last Run Result**가 그대로 `main.py`의 exit code입니다.

> 콘솔로 리다이렉트된 출력은 cp949를 따라 한글이 깨지므로, 배치 파일이 `PYTHONIOENCODING=utf-8`을 지정합니다.

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
M7-Terminal/
├── main.py                  # 일일 리포트 실행
├── .env                     # 자격증명 (gitignore)
├── config.example.yaml      # 설정 템플릿
├── scripts/smoke_test.py    # 읽기전용 연결 점검
├── docs/ui/                 # 대시보드 디자인 원본 (DESIGN.md, mockup.html)
├── tests/                   # 83개 — 네트워크·자격증명 불필요
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

83개 전부 네트워크·자격증명 없이 실행됩니다 (HTTP 모킹). 커버 범위: 토큰 캐시 재사용, 401 재발급 1회 제한, 429 `Retry-After` 준수, 쓰기 가드, KRW 환산·환차손익, 중복 심볼 병합, `Decimal` 정밀도, 대시보드 API·캐시.

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
