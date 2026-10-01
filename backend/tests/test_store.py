from __future__ import annotations

import random
import string
from datetime import date, datetime

import pytest

from app.converter import convert_workbook
from app.store import Store, normalize_value, sql_literal

from .conftest import write_workbook


def test_sql_literal_roundtrip_hostile_strings():
    store = Store()
    store.con.execute("CREATE TABLE t (v VARCHAR)")
    nasty = [
        "it's",
        "a''b",
        "back\\slash",
        "새\n줄",
        "emoji 😀",
        "'; DROP TABLE t; --",
        "x\x00y",
        "",
    ]
    rng = random.Random(1)
    alphabet = string.printable + "가나다라마바사'\"\\"
    nasty += ["".join(rng.choice(alphabet) for _ in range(40)) for _ in range(200)]
    for s in nasty:
        store.con.execute(f"INSERT INTO t VALUES ({sql_literal(s)})")
    got = [r[0] for r in store.con.execute("SELECT v FROM t").fetchall()]
    assert got == [s.replace("\x00", "") for s in nasty]
    assert store.con.execute("SELECT count(*) FROM t").fetchone()[0] == len(nasty)


def test_sql_literal_types():
    assert sql_literal(None) == "NULL"
    assert sql_literal(True) == "TRUE" and sql_literal(7) == "7"
    assert sql_literal(date(2026, 1, 2)) == "DATE '2026-01-02'"
    assert sql_literal(datetime(2026, 1, 2, 3, 4, 5)) == "TIMESTAMP '2026-01-02 03:04:05'"
    with pytest.raises(TypeError):
        sql_literal(object())


@pytest.fixture
def loaded(tmp_path):
    path = write_workbook(
        tmp_path / "x.xlsx",
        {
            "거래": [
                ["제목"],
                ["고객", "금액", "일자"],
                ["Bangkok Industrial Supply", 100, datetime(2026, 7, 24)],
                ["한울산기", 50, datetime(2026, 7, 10)],
                ["한울산기 소계", 50, None],
                ["합계", 150, None],
            ]
        },
    )
    store = Store()
    fid = store.add_file(
        rel_path="회계팀/x.xlsx",
        name="x.xlsx",
        dept="회계팀",
        top_folder="회계팀",
        size=1,
        modified=datetime(2026, 1, 1),
        sha256="h",
    )
    for sh in convert_workbook(path):
        store.add_sheet(fid, sh)
    return store, fid


def test_table_has_position_columns_and_typed_values(loaded):
    store, fid = loaded
    sheet = store.sheets_of(fid)[0]
    rows = store.query(
        f'SELECT "_row", "_row_kind", "고객", "금액", "일자" FROM {sheet["table"]} ORDER BY 1'
    )
    assert rows[0] == (3, "data", "Bangkok Industrial Supply", 100, date(2026, 7, 24))
    assert [r[1] for r in rows] == ["data", "data", "subtotal", "total"]
    total = store.query(f'SELECT sum("금액") FROM {sheet["table"]} WHERE "_row_kind" = \'data\'')[
        0
    ][0]
    assert total == 150  # subtotal/total rows are not double counted


def test_value_index_search(loaded):
    store, _ = loaded
    hits = store.search_value("bangkok industrial")
    assert hits == [
        {
            "file": "회계팀/x.xlsx",
            "sheet": "거래",
            "row": 3,
            "column": "고객",
            "value": "Bangkok Industrial Supply",
            "table": hits[0]["table"],
        }
    ]
    assert [h["row"] for h in store.search_value("한울산기")] == [4]  # subtotal row not indexed
    assert store.search_value("한울산기", exact=True)[0]["value"] == "한울산기"
    assert store.search_value("%") == []  # wildcard characters are escaped
    assert store.search_value("   ") == []


def test_normalize_value():
    assert normalize_value("  Hanoi   Machinery Co. ") == "hanoi machinery co."


# ---- regressions found in code review ------------------------------------------------------
def test_non_finite_floats_are_refused():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            sql_literal(bad)


def test_failed_sheet_leaves_no_partial_table():
    from app.converter import Column, SheetTable

    store = Store()
    fid = store.add_file(
        rel_path="a.xlsx",
        name="a.xlsx",
        dept="미분류",
        top_folder="",
        size=1,
        modified=datetime(2026, 1, 1),
        sha256="h",
    )
    bad = SheetTable(
        "bad",
        0,
        False,
        1,
        columns=[Column("d", "DATE", 1)],
        rows=[(2, "data", None, ["not a date"])],
    )
    with pytest.raises(Exception):  # noqa: B017 - DuckDB conversion error
        store.add_sheet(fid, bad)
    assert store.query("SELECT count(*) FROM sheets")[0][0] == 0
    assert store.query("SELECT count(*) FROM columns")[0][0] == 0
    assert (
        store.query("SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 't_%'")[
            0
        ][0]
        == 0
    )
    good = SheetTable(
        "ok", 1, False, 1, columns=[Column("d", "BIGINT", 1)], rows=[(2, "data", None, [1])]
    )
    assert store.add_sheet(fid, good) == "t_0002"  # the store is still usable
