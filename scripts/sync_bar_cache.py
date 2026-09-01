"""Sync bars.db to/from GCS around a Cloud Run Job execution.

The bar cache (src/data/cache.py) is deliberately a rebuildable local file,
never Firestore - a full universe backfill is too many writes for Firestore's
free tier. On Cloud Run the local disk is thrown away after every execution,
so without this the same rebuild cost is paid on every single run. GCS is
just a place to park that one file between runs; losing it costs one slow
run, never correctness (see src/data/cache.py's own docstring).

Usage: python scripts/sync_bar_cache.py download|upload
Controlled entirely by M7_BAR_CACHE_GCS_URI (gs://bucket/path/bars.db) and
M7_BAR_CACHE_PATH (local path, defaults to <repo_root>/bars.db, same default
src/data/cache.py uses). Either direction is best-effort: a failure here is
never fatal to the batch job it wraps.
"""

import os
import sys
from pathlib import Path

_DEFAULT_LOCAL_PATH = Path(__file__).resolve().parents[1] / "bars.db"


def _local_path():
    return Path(os.environ.get("M7_BAR_CACHE_PATH", _DEFAULT_LOCAL_PATH))


def _blob():
    uri = os.environ.get("M7_BAR_CACHE_GCS_URI")
    if not uri or not uri.startswith("gs://"):
        return None
    from google.cloud import storage

    bucket_name, _, blob_path = uri.removeprefix("gs://").partition("/")
    if not bucket_name or not blob_path:
        print(f"sync_bar_cache: malformed M7_BAR_CACHE_GCS_URI={uri!r}, skipping")
        return None
    return storage.Client().bucket(bucket_name).blob(blob_path)


def download():
    blob = _blob()
    if blob is None:
        return
    local = _local_path()
    try:
        if blob.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(local))
            print(f"sync_bar_cache: downloaded {blob.name} -> {local}")
        else:
            print(f"sync_bar_cache: no cache at {blob.name} yet, starting fresh")
    except Exception as exc:  # noqa: BLE001 - a cache miss, not a failure
        print(f"sync_bar_cache: download skipped ({exc})")


def upload():
    blob = _blob()
    if blob is None:
        return
    local = _local_path()
    if not local.exists():
        return
    try:
        blob.upload_from_filename(str(local))
        print(f"sync_bar_cache: uploaded {local} -> {blob.name}")
    except Exception as exc:  # noqa: BLE001 - next run just rebuilds instead
        print(f"sync_bar_cache: upload skipped ({exc})")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "download":
        download()
    elif action == "upload":
        upload()
    else:
        print("usage: sync_bar_cache.py download|upload", file=sys.stderr)
        sys.exit(1)
