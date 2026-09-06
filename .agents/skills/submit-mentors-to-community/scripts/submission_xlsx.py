"""Bounded, standard-library reader for public submission workbooks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CELL_REF_PATTERN = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class SheetData:
    name: str
    rows: list[list[str]]
    formulas: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkbookData:
    active_sheet_name: str
    sheets: tuple[SheetData, ...]

    def sheet(self, name: str) -> SheetData | None:
        return next((sheet for sheet in self.sheets if sheet.name == name), None)


def _tag(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _column_index(name: str) -> int:
    value = 0
    for char in name:
        value = value * 26 + ord(char) - 64
    return value


def _parse_xml(data: bytes) -> ET.Element:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XLSX 不支持 XML 实体或 DTD")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("XLSX XML 无法解析") from exc


def _read_shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _parse_xml(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(text.text or "" for text in item.iter(_tag(MAIN_NS, "t")))
        for item in root.findall(_tag(MAIN_NS, "si"))
    ]


def _resolve_workbook_target(target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return str(PurePosixPath("xl") / PurePosixPath(target))


def _read_sheet(
    archive: ZipFile,
    *,
    name: str,
    target: str,
    shared_strings: list[str],
) -> SheetData:
    root = _parse_xml(archive.read(target))
    row_values: dict[int, dict[int, str]] = {}
    formulas: list[str] = []
    max_column = 0
    max_row = 0
    for row in root.findall(f".//{_tag(MAIN_NS, 'row')}"):
        row_number = int(row.attrib.get("r", "0") or "0")
        if not 1 <= row_number <= 50001 or row_number in row_values:
            raise ValueError("工作表行号无效、重复或超过 50000 条导师限制")
        max_row = max(max_row, row_number)
        current: dict[int, str] = {}
        for cell in row.findall(_tag(MAIN_NS, "c")):
            reference = cell.attrib.get("r", "")
            match = CELL_REF_PATTERN.fullmatch(reference)
            if not match or int(match.group(2)) != row_number:
                raise ValueError("工作表单元格坐标无效")
            column_index = _column_index(match.group(1))
            if column_index > 10 or column_index in current:
                raise ValueError("工作表包含额外列或重复单元格")
            max_column = max(max_column, column_index)
            formula = cell.find(_tag(MAIN_NS, "f"))
            if formula is not None:
                formulas.append(reference)
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                value = "".join(
                    text.text or "" for text in cell.findall(f".//{_tag(MAIN_NS, 't')}")
                )
            else:
                value_element = cell.find(_tag(MAIN_NS, "v"))
                raw_value = value_element.text if value_element is not None else ""
                if cell_type == "s" and raw_value:
                    try:
                        string_index = int(raw_value)
                        if string_index < 0:
                            raise ValueError("negative shared string index")
                        value = shared_strings[string_index]
                    except (IndexError, ValueError):
                        raise ValueError("无效的共享字符串索引") from None
                elif cell_type == "b":
                    value = "TRUE" if raw_value == "1" else "FALSE"
                else:
                    value = raw_value or ""
            current[column_index] = value
        row_values[row_number] = current
    rows = [
        [
            row_values.get(row_number, {}).get(column, "")
            for column in range(1, max_column + 1)
        ]
        for row_number in range(1, max_row + 1)
    ]
    return SheetData(name=name, rows=rows, formulas=tuple(formulas))


def read_workbook(path: Path) -> WorkbookData:
    try:
        archive = ZipFile(path)
    except (BadZipFile, OSError) as error:
        raise ValueError("XLSX 文件无法作为 ZIP/OOXML 工作簿读取") from error
    with archive:
        entries = archive.infolist()
        names = archive.namelist()
        if len(entries) > 32 or len(set(names)) != len(names):
            raise ValueError("XLSX 包含过多或重复的 ZIP 部件")
        if sum(entry.file_size for entry in entries) > 32 * 1024 * 1024:
            raise ValueError("XLSX 解压后超过 32 MiB 限制")
        allowed = {
            "[Content_Types].xml",
            "_rels/.rels",
            "docProps/core.xml",
            "docProps/app.xml",
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
            "xl/styles.xml",
            "xl/sharedStrings.xml",
            "xl/theme/theme1.xml",
        }
        worksheet_parts = {
            name
            for name in names
            if re.fullmatch(r"xl/worksheets/sheet[0-9]+\.xml", name)
        }
        if len(worksheet_parts) != 1 or set(names) - allowed - worksheet_parts:
            raise ValueError("XLSX 只能包含一个工作表及标准部件；请重新导出公开字段")
        required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
        missing = required.difference(archive.namelist())
        if missing:
            raise ValueError(f"XLSX 缺少必要部件：{', '.join(sorted(missing))}")
        workbook_root = _parse_xml(archive.read("xl/workbook.xml"))
        rels_root = _parse_xml(archive.read("xl/_rels/workbook.xml.rels"))
        relationships = {
            item.attrib.get("Id", ""): item.attrib.get("Target", "")
            for item in rels_root.findall(_tag(PACKAGE_REL_NS, "Relationship"))
        }
        sheet_elements = workbook_root.findall(f".//{_tag(MAIN_NS, 'sheet')}")
        if (
            len(sheet_elements) != 1
            or sheet_elements[0].attrib.get("state", "visible") != "visible"
        ):
            raise ValueError("XLSX 必须且只能包含一个可见的 community-share 工作表")
        active_view = workbook_root.find(f".//{_tag(MAIN_NS, 'workbookView')}")
        active_index = (
            int(active_view.attrib.get("activeTab", "0"))
            if active_view is not None
            else 0
        )
        if active_index < 0 or active_index >= len(sheet_elements):
            raise ValueError("XLSX 活动工作表索引无效")
        shared_strings = _read_shared_strings(archive)
        sheets: list[SheetData] = []
        for sheet_element in sheet_elements:
            name = sheet_element.attrib.get("name", "")
            rel_id = sheet_element.attrib.get(_tag(OFFICE_REL_NS, "id"), "")
            target = relationships.get(rel_id)
            if not target:
                raise ValueError(f"工作表 {name or rel_id} 缺少关系目标")
            resolved_target = _resolve_workbook_target(target)
            if resolved_target not in worksheet_parts:
                raise ValueError(f"工作表 {name} 的 XML 部件不存在")
            sheets.append(
                _read_sheet(
                    archive,
                    name=name,
                    target=resolved_target,
                    shared_strings=shared_strings,
                )
            )
        return WorkbookData(
            active_sheet_name=sheets[active_index].name,
            sheets=tuple(sheets),
        )
