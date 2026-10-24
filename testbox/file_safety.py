"""Shared bounds for the lightweight Office readers and spreadsheet exports."""
from __future__ import annotations

import zipfile
from typing import Any
from xml.etree import ElementTree

from testbox.sdk import PluginError

MAX_EXCEL_ENTRIES = 4096
MAX_EXCEL_BYTES = 100 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024


def check_excel_archive(archive: zipfile.ZipFile) -> None:
    entries = archive.infolist()
    if (len(entries) > MAX_EXCEL_ENTRIES
            or sum(item.file_size for item in entries) > MAX_EXCEL_BYTES):
        raise PluginError("INPUT_LIMIT_EXCEEDED", "Excel 解压大小或条目数超过限制")
    if len({item.filename for item in entries}) != len(entries):
        raise PluginError("INPUT_INVALID", "Excel 包包含重复条目")


def read_excel_xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise PluginError("INPUT_LIMIT_EXCEEDED", "Excel XML 大小超过 16 MiB 限制")
    with archive.open(info) as stream:
        content = stream.read(MAX_XML_BYTES + 1)
    if len(content) > MAX_XML_BYTES:
        raise PluginError("INPUT_LIMIT_EXCEEDED", "Excel XML 实际大小超过限制")
    # Do not allow entity expansion, including UTF-16/32 encoded declarations.
    declarations = content.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise PluginError("INPUT_INVALID", "Excel XML 不允许 DTD 或实体声明")
    return ElementTree.fromstring(content)


def spreadsheet_cell(value: Any) -> Any:
    """Neutralize string formulas, including whitespace and control prefixes.

    Preserve real numeric values; a negative number is data, not a string
    formula. JSON/SQL and typed XLSX exports have their own representation.
    """
    if isinstance(value, str) and (value.startswith(("\t", "\r", "\n"))
                                  or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value
