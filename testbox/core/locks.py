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
        self.local_lock.acquire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+b")
            self.handle.seek(0)
            self.handle.write(b"0")
            self.handle.flush()
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                while True:
                    try:
                        msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
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
