# 작업일지

프로젝트 진행 기록. 최신 항목이 위에 옵니다.

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
