#!/bin/sh
# Cloud Run Job entrypoint. Wraps the actual command (e.g. `python main.py`)
# with a best-effort bars.db sync so trade.py's 400-day lookback doesn't
# refetch the whole universe from Yahoo on every ephemeral run.
set -u

python scripts/sync_bar_cache.py download

"$@"
exit_code=$?

python scripts/sync_bar_cache.py upload

exit "$exit_code"
