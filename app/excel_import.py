"""Excel files for lead import: an .xlsx / .xlsm (openpyxl) or old .xls (xlrd) sheet turned into CSV text, which then
goes through the normal smart import (preview, mapping, commit) exactly like a CSV file.

- Reads cell values only (formulas give their last saved result); macros are never run.
- The first sheet with data is used unless a sheet name is given; every sheet name is returned so the screen can switch.
- Leading empty rows are skipped; the first non-empty row is the column names. Fully empty rows and columns are dropped.
- Numbers that are whole (phone numbers saved as numbers, 9845012345.0) are written without ".0"; dates as YYYY-MM-DD.
- Limits: MAX_BYTES file size, MAX_ROWS rows, MAX_COLS columns, and the CSV must fit the import's own size limit.
"""
import csv
import io
import zipfile
from datetime import date, datetime, time

MAX_BYTES = 15 * 1024 * 1024
MAX_ROWS = 50000
MAX_COLS = 200


class ExcelError(ValueError):
    pass


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0, 0) else v.isoformat(sep=" ", timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def _xlsx_sheets(data: bytes):
    import openpyxl
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise ExcelError("This Excel file could not be read. Open it in Excel and save it again as .xlsx.") from exc
    out = {}
    for ws in book.worksheets:
        out[ws.title] = (lambda ws=ws: ws.iter_rows(values_only=True))
    return out, book


def _xls_sheets(data: bytes):
    import xlrd
    try:
        book = xlrd.open_workbook(file_contents=data)
    except xlrd.XLRDError as exc:
        raise ExcelError("This old-style Excel file (.xls) could not be read. Save it as .xlsx or CSV and try again.") from exc

    def rows(sh):
        for r in range(sh.nrows):
            vals = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    vals.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                else:
                    vals.append(cell.value if cell.value != "" else None)
            yield vals
    return {sh.name: (lambda sh=sh: rows(sh)) for sh in book.sheets()}, None


def _table(rows_iter) -> list:
    table = []
    for row in rows_iter:
        vals = [_cell(v) for v in list(row)[:MAX_COLS + 1]]
        if len(vals) > MAX_COLS:
            raise ExcelError(f"This sheet has more than {MAX_COLS} columns. Remove the extra columns and try again.")
        if not any(vals):
            continue
        table.append(vals)
        if len(table) > MAX_ROWS + 1:
            raise ExcelError(f"This sheet has more than {MAX_ROWS:,} rows. Split it into smaller files.")
    if not table:
        return []
    width = max(len(r) for r in table)
    table = [r + [""] * (width - len(r)) for r in table]
    used = [i for i in range(width) if any(r[i] for r in table)]
    return [[r[i] for i in used] for r in table]


def to_csv(data: bytes, filename: str = "", sheet: str = "") -> dict:
    """{csv, sheet, sheets, rows} for the first sheet with data (or the named one)."""
    if not data:
        raise ExcelError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ExcelError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB. Split it or save only the sheet you need.")
    name = (filename or "").lower()
    if data[:4] == b"PK\x03\x04":
        sheets, book = _xlsx_sheets(data)
    elif data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        sheets, book = _xls_sheets(data)
    else:
        raise ExcelError("This is not an Excel file (.xlsx or .xls)." + (" Upload CSV files as CSV." if name.endswith(".csv") else ""))
    try:
        if sheet and sheet not in sheets:
            raise ExcelError(f"There is no sheet called \"{sheet}\" in this file.")
        chosen, table = sheet, []
        for title in ([sheet] if sheet else list(sheets)):
            table = _table(sheets[title]())
            if table:
                chosen = title
                break
        if not table:
            raise ExcelError("No data found in this Excel file.")
    finally:
        if book is not None:
            book.close()
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(table)
    return {"csv": buf.getvalue(), "sheet": chosen, "sheets": list(sheets), "rows": len(table) - 1, "columns": table[0]}
