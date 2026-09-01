# Runs main.py / trade.py as Cloud Run Jobs. See DESIGN_Cloud_Migration.md.
FROM python:3.12-slim

# Toss/Notion 응답에 한글이 섞여 있어 run_report.bat이 Windows 콘솔용으로 두던
# 인코딩 강제를 컨테이너에서도 유지한다.
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUTF8=1

# The base image runs on UTC. Snapshot timestamps are pinned to KST in code
# (src/store/repo.py), but date.today() elsewhere - the audit log, the report
# header, the "is today a rebalance day" check - would still be nine hours
# off, which near midnight is the wrong day entirely.
ENV TZ=Asia/Seoul
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
