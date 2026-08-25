"""Plugin Host process execution."""
from __future__ import annotations

import json
import os
import subprocess
import sys
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


class ProcessRunner:
    def __init__(self, root: Path, *, timeout_seconds: float):
        self.root = root
        self.timeout_seconds = timeout_seconds

    def run(self, request: dict[str, Any], *, task_id: str, on_started: Callable[[int], None] | None = None) -> HostExecution:
        environment = os.environ.copy()
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--plugin-host"]
        else:
            package_root = str(Path(__file__).resolve().parents[2])
            environment["PYTHONPATH"] = package_root + os.pathsep + environment.get("PYTHONPATH", "")
            command = [sys.executable, "-m", "testbox.core.host"]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=self.root, env=environment)
        if on_started is not None:
            on_started(process.pid)
        try:
            stdout, stderr = process.communicate(json.dumps(request), timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return HostExecution({"status": "failed", "message": f"插件执行超过 {self.timeout_seconds:g} 秒限制", "data": {"error_code": ErrorCode.TIMEOUT, "timeout_seconds": self.timeout_seconds}, "files": [], "warnings": []}, process.returncode or -9, "", process.pid)
        try:
            event = json.loads(stdout)
            valid = event.get("protocol_version") == 1 and event.get("event") == "result" and event.get("task_id") == task_id and isinstance(event.get("result"), dict)
            if not valid:
                raise ValueError("响应事件不符合协议")
            payload = event["result"]
        except (json.JSONDecodeError, ValueError, AttributeError):
            payload = {"status": "failed", "message": "插件 Host 协议错误", "data": {"error_code": ErrorCode.HOST_PROTOCOL_ERROR}, "files": [], "warnings": []}
        if process.returncode and payload.get("status") == "success":
            payload = {"status": "failed", "message": "插件 Host 异常退出", "data": {"error_code": ErrorCode.HOST_CRASHED, "exit_code": process.returncode}, "files": [], "warnings": []}
        return HostExecution(payload, process.returncode, stderr, process.pid)
