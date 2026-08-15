"""Read an XLSX skill source without adding a spreadsheet runtime dependency."""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Iterable

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "p": "http://schemas.openxmlformats.org/package/2006/relationships"}


@dataclass(frozen=True)
class SpreadsheetTable:
    name: str
    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def _column(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    result = 0
    for char in letters.group(0) if letters else "A":
        result = result * 26 + ord(char) - 64
    return result - 1


def _strings(book: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(book.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.itertext()) for node in root.findall("m:si", NS)]


def _sheet_rows(book: zipfile.ZipFile, target: str, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(book.read(target))
    result: list[list[str]] = []
    for row in root.findall(".//m:sheetData/m:row", NS):
        values: dict[int, str] = {}
        for cell in row.findall("m:c", NS):
            index = _column(cell.attrib.get("r", "A1"))
            kind = cell.attrib.get("t")
            value = cell.find("m:v", NS)
            inline = cell.find("m:is", NS)
            text = "" if value is None else (value.text or "")
            if kind == "s" and text.isdigit():
                text = shared[int(text)]
            elif kind == "inlineStr" and inline is not None:
                text = "".join(inline.itertext())
            values[index] = text.strip()
        if values:
            result.append([values.get(index, "") for index in range(max(values) + 1)])
    return result


def read_xlsx(content: bytes) -> tuple[SpreadsheetTable, ...]:
    with zipfile.ZipFile(io.BytesIO(content)) as book:
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("p:Relationship", NS)}
        shared = _strings(book)
        tables = []
        for sheet in workbook.findall("m:sheets/m:sheet", NS):
            relation = sheet.attrib[f"{{{NS['r']}}}id"]
            target = targets[relation].lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            rows = _sheet_rows(book, target, shared)
            if not rows:
                continue
            headers = tuple(value.strip() for value in rows[0])
            mapped = tuple({header: row[index].strip() if index < len(row) else ""
                            for index, header in enumerate(headers) if header}
                           for row in rows[1:] if any(value.strip() for value in row))
            tables.append(SpreadsheetTable(sheet.attrib["name"], headers, mapped))
        return tuple(tables)


def read_csv(content: bytes) -> tuple[SpreadsheetTable, ...]:
    text = content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    headers = tuple(rows[0]) if rows else ()
    return (SpreadsheetTable("csv", headers, tuple(
        {str(key): str(value or "").strip() for key, value in row.items()} for row in rows
    )),)


def flatten(tables: Iterable[SpreadsheetTable]) -> tuple[dict[str, str], ...]:
    return tuple({"_sheet": table.name, **row} for table in tables for row in table.rows)


def skill_tables(tables: Iterable[SpreadsheetTable]) -> tuple[SpreadsheetTable, ...]:
    """Keep worksheets that contain both a user title and an instruction.

    Workbooks commonly include source indexes, summaries, and review checklists
    alongside the actual cards. Those support sheets must not be interpreted as
    incomplete skill rows merely because they also contain an ID column.
    """
    title_headers = {"title", "title_user", "название", "название навыка", "навык"}
    instruction_headers = {
        "standard_variant", "instruction", "how", "инструкция", "обычная версия", "алгоритм",
    }
    selected = []
    for table in tables:
        headers = {re.sub(r"\s+", " ", value.strip().lower()) for value in table.headers}
        if headers & title_headers and headers & instruction_headers:
            selected.append(table)
    return tuple(selected)
