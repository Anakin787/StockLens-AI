"""Delete the temporary demo rows from the audit log.

    python -m scripts.clear_demo_audit

Only touches entries whose summary starts with "[임시 데모]" - the real
entries this log exists for are append-only and are never removed here.
"""

import sys

from src.config import load_config
from src.store.repo import Store

MARKER = "[임시 데모]"


def run():
    load_config()
    store = Store()
    removed = 0
    for doc in store.client.collection("audit_log").stream():
        summary = (doc.to_dict() or {}).get("summary") or ""
        if summary.startswith(MARKER):
            doc.reference.delete()
            removed += 1
    print(f"임시 데모 항목 {removed}건을 삭제했습니다.")
    print(f"남은 감사 로그: {len(store.recent_audit(limit=200))}건")
    return 0


if __name__ == "__main__":
    sys.exit(run())
