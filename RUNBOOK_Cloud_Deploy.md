# 런북: 이미지 빌드 → Cloud Run 배포

PC를 켜두지 않아도 배치가 돌게 하는 절차. **데이터(Firestore)는 이미 GCP에 있고,
이 문서는 남은 절반인 "컴퓨트와 트리거"를 옮기는 과정**이다.

> ⚠️ **이 문서의 명령은 아직 실제 프로젝트에서 실행해 검증하지 않았다.**
> 처음 따라갈 때 프로젝트 번호·서비스계정 이름·리전은 실제 값으로 확인하면서 진행하고,
> 어긋난 부분은 이 문서를 고쳐 둘 것.

---

## TL;DR — 두 번째부터는 이것만

📁 **로컬 WSL · 리포 루트**

```bash
cd /mnt/c/Users/seonb/orca/m7-terminal
gcloud run jobs deploy m7-daily --source . --region asia-northeast3
```

빌드·푸시·Job 갱신이 한 번에 끝난다. Docker Desktop을 켤 필요도 없다.
**0장(최초 1회 셋팅)은 처음 한 번만** 하면 된다.

---

## 이 문서를 읽는 법 — 명령을 "어디서" 치는가

모든 코드 블록 위에 실행 위치가 표시돼 있다. 세 가지뿐이다.

