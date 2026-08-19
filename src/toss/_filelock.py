"""A small cross-platform advisory file lock.

Used to serialise access to the OAuth token cache. Toss keeps exactly one
valid access token per client, and issuing a new one immediately invalidates
the previous one - so two StockLens processes refreshing at the same time
would log each other out. The lock makes "check the cache, refresh if stale,
write it back" atomic across processes.
"""

import os
import time

if os.name == "nt":
    import msvcrt

    def _lock(handle):
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(handle):
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _unlock(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class FileLock:
    """Context manager holding an exclusive lock on ``<path>.lock``."""

    def __init__(self, path, timeout=10.0):
        self.path = f"{path}.lock"
        self.timeout = timeout
        self._handle = None

    def __enter__(self):
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)

        deadline = time.monotonic() + self.timeout
        # msvcrt.locking blocks but raises OSError after ~10 tries, so retry
        # until the deadline rather than failing on first contention.
        while True:
            self._handle = open(self.path, "a+b")
            try:
                self._handle.seek(0)
                _lock(self._handle)
                return self
            except OSError:
                self._handle.close()
                self._handle = None
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"토큰 캐시 락을 {self.timeout}초 안에 얻지 못했습니다: {self.path}"
                    )
                time.sleep(0.1)

    def __exit__(self, exc_type, exc, tb):
        if self._handle is not None:
            try:
                _unlock(self._handle)
            finally:
                self._handle.close()
                self._handle = None
        return False
