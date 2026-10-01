"""Turn each worksheet into a clean table (header row + typed columns + row kinds).

Rules (all deterministic, no guessing about meaning):
  1. Header row  = the first row in the top 30 rows that is mostly text (numbers that look like
                   years/months/dates are allowed too) and covers at least 60% of the table
                   width. Rows above it are kept as "preamble" (title, 기준일, 단위 ...).
                   Tables do not have to start in column A.
  2. Subtotal / total rows ("... 소계", "합계", "Total") are KEPT but tagged with _row_kind so a
     query can exclude them (otherwise sums are double counted). Their text goes to _label.
  3. Every row keeps its original Excel row number in _row (needed for source citations).
  4. Column type is inferred from data rows: BIGINT / DOUBLE / DATE / TIMESTAMP / BOOLEAN,
     otherwise VARCHAR. Excel error cells (#N/A ...) become NULL and are counted.
  5. Sheets are opened read-only with the values Excel cached in the file. Formulas are NOT
     recalculated; formulas without a cached value are counted (uncached_formula_cells).
"""

from __future__ import annotations

import math
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import BinaryIO

import openpyxl

from . import config

HEADER_SCAN_ROWS = 30
PREAMBLE_MAX_LINES = 12
PREAMBLE_LINE_MAX_CHARS = 200
MAX_EMPTY_STREAK = 20_000  # stop reading a sheet after this many empty rows in a row

KIND_DATA = "data"
KIND_SUBTOTAL = "subtotal"
KIND_TOTAL = "total"

_UNIT = r"(\s*[\(（\[].*[\)）\]])?\s*$"  # optional trailing "(원)" etc.
_SUBTOTAL_RE = re.compile(r"(소\s*계|sub\s*-?\s*total)" + _UNIT, re.I)
_TOTAL_RE = re.compile(
    r"((?:^|\s)[월일대연]\s*계|합\s*계|총\s*계|누\s*계|총\s*합|grand\s*total|total)" + _UNIT, re.I
)
_LABEL_STRIP = " \t[]【】:："
_BARE_TOTAL_RE = re.compile(r"계\s*$")
_RESERVED = {"_row", "_row_kind", "_label"}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_INT_LIMIT = 9e18
_CACHED_VALUE_RE = re.compile(rb"<v(?:\s[^>/]*)?>")


@dataclass
class Column:
    name: str
    dtype: str
    source_col: int  # 1-based column number in the sheet


@dataclass
class SheetTable:
    sheet_name: str
    sheet_index: int
    hidden: bool
    header_row: int | None  # 1-based Excel row number, None if no header was found
    preamble: list[str] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    # each row: (row_no, row_kind, label, [values aligned with columns])
    rows: list[tuple] = field(default_factory=list)
    error_cells: int = 0
    mixed_columns: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def n_data_rows(self) -> int:
        return sum(1 for r in self.rows if r[1] == KIND_DATA)