| 표시 | 어디서 | 왜 거기여야 하나 |
|---|---|---|
| 📁 **로컬 WSL · 리포 루트** | `cd /mnt/c/Users/seonb/orca/m7-terminal` 한 상태의 WSL 터미널 | **리포 파일이 필요한 명령.** `--source .`가 현재 폴더를 업로드하고, `config.yaml`·`bars.db`를 읽어 올린다. 다른 데서 치면 파일을 못 찾는다 |
| ☁️ **아무 셸이나** | 로컬 WSL이든, [GCP 콘솔](https://console.cloud.google.com)의 **Cloud Shell**(우상단 터미널 아이콘)이든 상관없음 | **GCP를 조작만 하는 명령.** 리포 파일을 안 쓴다. Cloud Shell은 gcloud가 이미 깔려 있고 로그인도 돼 있어서, 새 PC나 노트북에서 급할 때 편하다 |
| 🐳 **컨테이너 안** | `docker run`으로 띄운 컨테이너 내부 | 이미지에 뭐가 들어갔는지 확인할 때만 |

가장 흔한 실수는 **📁 표시가 붙은 명령을 리포 루트 밖에서 치는 것**이다.
`--source .`는 "지금 폴더를 통째로 올려라"는 뜻이라, 엉뚱한 폴더에서 치면 엉뚱한 걸 배포한다.
`0-6 시크릿 등록`도 `config.yaml`을 읽으므로 반드시 리포 루트여야 한다.

---

## 공통 변수

📁 **로컬 WSL · 리포 루트** (또는 ☁️ Cloud Shell — 어느 쪽이든 **그 셸을 새로 열 때마다** 다시 붙여넣어야 한다)

```bash
export PROJECT=quant-81f19          # .firebaserc의 default 프로젝트
export REGION=asia-northeast3       # 서울. Firestore 리전과 맞출 것 (0-1 참고)
export REPO=m7                      # Artifact Registry 저장소 이름
export SA=m7-runner                 # 실행용 서비스계정 이름
export SA_EMAIL=${SA}@${PROJECT}.iam.gserviceaccount.com
export BUCKET=gs://${PROJECT}-m7-cache
export IMAGE=${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/m7-terminal
```

> `export`는 그 터미널 창에만 유효하다. 창을 닫았다 열면 값이 사라지므로,
> 아래 명령에서 `$PROJECT`가 빈 값으로 들어가 이상하게 실패하면 이걸 다시 붙여넣었는지부터 확인한다.

---

## 0. 최초 1회 셋팅

### 0-1. gcloud 로그인과 프로젝트 지정

☁️ **아무 셸이나** — 단 `gcloud auth login`은 브라우저가 열려야 하므로 **로컬에서 하는 게 편하다**
(Cloud Shell은 이미 로그인 상태라 이 줄이 아예 필요 없다).

```bash
gcloud auth login
gcloud config set project $PROJECT
gcloud firestore databases list        # 기존 Firestore의 리전 확인 → REGION을 여기 맞춘다
```

Firestore와 Cloud Run 리전이 다르면 매 호출마다 왕복 지연이 붙는다. 굳이 다르게 둘 이유가 없다.

> WSL에서 브라우저가 안 열리면 `gcloud auth login --no-launch-browser`로 URL을 복사해 쓴다.

### 0-2. API 활성화

☁️ **아무 셸이나**

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com
```

### 0-3. 이미지 저장소

☁️ **아무 셸이나**

```bash
gcloud artifacts repositories create $REPO \
  --repository-format=docker --location=$REGION \
  --description="M7 Terminal container images"
```

### 0-4. bars.db 보관용 버킷

일봉 캐시는 의도적으로 Firestore가 아니라 로컬 SQLite다(`src/data/cache.py` 상단 주석 참고 —
유니버스 백필이 ~5만 write라 무료 티어를 넘긴다). Cloud Run은 실행이 끝나면 디스크가 사라지므로,
그 파일 하나를 GCS에 주차해 둔다. **잃어버려도 느린 실행 한 번일 뿐, 정합성 문제는 없다.**

☁️ **아무 셸이나** — 버킷 생성

```bash
gcloud storage buckets create $BUCKET --location=$REGION
```

📁 **로컬 WSL · 리포 루트** — 초기 업로드는 `bars.db` 파일이 있는 곳에서 해야 한다

```bash
gcloud storage cp bars.db $BUCKET/bars.db
```

> 22MB짜리 파일이다. 이걸 미리 올려두면 첫 클라우드 실행이 Yahoo에서 16년치를 다시 받지 않는다.

### 0-5. 서비스계정과 권한

☁️ **아무 셸이나**

```bash
gcloud iam service-accounts create $SA --display-name="M7 Terminal runner"

for ROLE in \
  roles/datastore.user \
  roles/storage.objectAdmin \
  roles/secretmanager.secretAccessor
do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:${SA_EMAIL}" --role="$ROLE"
done
```

> **서비스계정 JSON 키는 만들지 않는다.** Cloud Run에서는 붙여둔 서비스계정으로 ADC가 자동 동작하므로
> `GOOGLE_APPLICATION_CREDENTIALS`가 필요 없다. 그 변수는 로컬 개발 전용이다.

### 0-6. 시크릿 등록

이미지에는 비밀이 하나도 들어가지 않는다(`.dockerignore` 참고). 런타임에 주입한다.

📁 **로컬 WSL · 리포 루트** — `config.yaml`과 `.env` 값을 읽으므로 **반드시 리포 루트에서**

```bash
# config.yaml 통째로 (전략 파라미터 + 자격증명)
gcloud secrets create m7-config --data-file=config.yaml

# API 키들. .env를 셸에 불러온 뒤 실행한다
set -a && . ./.env && set +a
printf '%s' "$TOSS_CLIENT_ID"     | gcloud secrets create toss-client-id --data-file=-
printf '%s' "$TOSS_CLIENT_SECRET" | gcloud secrets create toss-client-secret --data-file=-
printf '%s' "$NOTION_TOKEN"       | gcloud secrets create notion-token --data-file=-
printf '%s' "$NOTION_DATABASE_ID" | gcloud secrets create notion-database-id --data-file=-
printf '%s' "$GOOGLE_AI_API_KEY"  | gcloud secrets create google-ai-api-key --data-file=-
```

> `printf`는 `echo`와 달리 끝에 개행을 안 붙인다. 토큰 끝에 `\n`이 붙으면 인증이 조용히 실패하므로
> 여기서는 `echo`를 쓰지 말 것.

값이 바뀌면 새 버전만 추가하면 된다. **이미지 재빌드는 필요 없다.**

📁 **로컬 WSL · 리포 루트**

```bash
gcloud secrets versions add m7-config --data-file=config.yaml
```

### 0-7. 토스 허용 IP

등록되지 않은 IP의 API 호출은 `403 edge-blocked`로 **전량 차단**된다(README 59행).
Cloud Run의 아웃바운드 IP는 기본적으로 동적이므로, 고정 IP를 만들어 그 IP를 등록해야 한다.

> ✅ **IP 등록은 완료됨.** 다만 등록한 IP가 **PC의 공인 IP**라면 Cloud Run에서는 통하지 않는다.
> 클라우드에서 처음 돌릴 때 `403`이 나오면 아래로 NAT 고정 IP를 만들어 그 주소를 추가 등록한다.

☁️ **아무 셸이나** — 필요해졌을 때만

```bash
# 개요만. 실제 적용 시 서브넷/커넥터 이름을 정하고 진행할 것.
gcloud compute addresses create m7-nat-ip --region=$REGION
gcloud compute routers create m7-router --network=default --region=$REGION
gcloud compute routers nats create m7-nat \
  --router=m7-router --region=$REGION \
  --nat-external-ip-pool=m7-nat-ip \
  --nat-all-subnet-ip-ranges

# 이 주소를 토스 WTS > 설정 > Open API > 허용 IP 관리 에 등록
gcloud compute addresses describe m7-nat-ip --region=$REGION --format='value(address)'
```

이후 Job에 `--vpc-connector`와 `--vpc-egress=all-traffic`을 붙여야 그 NAT를 타고 나간다.

---

## 1. 매번 하는 배포

### 1-1. (선택) 로컬 검증

클라우드 왕복 없이 빨리 깨지는지 보고 싶을 때만. **배포에 필수가 아니다.**
Docker Desktop이 실행 중이어야 한다.

📁 **로컬 WSL · 리포 루트**

```bash
docker build -t m7-terminal .

# 이미지에는 설정도 비밀도 없으므로 손으로 주입해야 돈다
docker run --rm \
  --env-file .env \
  -v "$PWD/config.yaml:/app/config.yaml:ro" \
  -v "$PWD/secrets:/secrets:ro" \
  -e GOOGLE_APPLICATION_CREDENTIALS=/secrets/<서비스계정>.json \
  m7-terminal python main.py
```

🐳 **컨테이너 안** — 비밀이 정말 안 들어갔는지 확인. 셋 다 "No such file"이어야 정상

```bash
docker run --rm m7-terminal ls /app/config.yaml /app/.env /app/bars.db
```

### 1-2. 배포

📁 **로컬 WSL · 리포 루트** — `--source .`가 현재 폴더를 업로드하므로 위치가 곧 배포 내용이다

```bash
gcloud run jobs deploy m7-daily --source . --region=$REGION
```

Cloud Build가 빌드 → Artifact Registry 푸시 → Job 갱신까지 한 번에 한다.
**Docker Desktop을 켤 필요가 없다** — 빌드가 GCP에서 일어난다.

<details>
<summary>로컬 이미지를 직접 올리고 싶다면 (3단계)</summary>

📁 **로컬 WSL · 리포 루트** — Docker Desktop 실행 중이어야 함

```bash
export TAG=$(git rev-parse --short HEAD)
gcloud auth configure-docker ${REGION}-docker.pkg.dev   # 최초 1회
docker build -t ${IMAGE}:${TAG} .
docker push ${IMAGE}:${TAG}
gcloud run jobs update m7-daily --image=${IMAGE}:${TAG} --region=$REGION   # ← 빠뜨리기 쉬움
```

**`:latest`에 덮어쓰기만 하면 반영되지 않는다.** Cloud Run은 배포 시점에 태그를 digest로
고정하므로, 같은 태그로 push해도 Job은 예전 이미지를 계속 실행한다. 그래서 마지막 `update`가
반드시 필요하고, 애초에 커밋 해시를 태그로 쓰면 이 혼동이 사라지고 롤백도 쉬워진다.
</details>

---

## 2. Job 정의

`ENTRYPOINT`가 `entrypoint.sh`이고 그 안에서 `"$@"`를 실행하므로, **컨테이너 인자가 곧 실행할 명령**이다.

☁️ **아무 셸이나** — 이미 올라간 이미지를 가리키기만 하므로 리포 파일이 필요 없다

```bash
# 일일 리포트 (main.py)
gcloud run jobs create m7-daily \
  --image=${IMAGE}:latest --region=$REGION \
  --service-account=$SA_EMAIL \
  --args=python,main.py \
  --task-timeout=30m --max-retries=1 \
  --set-env-vars="M7_BAR_CACHE_GCS_URI=${BUCKET}/bars.db" \
  --set-secrets="/app/config.yaml=m7-config:latest,\
TOSS_CLIENT_ID=toss-client-id:latest,\
TOSS_CLIENT_SECRET=toss-client-secret:latest,\
NOTION_TOKEN=notion-token:latest,\
NOTION_DATABASE_ID=notion-database-id:latest,\
GOOGLE_AI_API_KEY=google-ai-api-key:latest"

# 매매 엔진 (trade.py) — 위와 동일하되 이름과 인자만 다르다
gcloud run jobs create m7-trade \
  ... --args=python,trade.py
```

> **`--command`는 쓰지 말 것.** ENTRYPOINT를 덮어써서 `entrypoint.sh`를 건너뛰게 되고,
> 그러면 bars.db GCS 동기화가 사라져 매 실행마다 유니버스 전체를 다시 받는다. 반드시 `--args`만 쓴다.

기존 Job 수정은 `create` 대신 `update`. 이미 만든 뒤 시크릿 하나만 추가할 때도 마찬가지다.

### 관련 CLI 인자

| 명령 | 용도 |
|---|---|
| `python main.py` | 일일 리포트 (Notion 발행) |
| `python trade.py` | 매매 엔진 (기본 PAPER) |
| `python trade.py --reconcile` | 미체결 LIVE 주문 체결 확인 + OCO 등록 |
| `python trade.py --dry-run` | 리스크 게이트까지만, DB 기록 없음 |

`--live`는 아직 **코드에서 거부된다**(exit 4). 설계 6절 [10]에서 최소 수량 1주 검증 후에 열린다.

### 종료 코드

`main.py` / `trade.py` 공통: `0` 정상 · `2` 토스 API 오류 · `3` 예기치 못한 오류.
`trade.py`는 추가로 `1` 엔진 비활성 · `4` `--live` 차단.

---

## 3. 스케줄 (Cloud Scheduler)

☁️ **아무 셸이나**

```bash
gcloud scheduler jobs create http m7-daily-schedule \
  --location=$REGION \
  --schedule="35 23 * * 1-5" \
  --time-zone="Asia/Seoul" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/m7-daily:run" \
  --http-method=POST \
  --oauth-service-account-email=$SA_EMAIL

# Scheduler가 Job을 실행하려면 이 권한이 필요하다
gcloud run jobs add-iam-policy-binding m7-daily \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/run.invoker" --region=$REGION
```

시간은 실제 운용에 맞춰 조정할 것. 리밸런싱 요일은 `config.yaml`의 `rebalance_weekday`가
따로 정하므로(0=월요일, 그날이 휴장이면 그 주 첫 거래일), **스케줄은 매일 돌려도 무방하다.**

---

## 4. 확인 · 로그 · 롤백

☁️ **아무 셸이나** — 전부 GCP 조회/조작이라 리포 파일이 필요 없다.
급할 때 폰이나 다른 PC에서 Cloud Shell로 들어가 그대로 칠 수 있다.

```bash
# 즉시 한 번 실행
gcloud run jobs execute m7-daily --region=$REGION --wait

# 실행 이력
gcloud run jobs executions list --job=m7-daily --region=$REGION

# 로그
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="m7-daily"' \
  --limit=100 --format='value(textPayload)'

# 롤백 — 이전 커밋 해시 태그로 되돌린다
gcloud run jobs update m7-daily --image=${IMAGE}:<이전해시> --region=$REGION
```

---

## 5. 자주 걸리는 것들

| 증상 | 원인 |
|---|---|
| 코드를 고쳤는데 클라우드는 옛날 동작 | `push`만 하고 `jobs update`를 안 함. 태그가 digest로 고정돼 있다 |
| `$PROJECT`가 빈 값으로 들어가 실패 | 터미널을 새로 열고 "공통 변수" `export`를 다시 안 붙여넣음 |
| 엉뚱한 내용이 배포됨 | 📁 명령을 리포 루트 밖에서 실행. `--source .`는 "지금 폴더"를 올린다 |
| `403 edge-blocked` | 토스 허용 IP. 클라우드 IP가 등록돼 있는지 확인 (0-7) |
| 에러 없이 이상한 설정으로 매매 | `config.yaml` 마운트 누락. 아래 ⚠️ 참고 |
| 매 실행마다 Yahoo 재다운로드 | `M7_BAR_CACHE_GCS_URI` 미설정, 또는 `--command`로 entrypoint를 덮어씀 |
| Firestore 권한 오류 | 서비스계정에 `roles/datastore.user` 누락 |
| 급히 멈춰야 함 | 대시보드에서 킬 스위치 ON. Firestore `system/kill_switch` 문서 — **재배포 불필요, 즉시 반영** |

> ⚠️ **`config.yaml`이 없어도 크래시하지 않는다.** `_load_yaml()`은 파일이 없으면 빈 dict를
> 반환한다(`src/config.py:268`). 즉 마운트를 깜빡하면 의도한 전략이 아닌 기본값으로 조용히 주문이
> 나갈 수 있다. 클라우드 운용 전에 "설정이 비어 있으면 즉시 중단" 가드를 넣는 것이 좋다. **(미착수)**

---

## 6. 대시보드 (Cloud Run **Service**)

여기까지는 전부 Job — "실행하고 끝나는" 배치다. 대시보드는 **계속 떠 있어야** 하므로
같은 이미지를 Cloud Run **Service**로 따로 올린다.

> ⚠️ **순서가 중요하다.** 킬 스위치를 밖에서 내리려면 대시보드가 먼저 떠 있어야 한다.
> `Job 배포 → 대시보드 + IAP → 그 다음에 Scheduler 켜기` 순서를 지킬 것.
> 브레이크에 손이 닿기 전에 시동을 걸지 않는다.

### 6-1. 인증 없이 공개하면 안 되는 이유

`src/dashboard/api.py:5`에 그대로 적혀 있다 — **이 앱에는 인증이 없다.**
지금까지 안전했던 건 오직 `127.0.0.1`에 묶여 있었기 때문이다.

그대로 공개하면 **URL을 아는 누구나 킬 스위치를 내리고 포트폴리오 전체를 볼 수 있다.**
그래서 6-3의 IAP가 선택이 아니라 필수다. 6-2와 6-3은 반드시 붙여서 진행한다.

### 6-2. Service 배포

📁 **로컬 WSL · 리포 루트**

```bash
gcloud run deploy m7-dashboard --source . --region=$REGION \
  --service-account=$SA_EMAIL \
  --args=uvicorn,src.dashboard.api:app,--host,0.0.0.0,--port,8080 \
  --port=8080 \
  --no-allow-unauthenticated \
  --min-instances=0 \
  --set-secrets="/app/config.yaml=m7-config:latest,\
TOSS_CLIENT_ID=toss-client-id:latest,\
TOSS_CLIENT_SECRET=toss-client-secret:latest"
```

포인트 넷:

- **`--host 0.0.0.0`** — 로컬에서 쓰던 `127.0.0.1`이면 Cloud Run이 컨테이너에 요청을 넣지 못해
  배포가 그대로 실패한다. 이 한 줄이 로컬과 클라우드의 유일한 실행 차이다.
- **`--no-allow-unauthenticated`** — 기본을 잠근 상태로 띄운다. 6-3에서 IAP로 열 때까지
  아무도 못 들어온다. **이 플래그를 빼고 띄우는 순간 킬 스위치가 인터넷에 공개된다.**
- **`M7_BAR_CACHE_GCS_URI`를 설정하지 않는다** — 이유는 6-4.
- **`--min-instances=0`** — 안 볼 때는 0원. 대신 첫 접속이 몇 초 느리다. 급할 때 그 몇 초가
  아깝다면 `1`로 올린다(상시 과금).

Notion/AI 키는 대시보드가 쓰지 않으므로 뺐다. 화면에서 뭔가 비어 보이면 그때 추가한다.

### 6-3. IAP로 잠그기 — 내 구글 계정만

**코드 수정이 필요 없는 방식.** 구글 로그인 한 번이면 폰 브라우저에서도 그대로 열린다.

☁️ **아무 셸이나**

```bash
gcloud services enable iap.googleapis.com

# 이 서비스에 IAP를 켠다
gcloud run services update m7-dashboard --region=$REGION --iap

# 내 계정만 통과시킨다
gcloud run services add-iam-policy-binding m7-dashboard --region=$REGION \
  --member="user:jujeong@ncurity.com" \
  --role="roles/run.invoker"
```

> IAP 최초 활성화 때 GCP 콘솔에서 **OAuth 동의 화면**을 한 번 구성하라고 요구할 수 있다.
> 또한 프로젝트 설정에 따라 Cloud Run 직접 IAP 대신 **외부 HTTPS 로드밸런서 + IAP** 경로를
> 요구하는 경우가 있다. 그때는 콘솔의 IAP 페이지 안내를 따르는 편이 빠르다.
> **이 부분은 실제로 실행해 검증하지 않았으므로, 진행하면서 실제 절차로 고쳐 둘 것.**

배포 후 URL 확인:

```bash
gcloud run services describe m7-dashboard --region=$REGION --format='value(status.url)'
```

**접속 확인은 시크릿 창에서 한다.** 로그인된 창에서는 통과해서, 잠긴 건지 아닌지 구분이 안 된다.
시크릿 창에서 구글 로그인 화면이 뜨면 정상이고, 대시보드가 바로 보이면 잠기지 않은 것이다.

### 6-4. 대시보드에는 bars.db를 공유하지 않는다

대시보드도 전일 종가 계산에 일봉을 쓴다(`src/dashboard/service.py:96`, 12일치).
그런데 Job과 같은 GCS 파일을 공유하게 두면 **`entrypoint.sh`가 종료 시 업로드**하므로,
12일치만 든 얇은 캐시가 Job의 16년치 캐시를 덮어쓸 수 있다. Service는 인스턴스가 여럿 뜨고
종료 시점도 제각각이라 경합이 생긴다.

그래서 Service에는 `M7_BAR_CACHE_GCS_URI`를 **설정하지 않는다.** 동기화 스크립트는 이 변수가
없으면 아무것도 하지 않고 그냥 빠진다(`scripts/sync_bar_cache.py`의 `_blob()`).
대신 인스턴스가 새로 뜰 때마다 yfinance에서 12일치를 받는데, 이건 충분히 싸다.

---

## 남은 일

- [ ] **빈 config 가드** — 5장 ⚠️ 항목
- [ ] **대시보드 배포 실행** — 절차는 6장에 정리됨. 아직 실행하지 않았고, IAP 경로는 미검증
- [ ] **실패 알림** — Job 실패가 조용히 묻히지 않도록 로그 기반 알림 설정
