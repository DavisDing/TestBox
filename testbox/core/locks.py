"""Cross-process plugin execution locks."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[Path, threading.Lock] = {}


def _process_lock(path: Path) -> threading.Lock:
    resolved = path.resolve()
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(resolved, threading.Lock())


class PluginExecutionLock:
    """Serialize execution for plugins that declare concurrency=false."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self.local_lock = _process_lock(self.path)
        self.handle = None

    def __del__(self):
        self.release()

    def acquire(self) -> None:
        self._acquire(blocking=True)

    def try_acquire(self) -> bool:
        """Acquire without waiting; maintenance skips work owned by a live task."""
        return self._acquire(blocking=False)

    def _acquire(self, *, blocking: bool) -> bool:
        if self.handle is not None:
            raise RuntimeError("同一个锁实例不能重复获取")
        if not self.local_lock.acquire(blocking=blocking):
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Do not use ``a+b`` here.  On Windows, append mode can make the
            # descriptor's file position/file-region behavior surprising for
            # ``msvcrt.locking``.  Open the lock file without append semantics
            # and ensure that the byte we lock actually exists.
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o666)
            self.handle = os.fdopen(fd, "r+b", buffering=0)
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                while True:
                    try:
                        msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if not blocking:
                            self.handle.close()
                            self.handle = None
                            self.local_lock.release()
                            return False
                        time.sleep(0.05)
            else:
                import fcntl

                try:
                    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                    fcntl.flock(self.handle.fileno(), flags)
                except BlockingIOError:
                    self.handle.close()
                    self.handle = None
                    self.local_lock.release()
                    return False
            return True
        except Exception:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            self.local_lock.release()
            raise

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
            self.local_lock.release()
