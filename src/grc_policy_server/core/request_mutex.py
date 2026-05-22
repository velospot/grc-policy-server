from __future__ import annotations

import fcntl
import os
from pathlib import Path
from threading import Lock


class RequestMutex:
    """Best-effort cross-process mutex using a lock file."""

    def __init__(self, lock_file: str) -> None:
        self._lock_file = Path(lock_file)
        self._guard = Lock()
        self._fd: int | None = None

    def acquire_nowait(self) -> bool:
        with self._guard:
            if self._fd is not None:
                return False

            self._lock_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._lock_file, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                return False
            except Exception:
                os.close(fd)
                raise

            self._fd = fd
            return True

    def release(self) -> None:
        with self._guard:
            if self._fd is None:
                return

            fd = self._fd
            self._fd = None
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def locked(self) -> bool:
        with self._guard:
            return self._fd is not None
