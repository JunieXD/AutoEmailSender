from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPE_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CORE_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
EXTENDED_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
XML_NS = "http://www.w3.org/XML/1998/namespace"

ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", OFFICE_REL_NS)
ET.register_namespace("cp", CORE_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("dcterms", DCTERMS_NS)
ET.register_namespace("xsi", XSI_NS)
ET.register_namespace("vt", VT_NS)

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


def _xml(root: ET.Element) -> bytes:
    body = ET.tostring(root, encoding="utf-8", short_empty_elements=True)
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + body


def column_name(index: int) -> str:
    if index < 1:
        raise ValueError("column index must be positive")
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _column_index(name: str) -> int:
    value = 0
    for char in name:
        value = value * 26 + ord(char) - 64
    return value


def _add_font(
    fonts: ET.Element,
    *,
    bold: bool = False,
    color: str = "1F2937",
    underline: bool = False,
) -> None:
    font = ET.SubElement(fonts, _tag(MAIN_NS, "font"))
    if bold:
        ET.SubElement(font, _tag(MAIN_NS, "b"))
    if underline:
        ET.SubElement(font, _tag(MAIN_NS, "u"))
    ET.SubElement(font, _tag(MAIN_NS, "sz"), {"val": "11"})
    ET.SubElement(font, _tag(MAIN_NS, "color"), {"rgb": f"FF{color}"})
    ET.SubElement(font, _tag(MAIN_NS, "name"), {"val": "Aptos"})
    ET.SubElement(font, _tag(MAIN_NS, "family"), {"val": "2"})


def _styles_xml() -> bytes:
    root = ET.Element(_tag(MAIN_NS, "styleSheet"))
    fonts = ET.SubElement(root, _tag(MAIN_NS, "fonts"), {"count": "3"})
    _add_font(fonts)
    _add_font(fonts, bold=True, color="FFFFFF")
    _add_font(fonts, color="2563EB", underline=True)

    fills = ET.SubElement(root, _tag(MAIN_NS, "fills"), {"count": "5"})
    fill = ET.SubElement(fills, _tag(MAIN_NS, "fill"))
    ET.SubElement(fill, _tag(MAIN_NS, "patternFill"), {"patternType": "none"})
    fill = ET.SubElement(fills, _tag(MAIN_NS, "fill"))
    ET.SubElement(fill, _tag(MAIN_NS, "patternFill"), {"patternType": "gray125"})
    for color in ("44403C", "B45309", "0369A1"):
        fill = ET.SubElement(fills, _tag(MAIN_NS, "fill"))
        pattern = ET.SubElement(
            fill,
            _tag(MAIN_NS, "patternFill"),
            {"patternType": "solid"},
        )
        ET.SubElement(pattern, _tag(MAIN_NS, "fgColor"), {"rgb": f"FF{color}"})
        ET.SubElement(pattern, _tag(MAIN_NS, "bgColor"), {"indexed": "64"})

    borders = ET.SubElement(root, _tag(MAIN_NS, "borders"), {"count": "2"})
    border = ET.SubElement(borders, _tag(MAIN_NS, "border"))
    for edge in ("left", "right", "top", "bottom", "diagonal"):
        ET.SubElement(border, _tag(MAIN_NS, edge))
    border = ET.SubElement(borders, _tag(MAIN_NS, "border"))
    for edge in ("left", "right", "top"):
        ET.SubElement(border, _tag(MAIN_NS, edge))
    bottom = ET.SubElement(border, _tag(MAIN_NS, "bottom"), {"style": "thin"})
    ET.SubElement(bottom, _tag(MAIN_NS, "color"), {"rgb": "FFD6D3D1"})
    ET.SubElement(border, _tag(MAIN_NS, "diagonal"))

    cell_style_xfs = ET.SubElement(
        root,
        _tag(MAIN_NS, "cellStyleXfs"),
        {"count": "1"},
    )
    ET.SubElement(
        cell_style_xfs,
        _tag(MAIN_NS, "xf"),
        {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0"},
    )

    cell_xfs = ET.SubElement(root, _tag(MAIN_NS, "cellXfs"), {"count": "6"})
    ET.SubElement(
        cell_xfs,
        _tag(MAIN_NS, "xf"),
        {
            "numFmtId": "0",
            "fontId": "0",
            "fillId": "0",
            "borderId": "0",
            "xfId": "0",
        },
    )
    for fill_id in (2, 3, 4):
        xf = ET.SubElement(
            cell_xfs,
            _tag(MAIN_NS, "xf"),
            {
                "numFmtId": "0",
                "fontId": "1",
                "fillId": str(fill_id),
                "borderId": "1",
                "xfId": "0",
                "applyFont": "1",
                "applyFill": "1",
                "applyBorder": "1",
                "applyAlignment": "1",
            },
        )
        ET.SubElement(
            xf,
            _tag(MAIN_NS, "alignment"),
            {"horizontal": "left", "vertical": "center", "wrapText": "1"},
        )
    xf = ET.SubElement(
        cell_xfs,
        _tag(MAIN_NS, "xf"),
        {
            "numFmtId": "49",
            "fontId": "0",
            "fillId": "0",
            "borderId": "0",
            "xfId": "0",
            "applyNumberFormat": "1",
            "applyAlignment": "1",
        },
    )
    ET.SubElement(
        xf,
        _tag(MAIN_NS, "alignment"),
        {"vertical": "top", "wrapText": "1"},
    )
    xf = ET.SubElement(
        cell_xfs,
        _tag(MAIN_NS, "xf"),
        {
            "numFmtId": "49",
            "fontId": "2",
            "fillId": "0",
            "borderId": "0",
            "xfId": "0",
            "applyNumberFormat": "1",
            "applyFont": "1",
            "applyAlignment": "1",
        },
    )
    ET.SubElement(
        xf,
        _tag(MAIN_NS, "alignment"),
        {"vertical": "top", "wrapText": "1"},
    )

    cell_styles = ET.SubElement(root, _tag(MAIN_NS, "cellStyles"), {"count": "1"})
    ET.SubElement(
        cell_styles,
        _tag(MAIN_NS, "cellStyle"),
        {"name": "Normal", "xfId": "0", "builtinId": "0"},
    )
    ET.SubElement(root, _tag(MAIN_NS, "dxfs"), {"count": "0"})
    ET.SubElement(
        root,
        _tag(MAIN_NS, "tableStyles"),
        {
            "count": "0",
            "defaultTableStyle": "TableStyleMedium2",
            "defaultPivotStyle": "PivotStyleLight16",
        },
    )
    return _xml(root)


def _append_text_cell(
    row: ET.Element,
    *,
    row_index: int,
    column_index: int,
    value: str,
    style_id: int,
) -> None:
    if value == "":
        return
    cell = ET.SubElement(
        row,
        _tag(MAIN_NS, "c"),
        {
            "r": f"{column_name(column_index)}{row_index}",
            "s": str(style_id),
            "t": "inlineStr",
        },
    )
    inline = ET.SubElement(cell, _tag(MAIN_NS, "is"))
    text = ET.SubElement(inline, _tag(MAIN_NS, "t"))
    if value[:1].isspace() or value[-1:].isspace():
        text.set(_tag(XML_NS, "space"), "preserve")
    text.text = value


def _worksheet_xml(
    *,
    columns: tuple[str, ...],
    rows: list[dict[str, str]],
    widths: tuple[float, ...],
    header_style_id: int,
    url_fields: frozenset[str],
) -> bytes:
    root = ET.Element(_tag(MAIN_NS, "worksheet"))
    last_column = column_name(len(columns))
    last_row = max(1, len(rows) + 1)
    ET.SubElement(
        root,
        _tag(MAIN_NS, "dimension"),
        {"ref": f"A1:{last_column}{last_row}"},
    )
    sheet_views = ET.SubElement(root, _tag(MAIN_NS, "sheetViews"))
    sheet_view = ET.SubElement(
        sheet_views,
        _tag(MAIN_NS, "sheetView"),
        {"workbookViewId": "0", "showGridLines": "0"},
    )
    ET.SubElement(
        sheet_view,
        _tag(MAIN_NS, "pane"),
        {
            "ySplit": "1",
            "topLeftCell": "A2",
            "activePane": "bottomLeft",
            "state": "frozen",
        },
    )
    ET.SubElement(sheet_view, _tag(MAIN_NS, "selection"), {"pane": "bottomLeft"})
    ET.SubElement(root, _tag(MAIN_NS, "sheetFormatPr"), {"defaultRowHeight": "15"})
    cols = ET.SubElement(root, _tag(MAIN_NS, "cols"))
    for index, width in enumerate(widths, start=1):
        ET.SubElement(
            cols,
            _tag(MAIN_NS, "col"),
            {
                "min": str(index),
                "max": str(index),
                "width": str(width),
                "customWidth": "1",
            },
        )
    sheet_data = ET.SubElement(root, _tag(MAIN_NS, "sheetData"))
    header_row = ET.SubElement(
        sheet_data,
        _tag(MAIN_NS, "row"),
        {"r": "1", "ht": "24", "customHeight": "1"},
    )
    for column_index, column in enumerate(columns, start=1):
        _append_text_cell(
            header_row,
            row_index=1,
            column_index=column_index,
            value=column,
            style_id=header_style_id,
        )
    for row_index, item in enumerate(rows, start=2):
        xml_row = ET.SubElement(
            sheet_data,
            _tag(MAIN_NS, "row"),
            {"r": str(row_index)},
        )
        for column_index, column in enumerate(columns, start=1):
            _append_text_cell(
                xml_row,
                row_index=row_index,
                column_index=column_index,
                value=item.get(column, ""),
                style_id=5 if column in url_fields and item.get(column) else 4,
            )
    ET.SubElement(
        root,
        _tag(MAIN_NS, "autoFilter"),
        {"ref": f"A1:{last_column}{last_row}"},
    )
    ET.SubElement(
        root,
        _tag(MAIN_NS, "pageMargins"),
        {
            "left": "0.4",
            "right": "0.4",
            "top": "0.6",
            "bottom": "0.6",
            "header": "0.2",
            "footer": "0.2",
        },
    )
    return _xml(root)


def _content_types_xml(sheet_count: int) -> bytes:
    # System.IO.Packaging (used by Microsoft Open XML and artifact-tool) is
    # stricter than openpyxl here: it expects the package content-types
    # vocabulary to use the default namespace, not an arbitrary prefix.
    root = ET.Element("Types", {"xmlns": CONTENT_TYPE_NS})
    ET.SubElement(
        root,
        "Default",
        {"Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml"},
    )
    ET.SubElement(
        root,
        "Default",
        {"Extension": "xml", "ContentType": "application/xml"},
    )
    overrides = [
        ("/xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
        ("/xl/styles.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"),
        ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
        ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml"),
    ]
    overrides.extend(
        (
            f"/xl/worksheets/sheet{index}.xml",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
        )
        for index in range(1, sheet_count + 1)
    )
    for part_name, content_type in overrides:
        ET.SubElement(
            root,
            "Override",
            {"PartName": part_name, "ContentType": content_type},
        )
    return _xml(root)


def _root_relationships_xml() -> bytes:
    root = ET.Element("Relationships", {"xmlns": PACKAGE_REL_NS})
    relationships = [
        ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument", "xl/workbook.xml"),
        ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties", "docProps/core.xml"),
        ("rId3", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties", "docProps/app.xml"),
    ]
    for rel_id, rel_type, target in relationships:
        ET.SubElement(
            root,
            "Relationship",
            {"Id": rel_id, "Type": rel_type, "Target": target},
        )
    return _xml(root)


def _workbook_xml(sheet_names: tuple[str, ...]) -> bytes:
    root = ET.Element(_tag(MAIN_NS, "workbook"))
    ET.SubElement(root, _tag(MAIN_NS, "fileVersion"), {"appName": "xl"})
    book_views = ET.SubElement(root, _tag(MAIN_NS, "bookViews"))
    ET.SubElement(book_views, _tag(MAIN_NS, "workbookView"), {"activeTab": "0"})
    sheets = ET.SubElement(root, _tag(MAIN_NS, "sheets"))
    for index, name in enumerate(sheet_names, start=1):
        ET.SubElement(
            sheets,
            _tag(MAIN_NS, "sheet"),
            {
                "name": name,
                "sheetId": str(index),
                _tag(OFFICE_REL_NS, "id"): f"rId{index}",
            },
        )
    ET.SubElement(
        root,
        _tag(MAIN_NS, "calcPr"),
        {"calcId": "0", "fullCalcOnLoad": "0"},
    )
    return _xml(root)


def _workbook_relationships_xml(sheet_count: int) -> bytes:
    root = ET.Element("Relationships", {"xmlns": PACKAGE_REL_NS})
    for index in range(1, sheet_count + 1):
        ET.SubElement(
            root,
            "Relationship",
            {
                "Id": f"rId{index}",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
                "Target": f"worksheets/sheet{index}.xml",
            },
        )
    ET.SubElement(
        root,
        "Relationship",
        {
            "Id": f"rId{sheet_count + 1}",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles",
            "Target": "styles.xml",
        },
    )
    return _xml(root)


def _core_properties_xml() -> bytes:
    root = ET.Element(_tag(CORE_NS, "coreProperties"))
    creator = ET.SubElement(root, _tag(DC_NS, "creator"))
    creator.text = "Auto Email Sender crawl-mentors-to-xlsx"
    modified_by = ET.SubElement(root, _tag(CORE_NS, "lastModifiedBy"))
    modified_by.text = "Auto Email Sender crawl-mentors-to-xlsx"
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    created = ET.SubElement(
        root,
        _tag(DCTERMS_NS, "created"),
        {_tag(XSI_NS, "type"): "dcterms:W3CDTF"},
    )
    created.text = timestamp
    modified = ET.SubElement(
        root,
        _tag(DCTERMS_NS, "modified"),
        {_tag(XSI_NS, "type"): "dcterms:W3CDTF"},
    )
    modified.text = timestamp
    return _xml(root)


def _app_properties_xml(sheet_names: tuple[str, ...]) -> bytes:
    root = ET.Element(_tag(EXTENDED_NS, "Properties"))
    application = ET.SubElement(root, _tag(EXTENDED_NS, "Application"))
    application.text = "Auto Email Sender"
    heading_pairs = ET.SubElement(root, _tag(EXTENDED_NS, "HeadingPairs"))
    vector = ET.SubElement(
        heading_pairs,
        _tag(VT_NS, "vector"),
        {"size": "2", "baseType": "variant"},
    )
    variant = ET.SubElement(vector, _tag(VT_NS, "variant"))
    value = ET.SubElement(variant, _tag(VT_NS, "lpstr"))
    value.text = "Worksheets"
    variant = ET.SubElement(vector, _tag(VT_NS, "variant"))
    value = ET.SubElement(variant, _tag(VT_NS, "i4"))
    value.text = str(len(sheet_names))
    titles = ET.SubElement(root, _tag(EXTENDED_NS, "TitlesOfParts"))
    vector = ET.SubElement(
        titles,
        _tag(VT_NS, "vector"),
        {"size": str(len(sheet_names)), "baseType": "lpstr"},
    )
    for name in sheet_names:
        value = ET.SubElement(vector, _tag(VT_NS, "lpstr"))
        value.text = name
    return _xml(root)


def write_professor_workbook(
    output_path: Path,
    *,
    columns: tuple[str, ...],
    records: list[dict[str, str]],
    review: list[dict[str, str]],
    sources: list[dict[str, str]],
) -> None:
    sheet_specs = [
        (
            "Professors",
            columns,
            records,
            (18, 30, 16, 24, 24, 24, 34, 54, 48, 48, 24, 40)[: len(columns)],
            1,
            frozenset({"profile_url", "source_url"}),
        ),
        (
            "Needs Review",
            ("name", "email", "profile_url", "source_url", "reason", "details"),
            review,
            (18, 30, 48, 48, 28, 60),
            2,
            frozenset({"profile_url", "source_url"}),
        ),
        (
            "Sources",
            ("url", "role", "status", "note"),
            sources,
            (58, 16, 16, 60),
            3,
            frozenset({"url"}),
        ),
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = tuple(spec[0] for spec in sheet_specs)
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml(len(sheet_specs)))
        archive.writestr("_rels/.rels", _root_relationships_xml())
        archive.writestr("docProps/core.xml", _core_properties_xml())
        archive.writestr("docProps/app.xml", _app_properties_xml(sheet_names))
        archive.writestr("xl/workbook.xml", _workbook_xml(sheet_names))
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            _workbook_relationships_xml(len(sheet_specs)),
        )
        archive.writestr("xl/styles.xml", _styles_xml())
        for index, (_, sheet_columns, rows, widths, style_id, url_fields) in enumerate(
            sheet_specs,
            start=1,
        ):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _worksheet_xml(
                    columns=sheet_columns,
                    rows=rows,
                    widths=widths,
                    header_style_id=style_id,
                    url_fields=url_fields,
                ),
            )


def _read_shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
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
    root = ET.fromstring(archive.read(target))
    row_values: dict[int, dict[int, str]] = {}
    formulas: list[str] = []
    max_column = 0
    max_row = 0
    for row in root.findall(f".//{_tag(MAIN_NS, 'row')}"):
        row_number = int(row.attrib.get("r", "0") or "0")
        if row_number <= 0:
            continue
        max_row = max(max_row, row_number)
        current: dict[int, str] = {}
        for cell in row.findall(_tag(MAIN_NS, "c")):
            reference = cell.attrib.get("r", "")
            match = CELL_REF_PATTERN.fullmatch(reference)
            if not match:
                continue
            column_index = _column_index(match.group(1))
            max_column = max(max_column, column_index)
            formula = cell.find(_tag(MAIN_NS, "f"))
            if formula is not None:
                formulas.append(reference)
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                value = "".join(
                    text.text or ""
                    for text in cell.findall(f".//{_tag(MAIN_NS, 't')}")
                )
            else:
                value_element = cell.find(_tag(MAIN_NS, "v"))
                raw_value = value_element.text if value_element is not None else ""
                if cell_type == "s" and raw_value:
                    try:
                        value = shared_strings[int(raw_value)]
                    except (IndexError, ValueError):
                        value = raw_value
                elif cell_type == "b":
                    value = "TRUE" if raw_value == "1" else "FALSE"
                else:
                    value = raw_value or ""
            current[column_index] = value
        row_values[row_number] = current
    rows = [
        [row_values.get(row_number, {}).get(column, "") for column in range(1, max_column + 1)]
        for row_number in range(1, max_row + 1)
    ]
    return SheetData(name=name, rows=rows, formulas=tuple(formulas))


def read_workbook(path: Path) -> WorkbookData:
    try:
        archive = ZipFile(path)
    except (BadZipFile, OSError) as error:
        raise ValueError("XLSX 文件无法作为 ZIP/OOXML 工作簿读取") from error
    with archive:
        required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
        missing = required.difference(archive.namelist())
        if missing:
            raise ValueError(f"XLSX 缺少必要部件：{', '.join(sorted(missing))}")
        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relationships = {
            item.attrib.get("Id", ""): item.attrib.get("Target", "")
            for item in rels_root.findall(_tag(PACKAGE_REL_NS, "Relationship"))
        }
        sheet_elements = workbook_root.findall(f".//{_tag(MAIN_NS, 'sheet')}")
        if not sheet_elements:
            raise ValueError("XLSX 不包含工作表")
        active_view = workbook_root.find(f".//{_tag(MAIN_NS, 'workbookView')}")
        active_index = int(active_view.attrib.get("activeTab", "0")) if active_view is not None else 0
        if active_index < 0 or active_index >= len(sheet_elements):
            active_index = 0
        shared_strings = _read_shared_strings(archive)
        sheets: list[SheetData] = []
        for sheet_element in sheet_elements:
            name = sheet_element.attrib.get("name", "")
            rel_id = sheet_element.attrib.get(_tag(OFFICE_REL_NS, "id"), "")
            target = relationships.get(rel_id)
            if not target:
                raise ValueError(f"工作表 {name or rel_id} 缺少关系目标")
            resolved_target = _resolve_workbook_target(target)
            if resolved_target not in archive.namelist():
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
