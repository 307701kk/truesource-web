"""Shared fixtures: small Excel shares built on the fly (no dependency on the real dataset)."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest


def set_mtime(path: Path, when: str) -> None:
    ts = datetime.strptime(when, "%Y-%m-%d %H:%M").timestamp()
    os.utime(path, (ts, ts))


def write_workbook(path: Path, sheets: dict[str, list[list]], hidden: tuple[str, ...] = ()) -> Path:
    """sheets: {sheet_name: list of rows}. A row is a list of cell values (None = empty)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
        if name in hidden:
            ws.sheet_state = "hidden"
    wb.save(path)
    return path


PERFORMANCE_ROWS = [
    ["2026년 영업 실적 집계"],
    ["기준일: 2026-09-28"],
    ["(단위: 원)"],
    [],
    ["팀명", "월", "매출실적", "목표"],
    ["영업1팀", "7월", 100, 90.5],
    ["영업2팀", "7월", 200, 180.5],
]


@pytest.fixture
def share(tmp_path: Path) -> Path:
    root = tmp_path / "share"
    perf_old = write_workbook(
        root / "영업팀/2026/실적/실적집계.xlsx",
        {
            "팀별실적": [r[:] for r in PERFORMANCE_ROWS[:1]]
            + [["기준일: 2026-09-15"]]
            + PERFORMANCE_ROWS[2:]
        },
    )
    set_mtime(perf_old, "2026-09-15 17:56")
    perf_v2 = write_workbook(
        root / "영업팀/2026/실적/실적집계_v2.xlsx", {"팀별실적": PERFORMANCE_ROWS}
    )
    set_mtime(perf_v2, "2026-09-28 13:10")
    copy = root / "공용/받은자료/실적집계_v2.xlsx"
    copy.parent.mkdir(parents=True)
    shutil.copy(perf_v2, copy)
    set_mtime(copy, "2026-09-29 11:42")

    ledger = write_workbook(
        root / "회계팀/월마감/2026-07/매출장_202607.xlsx",
        {
            "매출장": [
                ["2026년 7월 매출장"],
                ["회계팀"],
                [],
                ["일자", "업체명", "공급가액", "비고"],
                ["07/24", "Bangkok Industrial Supply", 7110000, None],
                ["07/10", "한울산기", 1000, None],
                [None, "한울산기 소계", 1000, None],
                ["합계", None, 7111000, None],
            ]
        },
    )
    set_mtime(ledger, "2026-08-07 09:00")
    memo = write_workbook(root / "개인/메모.xlsx", {"시트1": [["a", "b"], [1, 2]]})
    set_mtime(memo, "2026-01-01 09:00")
    (root / "영업팀/2026/실적/~$실적집계.xlsx").write_bytes(b"lock")
    (root / "영업팀/구버전.xls").write_bytes(b"old format")
    (root / "영업팀/깨진파일.xlsx").write_text("this is not a zip", encoding="utf-8")
    (root / "영업팀/메모.txt").write_text("ignored", encoding="utf-8")
    return root