# ---------------------------------------------------------------- cell helpers
def _clean(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _text(value) -> str:
    """Stable text form of any cell value (used for VARCHAR columns and the value index)."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and abs(value) < _INT_LIMIT else repr(value)
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time | timedelta):
        return str(value)
    return str(value)


def _row_kind(cells: list) -> tuple[str, str | None]:
    """Return (kind, label). Looks at the first three non-empty cells."""
    seen = 0
    for c in cells:
        if c is None:
            continue
        seen += 1
        if isinstance(c, str):
            text = c.strip(_LABEL_STRIP)
            if _SUBTOTAL_RE.search(text):
                return KIND_SUBTOTAL, c
            if _TOTAL_RE.search(text) or (seen <= 2 and _BARE_TOTAL_RE.fullmatch(text)):
                return KIND_TOTAL, c
        if seen >= 3:
            break
    return KIND_DATA, None


# ---------------------------------------------------------------- header detection
def _first_last(cells: list) -> tuple[int, int]:
    idx = [i for i, c in enumerate(cells) if c is not None]
    return (idx[0], idx[-1]) if idx else (-1, -1)


def _header_like(non_empty: list) -> bool:
    """Mostly text. Numbers are tolerated only when they are clearly column labels: years
    (2024, 2025 ...), dates, or consecutive month/day numbers (1, 2, 3 ...)."""
    numbers = [c for c in non_empty if not isinstance(c, str)]
    if not numbers:
        return True
    if not isinstance(non_empty[0], str):
        return False
    if any(isinstance(c, bool) for c in numbers):
        return False
    if all(isinstance(c, date) for c in numbers):
        return True
    ints = [c for c in numbers if isinstance(c, int)]
    if len(ints) != len(numbers):
        return False
    if all(1900 <= c <= 2100 for c in ints):
        return True
    return (
        len(ints) >= 3
        and all(1 <= c <= 31 for c in ints)
        and all(b - a == 1 for a, b in zip(ints, ints[1:], strict=False))
    )


_NOTE_LABEL_RE = re.compile(
    r"(작성자|작성일|기준일|담당자|부서|작성|문서|단위|마감일|출력일)\s*[:：]?\s*$|[:：]"
)


def _note_like(non_empty: list) -> bool:
    """'작성자: 홍길동' style key/value rows or label cells ending in ':' are not headers."""
    for c in non_empty:
        if isinstance(c, date):
            return True
        if isinstance(c, str) and _NOTE_LABEL_RE.search(c.strip()):
            return True
    return False


def find_header_position(grid: list[tuple[int, list]]) -> int | None:
    """Index into grid of the header row, or None."""
    # Table extent = rows with 2+ filled cells; single-cell rows are titles / notes.
    rows = [c for _, c in grid[:50]]
    spans = [_first_last(c) for c in rows if sum(x is not None for x in c) >= 2]
    if not spans:
        return None
    left = min(s[0] for s in spans)
    right = max(s[1] for s in spans)
    width = right - left + 1
    if width < 2:
        return None
    need = max(2, math.ceil(0.6 * width))
    for pos, (_, cells) in enumerate(grid[:HEADER_SCAN_ROWS]):
        non_empty = [c for c in cells if c is not None]
        if len(non_empty) < need or not _header_like(non_empty):
            continue
        if _note_like(non_empty):
            nxt = grid[pos + 1][1] if pos + 1 < len(grid) else []
            nxt_ne = [c for c in nxt if c is not None]
            if len(nxt_ne) >= need and all(isinstance(c, str) for c in nxt_ne):
                continue
        if width <= 3:
            following = grid[pos + 1 : pos + 6]
            if not any(sum(c is not None for c in g) >= 2 for _, g in following):
                continue
        return pos
    return None


# ---------------------------------------------------------------- typing
def infer_dtype(values: list) -> tuple[str, bool]:
    """(dtype, mixed). `values` are the non-null values of one column."""
    if not values:
        return "VARCHAR", False
    kinds = set()
    for v in values:
        if isinstance(v, bool):
            kinds.add("bool")
        elif isinstance(v, int | float):
            kinds.add("num")
        elif isinstance(v, datetime):
            kinds.add("datetime")
        elif isinstance(v, date):
            kinds.add("date")
        else:
            kinds.add("str")
    if kinds == {"bool"}:
        return "BOOLEAN", False
    if kinds == {"num"}:
        if not all(math.isfinite(v) for v in values):
            return "VARCHAR", True
        all_int = all(
            (isinstance(v, int) or float(v).is_integer()) and abs(v) < _INT_LIMIT for v in values
        )
        return ("BIGINT" if all_int else "DOUBLE"), False
    if kinds <= {"datetime", "date"}:
        midnight = all(not isinstance(v, datetime) or v.time() == time(0) for v in values)
        return ("DATE" if midnight else "TIMESTAMP"), False
    return "VARCHAR", len(kinds) > 1


def coerce(value, dtype: str):
    """Convert a cell value to what the DuckDB column of `dtype` expects (None if impossible)."""
    if value is None:
        return None
    if dtype == "VARCHAR":
        return _text(value)
    if dtype == "BIGINT":
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return int(value) if float(value).is_integer() else None
    if dtype == "DOUBLE":
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value) if math.isfinite(value) else None
    if dtype == "DATE":
        if isinstance(value, datetime):
            return value.date()
        return value if isinstance(value, date) else None
    if dtype == "TIMESTAMP":
        if isinstance(value, datetime):
            return value
        return datetime.combine(value, time(0)) if isinstance(value, date) else None
    if dtype == "BOOLEAN":
        return value if isinstance(value, bool) else None
    return None


def _column_names(raw: list[str | None], source_cols: list[int]) -> list[str]:
    """Readable, unique column names. DuckDB identifiers are case-insensitive, so uniqueness is
    checked case-insensitively; control characters are removed."""
    names: list[str] = []
    used = {r.casefold() for r in _RESERVED}
    for header, col in zip(raw, source_cols, strict=True):
        base = _CONTROL_RE.sub(" ", header) if header else ""
        base = re.sub(r"\s+", " ", base).strip() or f"열{col}"
        if base.casefold() in _RESERVED:
            base = f"col_{base}"
        name, n = base, 2
        while name.casefold() in used:
            name = f"{base}_{n}"
            n += 1
        used.add(name.casefold())
        names.append(name)
    return names


# ---------------------------------------------------------------- sheet conversion
def convert_sheet(ws, sheet_index: int) -> SheetTable:
    hidden = getattr(ws, "sheet_state", "visible") != "visible"
    table = SheetTable(ws.title, sheet_index, hidden, None)

    grid: list[tuple[int, list]] = []
    empty_streak = 0
    for row_no, row in enumerate(ws.iter_rows(), start=1):
        cells = []
        for c in row:
            if getattr(c, "data_type", None) == "e":
                table.error_cells += 1
                cells.append(None)
            else:
                cells.append(_clean(getattr(c, "value", None)))
        if any(c is not None for c in cells):
            empty_streak = 0
            if len(grid) >= config.MAX_ROWS_PER_SHEET:
                table.truncated = True
                break
            grid.append((row_no, cells))
        else:
            empty_streak += 1
            if empty_streak >= MAX_EMPTY_STREAK:
                break  # formatted-but-empty rows at the bottom of the sheet
    if not grid:
        return table

    pos = find_header_position(grid)
    if pos is None:
        header_cells: list = []
        body = grid
    else:
        table.header_row = grid[pos][0]
        header_cells = grid[pos][1]
        body = grid[pos + 1 :]
        for _, cells in grid[:pos]:
            line = " | ".join(_text(c) for c in cells if c is not None)
            if line:
                table.preamble.append(line[:PREAMBLE_LINE_MAX_CHARS])
        table.preamble = table.preamble[:PREAMBLE_MAX_LINES]

    width = max([len(header_cells)] + [_first_last(c)[1] + 1 for _, c in body] + [0])
    keep = [
        j
        for j in range(width)
        if (j < len(header_cells) and header_cells[j] is not None)
        or any(j < len(c) and c[j] is not None for _, c in body)
    ]
    if not keep:
        return table

    raw_headers = [
        _text(header_cells[j]) if j < len(header_cells) and header_cells[j] is not None else None
        for j in keep
    ]
    names = _column_names(raw_headers, [j + 1 for j in keep])

    kinds: list[tuple[int, str, str | None, list]] = []
    for row_no, cells in body:
        values = [cells[j] if j < len(cells) else None for j in keep]
        kind, label = _row_kind(cells)
        kinds.append((row_no, kind, label, values))

    for ci, name in enumerate(names):
        data_vals = [v[ci] for _, k, _, v in kinds if k == KIND_DATA and v[ci] is not None]
        other_vals = [v[ci] for _, k, _, v in kinds if k != KIND_DATA and v[ci] is not None]
        dtype, mixed = infer_dtype(data_vals or other_vals)
        if dtype == "BIGINT" and any(infer_dtype([v])[0] == "DOUBLE" for v in other_vals):
            dtype = "DOUBLE"  # a subtotal with decimals must not be silently dropped
        if mixed:
            table.mixed_columns.append(name)
        table.columns.append(Column(name, dtype, keep[ci] + 1))

    dtypes = [c.dtype for c in table.columns]
    for row_no, kind, label, values in kinds:
        coerced = [coerce(v, dt) for v, dt in zip(values, dtypes, strict=True)]
        table.rows.append((row_no, kind, label, coerced))
    return table


# ---------------------------------------------------------------- workbook level
def check_archive(source: str | Path | BinaryIO) -> None:
    """Refuse zip bombs: the uncompressed size of an .xlsx must stay below a limit."""
    with zipfile.ZipFile(source) as zf:
        total = sum(i.file_size for i in zf.infolist())
    if hasattr(source, "seek"):
        source.seek(0)
    if total > config.MAX_UNCOMPRESSED_BYTES:
        raise ValueError(f"압축 해제 크기가 너무 큽니다 ({total // (1024 * 1024)}MB)")


def uncached_formula_cells(source: str | Path | BinaryIO) -> int:
    """Number of formula cells that have no cached value (they read as empty)."""
    count = 0
    with zipfile.ZipFile(source) as zf:
        for info in zf.infolist():
            if not (info.filename.startswith("xl/worksheets/") and info.filename.endswith(".xml")):
                continue
            xml = zf.read(info)
            for m in re.finditer(rb"<f[\s>/]", xml):
                end = xml.find(b"</c>", m.start())
                segment = xml[m.start() : end if end != -1 else len(xml)]
                if not _CACHED_VALUE_RE.search(segment):  # `<v />` is an empty, i.e. missing, cache
                    count += 1
    if hasattr(source, "seek"):
        source.seek(0)
    return count


def convert_workbook(source: str | Path | BinaryIO) -> list[SheetTable]:
    """Read every worksheet of a workbook. Raises on unreadable / password-protected files."""
    wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
    try:
        tables = []
        for idx, ws in enumerate(wb.worksheets):
            if hasattr(ws, "reset_dimensions"):
                ws.reset_dimensions()
            tables.append(convert_sheet(ws, idx))
        return tables
    finally:
        wb.close()
