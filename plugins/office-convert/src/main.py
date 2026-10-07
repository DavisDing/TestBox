"""Bounded legacy Office conversion; no imports from Core or other plugins."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
import xml.etree.ElementTree as ET

from testbox.sdk import Context, PluginError, Result

OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
MAX_FILES = 100
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
MAX_BATCH_OUTPUT_BYTES = 400 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ZIP_ENTRIES = 10000
MAX_LOG_BYTES = 64 * 1024
CHUNK = 64 * 1024
TARGETS = {".xls": ".xlsx", ".doc": ".docx", ".ppt": ".pptx"}
FILTERS = {".xlsx": "xlsx:Calc MS Excel 2007 XML", ".docx": "docx:Office Open XML Text",
           ".pptx": "pptx:Impress MS PowerPoint 2007 XML"}
ROOTS = {
    ".xlsx": ("xl/workbook.xml", "workbook", "spreadsheetml", "spreadsheetml.sheet"),
    ".docx": ("word/document.xml", "document", "wordprocessingml", "wordprocessingml.document"),
    ".pptx": ("ppt/presentation.xml", "presentation", "presentationml", "presentationml.presentation"),
}
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


class ItemFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class ProcessOutcome:
    returncode: int | None
    timed_out: bool = False
    reaped: bool = True
    output_limited: bool = False
    stdout: bytes = b""


class WindowsJob:
    """Own this native converter's process tree; close kills descendants.

    Assignment failure is fail-closed, never a fallback to killing only the
    wrapper. Windows support is implemented but NOT native acceptance tested.
    """
    def __init__(self, process: subprocess.Popen):
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount",
                "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("cannot create converter job")
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError("cannot configure converter job")
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            self.close()
            raise OSError("cannot isolate converter job")

    def kill(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def check_deadline(deadline: float):
    if time.monotonic() >= deadline:
        raise ItemFailure("BATCH_TIMEOUT", "批次 deadline 已到")


def kill_owned(process: subprocess.Popen, job: WindowsJob | None) -> bool:
    """Kill ONLY our session/job, including children after wrapper exit."""
    try:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            # Wrapper may exit before its children; always finish its group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif job is not None:
            job.kill()
        elif process.poll() is None:
            process.kill()  # Only used when job setup itself failed.
        process.wait(timeout=2)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_process(argv: list[str], work: Path, timeout: float, monitored: Path | None = None) -> ProcessOutcome:
    """Bound both pipes to 64KiB, discard excess and stop the own process tree.

    stdout/stderr are never logged, persisted, or returned except the bounded
    version string. Threads drain both pipes concurrently to avoid deadlocks.
    """
    exceeded = threading.Event()
    retained = [bytearray(), bytearray()]
    process = None
    job = None
    readers: list[threading.Thread] = []
    outcome = ProcessOutcome(None)

    def drain(pipe, index):
        total = 0
        try:
            while chunk := pipe.read(CHUNK):
                total += len(chunk)
                if len(retained[index]) < MAX_LOG_BYTES:
                    retained[index].extend(chunk[:MAX_LOG_BYTES - len(retained[index])])
                if total > MAX_LOG_BYTES:
                    exceeded.set()
        except (OSError, ValueError):
            pass
        finally:
            pipe.close()

    end = time.monotonic() + max(0, timeout)
    try:
        process = subprocess.Popen(argv, cwd=str(work), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   shell=False, start_new_session=(os.name == "posix"),
                                   creationflags=(0x00000004 if os.name == "nt" else 0))
        if os.name == "nt":
            job = WindowsJob(process)
            # Assign the suspended native wrapper before it can spawn children.
            # Popen closes its primary-thread handle, so resume via the native
            # process API after assignment; failure stays fail-closed.
            import ctypes
            resume = ctypes.WinDLL("ntdll").NtResumeProcess
            resume.argtypes = [ctypes.c_void_p]
            resume.restype = ctypes.c_long
            if resume(int(process._handle)) != 0:
                raise OSError("cannot resume isolated converter")
        for index, pipe in enumerate((process.stdout, process.stderr)):
            thread = threading.Thread(target=drain, args=(pipe, index), daemon=True)
            thread.start()
            readers.append(thread)
        while process.poll() is None:
            remaining = end - time.monotonic()
            if remaining <= 0:
                outcome.timed_out = True
                break
            if exceeded.is_set():
                outcome.output_limited = True
                break
            if monitored is not None:
                try:
                    if monitored.is_symlink() or (monitored.is_file() and monitored.stat().st_size > MAX_OUTPUT_BYTES):
                        outcome.output_limited = True
                        break
                except OSError:
                    pass
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                pass
        outcome.reaped = kill_owned(process, job)
        outcome.returncode = process.returncode
        for thread in readers:
            thread.join(timeout=0.5)
        if any(t.is_alive() for t in readers):
            outcome.reaped = False  # A surviving descendant may still hold a pipe.
        outcome.output_limited |= exceeded.is_set()
        outcome.stdout = bytes(retained[0])
        return outcome
    except (OSError, ValueError):
        if process is not None:
            outcome.reaped = kill_owned(process, job)
        return outcome
    finally:
        if job is not None:
            job.close()


def find_engine(config: dict[str, Any]) -> Path | None:
    configured = config.get("soffice_path")
    if configured is not None:
        if not isinstance(configured, str) or not configured.strip() or "\x00" in configured:
            raise PluginError("CONFIG_INVALID", "soffice_path 必须是单个可信可执行文件路径")
        candidates = [configured]
    else:
        candidates = [p for name in ("soffice", "libreoffice") if (p := shutil.which(name))]
        candidates += ["/Applications/LibreOffice.app/Contents/MacOS/soffice",
                       "/Applications/LibreOfficeDev.app/Contents/MacOS/soffice",
                       "/usr/bin/soffice", "/usr/local/bin/soffice", "/opt/homebrew/bin/soffice"]
        if os.name == "nt":
            for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                         os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
                candidates += [str(Path(base) / "LibreOffice" / "program" / name) for name in ("soffice.exe", "soffice.com")]
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if os.name == "nt" and path.suffix.lower() not in {".exe", ".com"}:
            if configured is not None:
                raise PluginError("CONFIG_INVALID", "Windows 引擎仅允许 native exe/com，不接受 batch 脚本")
            continue
        try:
            path = path.resolve(strict=True)
            if path.is_file() and os.access(path, os.X_OK):
                return path
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def make_registry(profile: Path):
    user = profile / "user"
    user.mkdir(parents=True, exist_ok=False)
    (user / "registrymodifications.xcu").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema">
 <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
  <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
  <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
  <prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop>
  <prop oor:name="DisableOLEAutomation" oor:op="fuse"><value>true</value></prop>
  <prop oor:name="SecureURL" oor:op="fuse"><value/></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Calc/Content/Update">
  <prop oor:name="Link" oor:op="fuse"><value>1</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Writer/Content/Update">
  <prop oor:name="Link" oor:op="fuse"><value>0</value></prop>
  <prop oor:name="Field" oor:op="fuse"><value>false</value></prop>
  <prop oor:name="Chart" oor:op="fuse"><value>false</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Common/Misc">
  <prop oor:name="AllowInteractive" oor:op="fuse"><value>false</value></prop>
 </item>
</oor:items>
''', encoding="utf-8")
    # TrustedAuthors is an empty set in a fresh profile; no inherited profile,
    # extension registry, secure URLs or author certificate entries are copied.


def safe_output(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or "\\" in relative or "\x00" in relative or relative.startswith("/") or any(p in {".", ".."} or ":" in p for p in parts):
        raise PluginError("INVALID_OUTPUT_PATH", "输出路径不合法")
    target = root.joinpath(*parts)
    for path in [target, *target.parents]:
        if path == root.parent:
            break
        if path.is_symlink():
            raise PluginError("INVALID_OUTPUT_PATH", "输出路径不能经过符号链接")
    try:
        target.resolve().relative_to(root.resolve())
    except (OSError, ValueError, RuntimeError):
        raise PluginError("INVALID_OUTPUT_PATH", "输出路径必须位于任务 output") from None
    return target


def prepare_work(context: Context) -> tuple[Path, Path]:
    root = Path(context.workspace.output_dir).absolute()
    if root.is_symlink() or not root.is_dir():
        raise PluginError("INVALID_OUTPUT_PATH", "任务 output 不是安全目录")
    base = safe_output(root, ".office-work")
    base.mkdir(exist_ok=True, mode=0o700)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    work.chmod(0o700)
    return root, work


def csv_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def markdown_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    for old, new in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ("|", "&#124;"), ("`", "&#96;"), ("[", "&#91;"), ("]", "&#93;")):
        text = text.replace(old, new)
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)


def publish(source: Path, destination: Path):
    """Atomic no-clobber publication within the same output filesystem."""
    if source.is_symlink() or not source.is_file():
        raise ItemFailure("OUTPUT_INVALID", "转换输出不是普通文件")
    try:
        os.link(source, destination, follow_symlinks=False)
    except FileExistsError:
        raise ItemFailure("OUTPUT_EXISTS", "同名输出已存在，不覆盖已有文件") from None
    source.unlink()


def report_names(context: Context) -> list[str]:
    base = str(context.task.id)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", base):
        raise PluginError("TASK_ID_INVALID", "任务 ID 不能安全生成报告文件名")
    return [f"{base}.{ext}" for ext in ("json", "csv", "md")]


def write_reports(root: Path, work: Path, context: Context, report: dict[str, Any]) -> list[str]:
    fields = ["index", "source_name", "input_size", "input_sha256", "target_format", "status", "output", "error_code", "error_message"]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(fields)
    for item in report["items"]:
        writer.writerow([csv_safe(item.get(field)) for field in fields])
    summary = report["summary"]
    lines = ["# Office 转换报告", "", f"- 任务：{markdown_safe(context.task.id)}", f"- 状态：{summary['status']}",
             f"- complete：{str(summary['complete']).lower()}",
             f"- succeeded_count：{summary['succeeded_count']}；failed_count：{summary['failed_count']}；skipped_count：{summary['skipped_count']}",
             "", "|序号|源文件名|字节数|SHA256|目标格式|状态|输出|错误码|错误|", "|---:|---|---:|---|---|---|---|---|---|"]
    for item in report["items"]:
        lines.append("|" + "|".join(markdown_safe(item.get(key)) for key in fields) + "|")
    contents = [json.dumps(report, ensure_ascii=False, indent=2) + "\n", "\ufeff" + stream.getvalue(), "\n".join(lines) + "\n"]
    names = report_names(context)
    for name, content in zip(names, contents):
        target = safe_output(root, name)
        temporary = work / name
        temporary.write_text(content, encoding="utf-8")
        try:
            publish(temporary, target)
        except ItemFailure:
            raise PluginError("OUTPUT_EXISTS", "报告文件已存在，不覆盖") from None
    return names


def validate_params(command: str, params: dict[str, Any]) -> tuple[list[Path], int, int, bool]:
    if not isinstance(params, dict):
        raise PluginError("INVALID_PARAMETER", "参数必须为对象")
    if command == "office.inspect":
        if params:
            raise PluginError("INVALID_PARAMETER", "office.inspect 仅接受空对象")
        return [], 5, 5, True
    allowed = {"input", "inputs", "timeout_seconds", "batch_timeout_seconds", "continue_on_error"}
    if set(params) - allowed:
        raise PluginError("INVALID_PARAMETER", "不接受未声明参数或外部命令参数")
    if ("input" in params) == ("inputs" in params):
        raise PluginError("INVALID_INPUT_SELECTION", "input 与 inputs 必须严格二选一")
    raw = [params["input"]] if "input" in params else params["inputs"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_FILES or any(not isinstance(v, str) or not v.strip() or "\x00" in v for v in raw):
        raise PluginError("INVALID_INPUT", "需要 1–100 个非空文件路径")
    per_file = params.get("timeout_seconds", 60)
    batch = params.get("batch_timeout_seconds", 240)
    keep_going = params.get("continue_on_error", True)
    if type(per_file) is not int or not 5 <= per_file <= 120 or type(batch) is not int or not 10 <= batch <= 240 or type(keep_going) is not bool:
        raise PluginError("INVALID_PARAMETER", "超时范围或 continue_on_error 类型不合法")
    sources = [Path(v).expanduser().absolute() for v in raw]
    seen: set[Path] = set()
    seen_ids: set[tuple[int, int]] = set()
    total = 0
    for source in sources:
        try:
            if source.is_symlink():
                raise PluginError("SYMLINK_INPUT", "不接受符号链接输入")
            identity = source.resolve()
            if not source.is_file():
                raise PluginError("INPUT_NOT_FILE", "输入必须是存在的普通文件")
            info = source.stat()
            inode = (info.st_dev, info.st_ino)
            if identity in seen or (info.st_ino and inode in seen_ids):
                raise PluginError("DUPLICATE_INPUT", "输入文件不能重复")
            seen.add(identity)
            seen_ids.add(inode)
            total += info.st_size
            if info.st_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise PluginError("INPUT_TOO_LARGE", "输入超过单文件 50 MiB 或整批 100 MiB 限制")
        except PluginError:
            raise
        except (OSError, ValueError, RuntimeError):
            raise PluginError("INVALID_INPUT", "无法检查输入文件") from None
    return sources, per_file, batch, keep_going


def inspect_source(source: Path, item: dict[str, Any], deadline: float):
    suffix = source.suffix.lower()
    item["input_size"] = source.stat().st_size
    item["target_format"] = TARGETS.get(suffix, "").lstrip(".") or None
    if suffix not in TARGETS:
        raise ItemFailure("UNSUPPORTED_FORMAT", "仅支持旧 .xls、.doc、.ppt，不能原格式转原格式")
    if source.is_symlink():
        raise ItemFailure("SYMLINK_INPUT", "不接受符号链接输入")
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as handle:
        magic = handle.read(8)
        digest.update(magic)
        total += len(magic)
        # Reject HTML/OOXML/empty before launching LibreOffice.
        if magic != OLE_MAGIC:
            raise ItemFailure("INVALID_SOURCE", "输入不是旧 Office OLE CFB 文档（空、伪装或未知）")
        while chunk := handle.read(CHUNK):
            check_deadline(deadline)
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ItemFailure("INPUT_TOO_LARGE", "源文件超过单文件大小限制")
            digest.update(chunk)
    item.update(input_size=total, input_sha256=digest.hexdigest())


def xml_root(archive: zipfile.ZipFile, name: str, deadline: float) -> str:
    with archive.open(name) as stream:
        root = None
        for event, element in ET.iterparse(stream, events=("start", "end")):
            check_deadline(deadline)
            if root is None:
                root = element.tag
            if event == "end":
                element.clear()
        return root or ""


def verify_ooxml(path: Path, target: str, deadline: float):
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise ItemFailure("OUTPUT_MISSING", "转换器退出但没有生成非空普通文件")
    if path.stat().st_size > MAX_OUTPUT_BYTES:
        raise ItemFailure("OUTPUT_TOO_LARGE", "转换输出超过 100 MiB")
    part, tag, family, content_family = ROOTS[target]
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES or sum(i.compress_size for i in infos) > MAX_OUTPUT_BYTES:
                raise ItemFailure("OUTPUT_ARCHIVE_LIMIT", "输出 ZIP 超过压缩、解压大小或条目数限制")
            names: set[str] = set()
            actual_total = 0
            for info in infos:
                check_deadline(deadline)
                name = info.filename
                mode = stat.S_IFMT(info.external_attr >> 16)
                if not name or "\\" in name or "\x00" in name or name.startswith("/") or ":" in name or ".." in name.split("/") or mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ItemFailure("OUTPUT_ZIP_INVALID", "输出 ZIP 包含路径逃逸、链接或特殊条目")
                if name in names:
                    raise ItemFailure("OUTPUT_ZIP_INVALID", "输出 ZIP 含重复条目")
                names.add(name)
                lower = name.lower()
                if any(token in lower for token in ("vba", "macros", "scripts/", "activex/")):
                    raise ItemFailure("OUTPUT_MACRO_PRESENT", "输出仍含宏、脚本或活动内容")
                # Stream every byte: validates CRC without allocating the ZIP,
                # and rejects XML entities before ElementTree sees any XML.
                overlap = b""
                actual = 0
                with archive.open(info) as stream:
                    while chunk := stream.read(CHUNK):
                        check_deadline(deadline)
                        actual += len(chunk)
                        actual_total += len(chunk)
                        if actual_total > MAX_UNCOMPRESSED_BYTES or actual > info.file_size:
                            raise ItemFailure("OUTPUT_ARCHIVE_LIMIT", "输出 ZIP 实际解压大小超过限制")
                        if lower.endswith((".xml", ".rels")):
                            scan = (overlap + chunk).replace(b"\x00", b"").upper()
                            if b"<!DOCTYPE" in scan or b"<!ENTITY" in scan:
                                raise ItemFailure("OUTPUT_XML_INVALID", "输出 XML 含 DTD 或实体声明")
                            overlap = chunk[-64:]
                if actual != info.file_size:
                    raise ItemFailure("OUTPUT_ZIP_INVALID", "输出 ZIP 条目长度不一致")
            if "[Content_Types].xml" not in names or part not in names:
                raise ItemFailure("OUTPUT_OOXML_INVALID", "输出缺少必需 OOXML 主部件或 Content Types")
            root = xml_root(archive, part, deadline)
            expected = {f"{{http://schemas.openxmlformats.org/{family}/2006/main}}{tag}",
                        f"{{http://purl.oclc.org/ooxml/{family}/main}}{tag}"}
            if root not in expected:
                raise ItemFailure("OUTPUT_OOXML_INVALID", "OOXML 主部件根节点或命名空间不正确")
            ct_info = archive.getinfo("[Content_Types].xml")
            if ct_info.file_size > 2 * 1024 * 1024:
                raise ItemFailure("OUTPUT_ARCHIVE_LIMIT", "Content Types 超过大小限制")
            types = ET.fromstring(archive.read(ct_info))
            if types.tag != f"{{{CT_NS}}}Types":
                raise ItemFailure("OUTPUT_OOXML_INVALID", "Content Types 根节点不正确")
            wanted = f"application/vnd.openxmlformats-officedocument.{content_family}.main+xml"
            matches = []
            for node in types:
                content = node.get("ContentType", "")
                if any(word in content.lower() for word in ("macroenabled", "vba", "script", "activex")):
                    raise ItemFailure("OUTPUT_MACRO_PRESENT", "输出 Content Type 仍含宏或脚本")
                if node.tag == f"{{{CT_NS}}}Override" and node.get("PartName") == "/" + part:
                    matches.append(content)
            if matches != [wanted]:
                raise ItemFailure("OUTPUT_OOXML_INVALID", "OOXML 主部件 Content Type 不正确或重复")
    except ItemFailure:
        raise
    except (OSError, ValueError, RuntimeError, ET.ParseError, zipfile.BadZipFile, NotImplementedError, EOFError, LookupError, zlib.error):
        raise ItemFailure("OUTPUT_OOXML_INVALID", "输出 OOXML ZIP/XML/CRC 校验失败") from None


class Plugin:
    def init(self, context: Context):
        self.context = context

    def execute(self, command: str, params: dict[str, Any]) -> Result:
        started = time.monotonic()  # Includes validation/profile/probe time.
        if command not in {"office.convert", "office.inspect"}:
            raise PluginError("COMMAND_NOT_FOUND", "office 插件不支持此命令")
        sources, timeout, batch, keep_going = validate_params(command, params)
        engine = find_engine(self.context.config)
        if engine is None:
            if command == "office.convert":
                raise PluginError("DEPENDENCY_MISSING", "未找到 LibreOffice/soffice；请配置可信本机转换器")
            return self.inspection_result(False, None, None, ["DEPENDENCY_MISSING"])
        root, work = prepare_work(self.context)
        cleanup = True
        result = None
        try:
            if command == "office.inspect":
                profile = work / "profile"
                make_registry(profile)
                outcome = run_process([str(engine), f"-env:UserInstallation={profile.as_uri()}", "--version"], work, 5)
                cleanup = outcome.reaped
                text = outcome.stdout.decode("utf-8", errors="replace")
                # Only admit a bounded, engine-shaped version, never arbitrary
                # external diagnostics/paths in reports.
                match = re.search(r"LibreOffice[^\r\n]{0,230}", text)
                version = match.group(0) if match and outcome.returncode == 0 and not outcome.timed_out and not outcome.output_limited else None
                warning = "ENGINE_PROBE_TIMEOUT" if outcome.timed_out else "ENGINE_PROBE_FAILED"
                result = self.inspection_result(version is not None, str(engine), version, [] if version else [warning])
            else:
                result = self.convert(sources, engine, root, work, timeout, started + batch, keep_going)
            return result
        except UnreapedProcess as failure:
            cleanup = False
            result = failure.result
            return result
        finally:
            if cleanup:
                try:
                    shutil.rmtree(work, ignore_errors=False)
                except OSError:
                    self.context.logger.warning("本次 Office 临时目录清理未完成；未删除其它任务目录")
                    if result is not None:
                        result.warnings.append("CLEANUP_INCOMPLETE：本次私有临时目录未完全清理")
                # Never remove the shared base/another task's directory.
            else:
                self.context.logger.warning("转换器未确认回收；保留本次私有工作目录，未删除仍可能使用的 profile")

    @staticmethod
    def inspection_result(available, path, version, warnings):
        return Result("success", "LibreOffice 引擎可用" if available else "LibreOffice 引擎不可用", {
            "available": available, "engine_path": path, "engine_version": version,
            "support": {key.lstrip("."): value.lstrip(".") for key, value in TARGETS.items()},
            "warnings": warnings}, warnings=warnings)

    def convert(self, sources, engine, root, work, timeout, deadline, keep_going):
        names = report_names(self.context)
        for name in names:
            target = safe_output(root, name)
            if target.exists():
                raise PluginError("OUTPUT_EXISTS", "同名报告已存在，不覆盖")
        converted = safe_output(root, "converted")
        converted.mkdir(exist_ok=True)
        destinations = [safe_output(root, f"converted/{index:04d}-{source.stem}{TARGETS.get(source.suffix.lower(), '.unsupported')}")
                        for index, source in enumerate(sources, 1)]
        items, outputs = [], []
        stopped = False
        reaped = True
        output_bytes = 0
        for index, (source, destination) in enumerate(zip(sources, destinations), 1):
            item = {"index": index, "source_name": source.name, "input_size": source.stat().st_size,
                    "input_sha256": None, "target_format": TARGETS.get(source.suffix.lower(), "").lstrip(".") or None,
                    "status": "failed", "output": None, "error_code": None, "error_message": None}
            items.append(item)
            if stopped or not reaped or time.monotonic() >= deadline:
                reason = "STOP_ON_ERROR" if stopped else ("ENGINE_NOT_REAPED" if not reaped else "BATCH_TIMEOUT")
                item.update(status="skipped", error_code=reason, error_message="未处理：前序失败或批次 deadline 已到")
                continue
            try:
                inspect_source(source, item, deadline)
                target_format = TARGETS[source.suffix.lower()]
                destination = safe_output(root, destination.relative_to(root).as_posix())
                if destination.exists() or destination.is_symlink():
                    raise ItemFailure("OUTPUT_EXISTS", "同名输出已存在，不覆盖")
                private = work / f"file-{index:04d}"
                private.mkdir(mode=0o700)
                profile = private / "profile"
                make_registry(profile)
                temporary_output = private / "out"
                temporary_output.mkdir()
                generated = temporary_output / (source.stem + target_format)
                check_deadline(deadline)
                argv = [str(engine), f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore", "--nodefault",
                        "--convert-to", FILTERS[target_format], "--outdir", str(temporary_output), str(source)]
                outcome = run_process(argv, private, min(timeout, max(0, deadline - time.monotonic())), generated)
                reaped = outcome.reaped
                if outcome.timed_out:
                    raise ItemFailure("TIMEOUT", "转换达到单文件或批次时限，已终止本次进程树")
                if not reaped:
                    raise ItemFailure("ENGINE_NOT_REAPED", "无法确认本次进程树结束；停止后续转换并保留隔离目录")
                if outcome.output_limited:
                    raise ItemFailure("ENGINE_OUTPUT_LIMIT", "引擎日志或输出超过限额")
                if outcome.returncode != 0:
                    raise ItemFailure("CONVERSION_FAILED", "LibreOffice 启动或转换失败")
                verify_ooxml(generated, target_format, deadline)
                size = generated.stat().st_size
                if output_bytes + size > MAX_BATCH_OUTPUT_BYTES:
                    raise ItemFailure("OUTPUT_TOO_LARGE", "整批转换产物超过 400 MiB 限制")
                check_deadline(deadline)
                destination = safe_output(root, destination.relative_to(root).as_posix())
                publish(generated, destination)
                output_bytes += size
                item.update(status="succeeded", output=destination.relative_to(root).as_posix())
                outputs.append(item["output"])
            except ItemFailure as error:
                item.update(error_code=error.code, error_message=error.message)
                stopped = not keep_going
            except (OSError, ValueError):
                item.update(error_code="CONVERSION_FAILED", error_message="输入读取或转换输出处理失败")
                stopped = not keep_going
            finally:
                # Free each completed item's profile/temp output immediately;
                # don't accumulate failed output packages across a batch.
                private_path = work / f"file-{index:04d}"
                if reaped and private_path.exists():
                    try:
                        shutil.rmtree(private_path)
                    except OSError:
                        # Final cleanup gets one further attempt; no unrelated
                        # directory is removed to manufacture successful cleanup.
                        self.context.logger.warning("本文件 Office 临时目录清理需在任务收尾重试")
        succeeded = sum(i["status"] == "succeeded" for i in items)
        failed = sum(i["status"] == "failed" for i in items)
        skipped = sum(i["status"] == "skipped" for i in items)
        complete = not failed and not skipped
        summary = {"status": "succeeded" if complete else ("partial" if succeeded else "failed"),
                   "complete": complete, "total_count": len(items), "succeeded_count": succeeded,
                   "failed_count": failed, "skipped_count": skipped}
        reports = write_reports(root, work, self.context, {"task_id": self.context.task.id, "summary": summary, "items": items})
        warnings = [f"转换不完整：失败 {failed}，跳过 {skipped}"] if not complete else []
        if any(source.suffix.lower() == ".doc" for source in sources):
            warnings.append("DOC→DOCX 已检查 OOXML 结构，不保证表格/排版无损；本机 LibreOfficeDev alpha 验证发现旧 DOC 链路可能丢表，请逐份核查")
        data = {**summary, "summary": dict(summary)}
        result = Result("success" if succeeded else "failed", f"Office 转换：成功 {succeeded}，失败 {failed}，跳过 {skipped}", data, outputs + reports, warnings)
        if not reaped:
            raise UnreapedProcess(result)
        return result

    def destroy(self):
        pass


class UnreapedProcess(Exception):
    def __init__(self, result):
        self.result = result
