"""Minimal .xlsx reader that uses only the standard library.

Scope: read every sheet of a workbook as rows of text. Formulas are not
evaluated (the cached value is read), styles and dates are ignored, so a date
cell comes back as its serial number. That is enough for the Scopus
"discontinued sources" workbook, where only titles, ISSNs and short text fields
matter. It avoids adding openpyxl as a dependency.
"""

from __future__ import annotations

import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_REL_ID = f"{{{_NS['r']}}}id"


class XlsxError(ValueError):
    """The bytes are not a readable .xlsx workbook."""


def _column_index(ref: str) -> int:
    """Zero-based column number from a cell reference such as 'AB12'."""
    letters = re.match(r"[A-Za-z]+", ref)
    if not letters:
        raise XlsxError(f"bad cell reference {ref!r}")
    n = 0
    for ch in letters.group(0).upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _text_of(node: ET.Element | None) -> str:
    """Concatenate the visible text of an <si> or <is> node (ignores phonetic runs)."""
    if node is None:
        return ""
    parts = []
    for child in node:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "t":
            parts.append(child.text or "")
        elif tag == "r":
            for t in child.findall("m:t", _NS):
                parts.append(t.text or "")
    return "".join(parts)


def read_xlsx(data: bytes) -> list[tuple[str, list[list[str]]]]:
    """Return [(sheet name, rows)] with every cell as a string ('' for empty cells)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise XlsxError("not a zip archive, so not an .xlsx workbook") from exc
    with zf:
        names = set(zf.namelist())
        if "xl/workbook.xml" not in names:
            raise XlsxError("xl/workbook.xml missing, so not an .xlsx workbook")

        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = [_text_of(si) for si in root.findall("m:si", _NS)]

        rels: dict[str, str] = {}
        rels_path = "xl/_rels/workbook.xml.rels"
        if rels_path in names:
            for rel in ET.fromstring(zf.read(rels_path)).findall("pr:Relationship", _NS):
                target = rel.get("Target", "")
                full = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
                rels[rel.get("Id", "")] = full

        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        sheets = []
        for i, sheet in enumerate(workbook.findall("m:sheets/m:sheet", _NS), start=1):
            target = rels.get(sheet.get(_REL_ID, ""), f"xl/worksheets/sheet{i}.xml")
            sheets.append((sheet.get("name", f"sheet{i}"), target))

        out = []
        for name, path in sheets:
            if path not in names:
                raise XlsxError(f"sheet part {path} listed in the workbook but missing from the archive")
            out.append((name, _read_sheet(zf.read(path), shared)))
        return out


def _read_sheet(xml_bytes: bytes, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(xml_bytes)
    rows: list[list[str]] = []
    for row in root.findall("m:sheetData/m:row", _NS):
        cells: dict[int, str] = {}
        next_col = 0
        for c in row.findall("m:c", _NS):
            ref = c.get("r")
            col = _column_index(ref) if ref else next_col
            next_col = col + 1
            ctype = c.get("t", "n")
            v = c.find("m:v", _NS)
            if ctype == "s":
                idx = int(v.text) if v is not None and v.text else -1
                value = shared[idx] if 0 <= idx < len(shared) else ""
            elif ctype == "inlineStr":
                value = _text_of(c.find("m:is", _NS))
            elif ctype == "b":
                value = "TRUE" if v is not None and v.text == "1" else "FALSE"
            else:
                value = v.text if v is not None and v.text is not None else ""
            cells[col] = value
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
        else:
            rows.append([])
    return rows


def rows_to_records(rows: list[list[str]], header_hint: str = "issn") -> list[dict[str, str]]:
    """Turn sheet rows into dicts, using the first row that contains header_hint as the header.

    Returns [] when no row mentions the hint. Short rows are padded with ''.
    """
    for i, row in enumerate(rows):
        if any(header_hint.lower() in (cell or "").lower() for cell in row):
            headers = []
            for j, h in enumerate(row):
                name = (h or "").strip() or f"column_{j + 1}"
                while name in headers:  # repeated header text, keep both columns
                    name += "_2"
                headers.append(name)
            records = []
            for data_row in rows[i + 1 :]:
                if not any((cell or "").strip() for cell in data_row):
                    continue
                padded = list(data_row) + [""] * (len(headers) - len(data_row))
                records.append({h: (padded[j] or "").strip() for j, h in enumerate(headers)})
            return records
    return []


def write_xlsx(sheets: dict[str, list[list[str]]]) -> bytes:
    """Write a minimal workbook (inline strings only). Used to build test fixtures offline."""
    from xml.sax.saxutils import escape

    def col_name(i: int) -> str:
        name = ""
        i += 1
        while i:
            i, rem = divmod(i - 1, 26)
            name = chr(65 + rem) + name
        return name

    main = _NS["m"]
    names = list(sheets)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(names) + 1)
        )
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{overrides}</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        )
        sheet_tags = "".join(
            f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, start=1)
        )
        zf.writestr(
            "xl/workbook.xml",
            f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{main}" xmlns:r="{_NS["r"]}">'
            f"<sheets>{sheet_tags}</sheets></workbook>",
        )
        rels = "".join(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(names) + 1)
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
        for i, name in enumerate(names, start=1):
            rows_xml = []
            for r, row in enumerate(sheets[name], start=1):
                cells = "".join(
                    f'<c r="{col_name(c)}{r}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'
                    for c, v in enumerate(row)
                    if v != ""
                )
                rows_xml.append(f'<row r="{r}">{cells}</row>')
            zf.writestr(
                f"xl/worksheets/sheet{i}.xml",
                f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{main}">'
                f'<sheetData>{"".join(rows_xml)}</sheetData></worksheet>',
            )
    return buf.getvalue()
