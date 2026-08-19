# 작업일지

프로젝트 진행 기록. 최신 항목이 위에 옵니다.

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

### 다음

- `config.yaml` 작성 (타 증권사 보유 종목 + Notion 연동) → `main.py` 첫 실행 → 스냅샷 적재 시작
- Phase 2: `src/toss/trading.py`부터. 구현 순서는 **리스크 게이트 → 주문 실행 → 조건주문(OCO 손절)** — 주문 코드가 먼저 동작하면 안전장치가 뒤로 밀린다
- 별건: `google-generativeai` SDK가 deprecated 상태이고 기본 모델도 구형(`gemini-1.5-flash`). 토스 마이그레이션과 무관하므로 분리해서 진행
