"""Excel (.xlsx) downloads: the same rows and columns as the CSV (app/exports.py), with real cell types.

- Numbers are numbers (score, rating, reviews, counts), yes/no flags are TRUE/FALSE, and each `<field>_ist` column is a
  real Excel date-time in India time (the raw ISO `<field>` column stays text, as in the CSV).
- Phone numbers are text cells formatted as text, so Excel keeps the leading + and never turns them into 9.19E+11.
- Any other text starting with = + - @ (or a tab / carriage return) gets a leading ' like `csv_safe`, so it can never run
  as a formula. Control characters Excel refuses are dropped.
- Header row bold and frozen, an AutoFilter over the table, column widths from the content (capped).
- Written with openpyxl's write-only mode into a temporary file that spills to disk past a few MB, then streamed out in
  chunks, so a big list never sits in memory twice.
"""
import json
import re
import tempfile
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .exports import IST

MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NUMERIC = {"score", "rating", "reviews_count", "open_roles", "company_size", "emails_sent", "replies", "group", "group_size",
           "lead_count", "members", "companies", "with_email", "emailed", "opened", "replied", "open_rate", "reply_rate",
           "accept_rate", "interested", "value", "step", "discovered", "new", "merged", "enrolled", "qualified"}
PHONE = {"phone", "phones", "to", "mobile", "whatsapp"}
PHONE_TEXT = re.compile(r"^\+?[\d\s().-]{5,}$")
DATE_FORMAT = "dd mmm yyyy hh:mm AM/PM"
MIN_WIDTH, MAX_WIDTH, WIDTH_SAMPLE = 8, 60, 2000
CHUNK = 64 * 1024
SPOOL = 4 * 1024 * 1024
UNSAFE_START = ("=", "+", "-", "@", "\t", "\r")


def _plain(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return "; ".join(_plain(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def safe_text(text: str) -> str:
    """Text Excel will show as typed: no formula start, no characters the file format forbids."""
    text = ILLEGAL_CHARACTERS_RE.sub("", text)
    return "'" + text if text[:1] in UNSAFE_START else text


def ist_datetime(raw):
    """An ISO timestamp as a naive India-time datetime (Excel has no time zones), or None."""
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return when.astimezone(IST).replace(tzinfo=None)


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value or "").strip().replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return None
    return float(text) if "." in text else int(text)


def typed(column: str, row: dict, columns: set):
    """(value, number_format) for one cell."""
    value = row.get(column)
    if column.endswith("_ist") and column[:-4] in columns:
        when = ist_datetime(row.get(column[:-4]))
        if when is not None:
            return when, DATE_FORMAT
        return safe_text(_plain(value)), None
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return value, None
    if column in PHONE:
        text = ILLEGAL_CHARACTERS_RE.sub("", _plain(value))
        return (text if PHONE_TEXT.match(text) else safe_text(text)), "@"
    if column in NUMERIC or isinstance(value, (int, float)):
        number = _number(value)
        if number is not None:
            return number, None
    return safe_text(_plain(value)), None


def _widths(columns: list, rows: list) -> list:
    widths = [len(c) for c in columns]
    for row in rows[:WIDTH_SAMPLE]:
        for i, c in enumerate(columns):
            v = row.get(c)
            n = 20 if c.endswith("_ist") else len(_plain(v))
            if n > widths[i]:
                widths[i] = n
    return [max(MIN_WIDTH, min(MAX_WIDTH, w + 2)) for w in widths]


def write(columns: list, rows: list, title: str = "Data"):
    """The workbook in a temporary file (rewound), ready to stream."""
    book = Workbook(write_only=True)
    sheet = book.create_sheet(title=re.sub(r"[\[\]:*?/\\]", "", title)[:31] or "Data")
    for i, w in enumerate(_widths(columns, rows), start=1):
        sheet.column_dimensions[get_column_letter(i)].width = w
    sheet.freeze_panes = "A2"
    last = get_column_letter(max(1, len(columns)))
    sheet.auto_filter.ref = f"A1:{last}{len(rows) + 1}"
    bold = Font(bold=True)
    header = []
    for c in columns:
        cell = WriteOnlyCell(sheet, value=safe_text(c))
        cell.font = bold
        header.append(cell)
    sheet.append(header)
    known = set(columns)
    for row in rows:
        out = []
        for c in columns:
            value, fmt = typed(c, row, known)
            if fmt:
                cell = WriteOnlyCell(sheet, value=value)
                cell.number_format = fmt
                out.append(cell)
            else:
                out.append(value)
        sheet.append(out)
    handle = tempfile.SpooledTemporaryFile(max_size=SPOOL)
    book.save(handle)
    handle.seek(0)
    return handle


def chunks(columns: list, rows: list, title: str = "Data"):
    """The .xlsx bytes in pieces (for a StreamingResponse)."""
    handle = write(columns, rows, title)
    try:
        while True:
            piece = handle.read(CHUNK)
            if not piece:
                return
            yield piece
    finally:
        handle.close()
