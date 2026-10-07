"""Plugin Host process execution with bounded single-response capture."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from testbox.core.errors import ErrorCode


@dataclass(frozen=True)
class HostExecution:
    payload: dict[str, Any]
    returncode: int
    stderr: str
    pid: int


class _Capture:
    """Keep a bounded response prefix or diagnostic tail, never a spool file."""

    def __init__(self, limit: int, *, tail: bool = False):
        self.limit = limit
        self.tail = tail
        self.buffer = bytearray()
        self.exceeded = threading.Event()

    def feed(self, chunk: bytes) -> None:
        remaining = self.limit - len(self.buffer)
        if len(chunk) > remaining:
            self.exceeded.set()
        if self.tail:
            self.buffer.extend(chunk)
            del self.buffer[:max(0, len(self.buffer) - self.limit)]
        else:
            self.buffer.extend(chunk[:remaining])


class ProcessRunner:
    # Limits apply to transport bytes, not plugin output artifacts. GUI files
    # are also checked while the Host runs; this is not an OS disk quota.
    MAX_RESPONSE_BYTES = 8 * 1024 * 1024
    MAX_STDERR_BYTES = 64 * 1024
    READ_CHUNK_BYTES = 64 * 1024
    CHECK_INTERVAL_SECONDS = 0.02

    def __init__(self, root: Path, *, timeout_seconds: float):
        self.root = root
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
        return {"status": "failed", "message": message,
                "data": {"error_code": code, **details}, "files": [], "warnings": []}

    @staticmethod
    def _kill_and_wait(process: Any) -> None:
        # Reaping is mandatory even if the Host exited between detection/kill.
        kill_error: OSError | None = None
        try:
            process.kill()
        except OSError as error:
            kill_error = error
        try:
            # A failed kill must not turn error cleanup into an infinite wait.
            process.wait(timeout=1.0 if kill_error is not None else None)
        except BaseException as wait_error:
            if kill_error is not None:
                raise kill_error from wait_error
            raise
        if kill_error is not None and not isinstance(kill_error, ProcessLookupError):
            raise kill_error

    @classmethod
    def _cleanup_process(cls, process: Any) -> None:
        original_error = sys.exc_info()[1]
        cleanup_errors: list[BaseException] = []
        try:
            poll = getattr(process, "poll", None)
            running = poll() is None if poll is not None else process.returncode is None
            if running:
                cls._kill_and_wait(process)
        except BaseException as error:
            cleanup_errors.append(error)
        # A failed kill/wait must not skip pipe closure or replace the error
        # that caused cleanup (especially an on_started/history exception).
        for name in ("stdin", "stdout", "stderr"):
            pipe = getattr(process, name, None)
            if pipe is not None:
                try:
                    pipe.close()
                except BaseException as error:
                    cleanup_errors.append(error)
        if cleanup_errors:
            if original_error is None:
                raise cleanup_errors[0]
            for error in cleanup_errors:
                original_error.add_note(f"Host resource cleanup also failed: {type(error).__name__}")

    def _exchange(self, process: Any, input_text: str | None,
                  response_path: Path | None) -> tuple[bytes, bytes, str | None]:
        stdout = _Capture(self.MAX_RESPONSE_BYTES)
        stderr = _Capture(self.MAX_STDERR_BYTES, tail=True)
        # Compatibility for existing lightweight Popen test doubles. Real
        # Popen exposes these pipes and never takes the communicate() branch.
        if not all(hasattr(process, name) for name in ("stdin", "stdout", "stderr")):
            try:
                out, err = process.communicate(input_text, timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                self._kill_and_wait(process)
                return b"", b"", "timeout"
            stdout.feed(out.encode("utf-8") if isinstance(out, str) else (out or b""))
            stderr.feed(err.encode("utf-8") if isinstance(err, str) else (err or b""))
            return bytes(stdout.buffer), bytes(stderr.buffer), "stdout" if stdout.exceeded.is_set() else None

        stopped = threading.Event()
        errors: list[Exception] = []
        threads: list[threading.Thread] = []

        def read_pipe(pipe: Any, capture: _Capture) -> None:
            try:
                # Raw fd reads work on Windows pipes as well as POSIX, avoid
                # select(), and never wait for a newline or accumulate it.
                while not stopped.is_set():
                    chunk = os.read(pipe.fileno(), self.READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    capture.feed(chunk)
                    if not capture.tail and capture.exceeded.is_set():
                        break
            except OSError as error:
                if not stopped.is_set():
                    errors.append(error)

        def write_request() -> None:
            try:
                if input_text is not None:
                    content = memoryview(input_text.encode("utf-8"))
                    while content and not stopped.is_set():
                        count = os.write(process.stdin.fileno(), content[:self.READ_CHUNK_BYTES])
                        content = content[count:]
            except BrokenPipeError:
                # The Host may report a failure before consuming the request.
                pass
            except OSError as error:
                if not stopped.is_set():
                    errors.append(error)
            finally:
                process.stdin.close()

        reason = None
        deadline = time.monotonic() + self.timeout_seconds
        try:
            for pipe, capture in ((process.stdout, stdout), (process.stderr, stderr)):
                if pipe is not None:
                    threads.append(threading.Thread(target=read_pipe, args=(pipe, capture), daemon=True))
            threads.append(threading.Thread(target=write_request, daemon=True))
            for thread in threads:
                thread.start()
            while True:
                if stdout.exceeded.is_set():
                    reason = "stdout"
                    break
                if response_path is not None:
                    try:
                        if response_path.stat().st_size > self.MAX_RESPONSE_BYTES:
                            reason = "response_file"
                            break
                    except FileNotFoundError:
                        pass
                if errors:
                    raise errors[0]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = "timeout"
                    break
                try:
                    process.wait(timeout=min(remaining, self.CHECK_INTERVAL_SECONDS))
                    break
                except subprocess.TimeoutExpired:
                    pass
            if reason is not None:
                self._kill_and_wait(process)
            # The process has exited, but readers may still have buffered pipe
            # data to drain. Bound this wait too (descendants can inherit pipes).
            drain_deadline = time.monotonic() + min(1.0, max(0.0, deadline - time.monotonic()))
            for thread in threads:
                thread.join(timeout=max(0.0, drain_deadline - time.monotonic()))
            if reason is None and any(thread.is_alive() for thread in threads):
                reason = "timeout"
            if reason is None and stdout.exceeded.is_set():
                reason = "stdout"
            if reason is None and errors:
                raise errors[0]
        finally:
            stopped.set()
            try:
                self._cleanup_process(process)
            finally:
                # Closing a pipe does not cancel an os.read already blocked
                # on an inherited fd held by a descendant. These daemon
                # readers can outlive run(); this is not process-tree cleanup.
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(timeout=0.1)
        return bytes(stdout.buffer), bytes(stderr.buffer), reason

    def run(self, request: dict[str, Any], *, task_id: str, on_started: Callable[[int], None] | None = None) -> HostExecution:
        environment = os.environ.copy()
        temp_dir: Path | None = None
        response_path: Path | None = None
        process = None
        try:
            if getattr(sys, "frozen", False):
                executable = Path(sys.executable).resolve()
                if executable.name.casefold() == "testbox-gui.exe":
                    # Preserve the windowed executable's UTF-8 file protocol.
                    temp_dir = Path(tempfile.mkdtemp(prefix="testbox-host-"))
                    request_path = temp_dir / "request.json"
                    response_path = temp_dir / "response.json"
                    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
                    command = [str(executable), "--plugin-host", "--request-file", str(request_path),
                               "--response-file", str(response_path)]
                else:
                    command = [sys.executable, "--plugin-host"]
            else:
                package_root = str(Path(__file__).resolve().parents[2])
                environment["PYTHONPATH"] = package_root + os.pathsep + environment.get("PYTHONPATH", "")
                command = [sys.executable, "-m", "testbox.core.host"]
            stdout_target = subprocess.DEVNULL if response_path is not None else subprocess.PIPE
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout_target,
                                       stderr=subprocess.PIPE, cwd=self.root, env=environment)
            # Callback errors propagate to Runtime only after the Host is reaped
            # and all temporary resources have been released in finally.
            if on_started is not None:
                on_started(process.pid)
            stdout, stderr, reason = self._exchange(
                process, None if response_path is not None else json.dumps(request), response_path)
            if reason is None and response_path is not None:
                try:
                    if not stat.S_ISREG(response_path.stat().st_mode):
                        raise ValueError("响应不是普通文件")
                    with response_path.open("rb") as response:
                        stdout = response.read(self.MAX_RESPONSE_BYTES + 1)
                    if len(stdout) > self.MAX_RESPONSE_BYTES:
                        reason = "response_file"
                except (OSError, ValueError):
                    stdout = b""
            if reason == "timeout":
                payload = self._failure(ErrorCode.TIMEOUT, f"插件执行超过 {self.timeout_seconds:g} 秒限制",
                                        timeout_seconds=self.timeout_seconds)
            elif reason is not None:
                payload = self._failure(ErrorCode.OUTPUT_TOO_LARGE, "插件 Host 响应超过大小限制",
                                        stream=reason, max_bytes=self.MAX_RESPONSE_BYTES)
            else:
                try:
                    event = json.loads(stdout)
                    valid = (event.get("protocol_version") == 1 and event.get("event") == "result"
                             and event.get("task_id") == task_id and isinstance(event.get("result"), dict))
                    if not valid:
                        raise ValueError("响应事件不符合协议")
                    payload = event["result"]
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError):
                    payload = self._failure(ErrorCode.HOST_PROTOCOL_ERROR, "插件 Host 协议错误")
            if process.returncode and payload.get("status") == "success":
                payload = self._failure(ErrorCode.HOST_CRASHED, "插件 Host 异常退出", exit_code=process.returncode)
            return HostExecution(payload, process.returncode, stderr.decode("utf-8", errors="replace"), process.pid)
        finally:
            try:
                if process is not None:
                    self._cleanup_process(process)
            finally:
                if temp_dir is not None:
                    shutil.rmtree(temp_dir, ignore_errors=True)
