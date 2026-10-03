"""In-memory DuckDB holding the scanned workbooks.

Layout
  files        one row per Excel file (catalog)
  sheets       one row per worksheet (header row, preamble, row counts)
  columns      one row per column of every sheet table (name, dtype)
  t_0001 ...   one table per worksheet; columns = the sheet's headers plus
                 _row       original Excel row number (for source citations)
                 _row_kind  'data' | 'subtotal' | 'total'   (filter on 'data' when summing!)
                 _label     the text of a subtotal/total row ("한울산기 소계", "합계")
  value_index  text value -> (sheet table, row, column) for fast "where is X" lookups

Nothing is written to disk. Data lives only as long as the process.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime

import duckdb

from . import config
from .converter import KIND_DATA, SheetTable

_DDL = [
    """CREATE TABLE files (
        file_id INTEGER, rel_path VARCHAR, name VARCHAR, dept VARCHAR, top_folder VARCHAR,
        size BIGINT, modified TIMESTAMP, sha256 VARCHAR, status VARCHAR, error VARCHAR,
        sheet_count INTEGER, data_rows BIGINT,
        fresh VARCHAR, copy_of VARCHAR, data_date VARCHAR)""",
    """CREATE TABLE sheets (
        table_name VARCHAR, file_id INTEGER, sheet_name VARCHAR, sheet_index INTEGER,
        hidden BOOLEAN, header_row INTEGER, n_rows INTEGER, n_data_rows INTEGER, n_cols INTEGER,
        error_cells INTEGER, mixed_columns VARCHAR, preamble VARCHAR, truncated BOOLEAN)""",
    """CREATE TABLE columns (
        table_name VARCHAR, ordinal INTEGER, name VARCHAR, dtype VARCHAR, source_col INTEGER)""",
    """CREATE TABLE value_index (
        value VARCHAR, value_norm VARCHAR, table_name VARCHAR, row_no INTEGER,
        column_name VARCHAR)""",
]


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def normalize_value(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).casefold()


def sql_literal(v) -> str:
    """Render a Python value as a DuckDB SQL literal.

    Values are inlined instead of bound as parameters because parameter binding of tens of
    thousands of values is ~50x slower in DuckDB's Python client. Only the types produced by
    the converter are accepted; anything else raises, so nothing unescaped can get through.
    """
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("NaN/Infinity cannot be stored")
        return repr(v)
    if isinstance(v, str):
        return "'" + v.replace("\x00", "").replace("'", "''") + "'"
    if isinstance(v, datetime):
        return f"TIMESTAMP '{v.isoformat(sep=' ')}'"
    if isinstance(v, date):
        return f"DATE '{v.isoformat()}'"
    raise TypeError(f"unsupported value type: {type(v).__name__}")


def _bulk_insert(con, table: str, rows: list[tuple], ncols: int) -> None:
    if not rows:
        return
    chunk = max(1, 4000 // max(ncols, 1))
    for i in range(0, len(rows), chunk):
        values = ",".join(
            "(" + ",".join(sql_literal(v) for v in row) + ")" for row in rows[i : i + chunk]
        )
        con.execute(f"INSERT INTO {table} VALUES {values}")


class Store:
    def __init__(self) -> None:
        self.con = duckdb.connect(":memory:")
        self.lock = threading.Lock()
        self.file_info: dict[
            str, dict
        ] = {}  # rel_path -> extra facts (tie_with, newer, locked, uncached)
        self._next_file_id = 1
        self._next_table = 1
        for ddl in _DDL:
            self.con.execute(ddl)

    # ------------------------------------------------------------ writing
    def add_file(
        self,
        *,
        rel_path,
        name,
        dept,
        top_folder,
        size,
        modified: datetime,
        sha256,
        status="ok",
        error=None,
    ) -> int:
        file_id = self._next_file_id
        self._next_file_id += 1
        self.con.execute(
            "INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,0,0,NULL,NULL,NULL)",
            [file_id, rel_path, name, dept, top_folder, size, modified, sha256, status, error],
        )
        return file_id

    def add_sheet(self, file_id: int, sheet: SheetTable) -> str | None:
        """Create the sheet's table and its catalog rows (all or nothing).
        Returns the table name (None for a sheet without any column)."""
        self.con.begin()
        try:
            table_name = self._add_sheet(file_id, sheet)
        except Exception:
            self.con.rollback()
            raise
        self.con.commit()
        return table_name

    def _add_sheet(self, file_id: int, sheet: SheetTable) -> str | None:
        table_name = None
        if sheet.columns:
            table_name = f"t_{self._next_table:04d}"
            self._next_table += 1
            col_ddl = ", ".join(f"{quote_ident(c.name)} {c.dtype}" for c in sheet.columns)
            self.con.execute(
                f'CREATE TABLE {table_name} ("_row" INTEGER, "_row_kind" VARCHAR, '
                f'"_label" VARCHAR, {col_ddl})'
            )
            rows = [(r[0], r[1], r[2], *r[3]) for r in sheet.rows]
            _bulk_insert(self.con, table_name, rows, 3 + len(sheet.columns))
            _bulk_insert(
                self.con,
                "columns",
                [
                    (table_name, i + 1, c.name, c.dtype, c.source_col)
                    for i, c in enumerate(sheet.columns)
                ],
                5,
            )
            self._index_values(table_name, sheet)
        self.con.execute(
            "INSERT INTO sheets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                table_name,
                file_id,
                sheet.sheet_name,
                sheet.sheet_index,
                sheet.hidden,
                sheet.header_row,
                len(sheet.rows),
                sheet.n_data_rows,
                len(sheet.columns),
                sheet.error_cells,
                json.dumps(sheet.mixed_columns, ensure_ascii=False),
                json.dumps(sheet.preamble, ensure_ascii=False),
                sheet.truncated,
            ],
        )
        return table_name

    def _index_values(self, table_name: str, sheet: SheetTable) -> None:
        text_cols = [i for i, c in enumerate(sheet.columns) if c.dtype == "VARCHAR"]
        entries = []
        for row_no, kind, _label, values in sheet.rows:
            if kind != KIND_DATA:
                continue
            for i in text_cols:
                v = values[i]
                if isinstance(v, str) and config.INDEX_MIN_LEN <= len(v) <= config.INDEX_MAX_LEN:
                    entries.append(
                        (v, normalize_value(v), table_name, row_no, sheet.columns[i].name)
                    )
        _bulk_insert(self.con, "value_index", entries, 5)

    def finish_file(
        self, file_id: int, sheet_count: int, data_rows: int, *, status="ok", error=None
    ) -> None:
        self.con.execute(
            "UPDATE files SET sheet_count=?, data_rows=?, status=?, error=? WHERE file_id=?",
            [sheet_count, data_rows, status, error, file_id],
        )

    def set_freshness(
        self, rel_path: str, fresh: str | None, copy_of: str | None, data_date: str | None
    ) -> None:
        self.con.execute(
            "UPDATE files SET fresh=?, copy_of=?, data_date=? WHERE rel_path=?",
            [fresh, copy_of, data_date, rel_path],
        )

    # ------------------------------------------------------------ reading
    def query(self, sql: str, params: list | None = None) -> list[tuple]:
        with self.lock:
            return self.con.cursor().execute(sql, params or []).fetchall()

    def files(self) -> list[dict]:
        cols = [
            "file_id",
            "rel_path",
            "name",
            "dept",
            "top_folder",
            "size",
            "modified",
            "status",
            "error",
            "sheet_count",
            "data_rows",
            "fresh",
            "copy_of",
            "data_date",
        ]
        rows = self.query(f"SELECT {', '.join(cols)} FROM files ORDER BY rel_path")
        return [dict(zip(cols, r, strict=True)) for r in rows]

    def sheets_of(self, file_id: int) -> list[dict]:
        rows = self.query(
            "SELECT table_name, sheet_name, sheet_index, hidden, header_row, n_rows, n_data_rows,"
            " n_cols, error_cells, mixed_columns, preamble, truncated"
            " FROM sheets WHERE file_id=? ORDER BY sheet_index",
            [file_id],
        )
        out = []
        for r in rows:
            columns = []
            if r[0]:
                columns = [
                    {"name": c[0], "dtype": c[1], "source_col": c[2]}
                    for c in self.query(
                        "SELECT name, dtype, source_col FROM columns WHERE table_name=?"
                        " ORDER BY ordinal",
                        [r[0]],
                    )
                ]
            out.append(
                {
                    "table": r[0],
                    "sheet": r[1],
                    "index": r[2],
                    "hidden": r[3],
                    "header_row": r[4],
                    "rows": r[5],
                    "data_rows": r[6],
                    "columns": columns,
                    "error_cells": r[8],
                    "mixed_columns": json.loads(r[9] or "[]"),
                    "preamble": json.loads(r[10] or "[]"),
                    "truncated": r[11],
                }
            )
        return out

    def search_value(self, text: str, *, exact: bool = False, limit: int = 50) -> list[dict]:
        """Where does this text appear? Returns file / sheet / row / column for each hit."""
        norm = normalize_value(text)
        if not norm:
            return []
        if exact:
            cond, param = "v.value_norm = ?", norm
        else:
            cond = "v.value_norm LIKE ? ESCAPE '\\'"
            param = "%" + norm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.query(
            "SELECT f.rel_path, s.sheet_name, v.row_no, v.column_name, v.value, v.table_name"
            " FROM value_index v JOIN sheets s ON s.table_name = v.table_name"
            " JOIN files f ON f.file_id = s.file_id"
            f" WHERE {cond} ORDER BY f.rel_path, s.sheet_index, v.row_no LIMIT ?",
            [param, limit],
        )
        return [
            {"file": r[0], "sheet": r[1], "row": r[2], "column": r[3], "value": r[4], "table": r[5]}
            for r in rows
        ]
