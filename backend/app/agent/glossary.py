"""Term dictionary (seed) + period rules. Grows later from user confirmations.

Grades returned by `match_term`:
  정확    the term is a standard term or an actual column name
  동의어  the term is a registered synonym
  미등록  not registered; candidates are guessed from column names (-> confidence 낮음)
"""

from __future__ import annotations

import calendar
import re
from datetime import date

GLOSSARY: list[dict] = [
    {
        "term": "매출실적",
        "columns": ["매출실적", "공급가액", "매출액", "금액"],
        "synonyms": ["실적", "매출", "매상", "판매액", "매출액"],
        "note": "부가세 제외 기준",
    },
    {"term": "목표", "columns": ["목표"], "synonyms": ["목표액", "타겟"], "note": ""},
    {
        "term": "팀",
        "columns": ["팀명", "담당팀", "부서"],
        "synonyms": ["팀명", "담당팀", "부서"],
        "note": "",
    },
    {
        "term": "거래처",
        "columns": ["거래처명", "업체명", "고객사", "거래처", "공급처"],
        "synonyms": ["고객", "업체", "고객사", "거래처명", "공급처"],
        "note": "",
    },
    {"term": "수량", "columns": ["수량", "발주수량", "입고수량"], "synonyms": ["물량"], "note": ""},
    {
        "term": "재고",
        "columns": ["기말재고", "기초재고", "안전재고"],
        "synonyms": ["재고수량", "기말재고", "현재고"],
        "note": "기말재고 = 해당 월말 재고",
    },
    {
        "term": "미수금",
        "columns": ["당월잔액", "전월잔액"],
        "synonyms": ["미수", "외상", "채권"],
        "note": "",
    },
    {"term": "계약금액", "columns": ["계약금액"], "synonyms": ["계약액"], "note": ""},
    {
        "term": "품목",
        "columns": ["품목명", "품목", "품목코드"],
        "synonyms": ["제품", "상품", "아이템", "자재"],
        "note": "",
    },
    {
        "term": "창고",
        "columns": ["창고"],
        "synonyms": ["보관창고", "물류창고"],
        "note": "재고현황은 창고별 시트로 나뉘어 있어 합쳐서 집계(also_tables)해야 전체 재고가 됩니다",
    },
    {
        "term": "입고",
        "columns": ["입고", "입고수량", "반품입고"],
        "synonyms": ["입고량"],
        "note": "",
    },
    {
        "term": "출고",
        "columns": ["출고"],
        "synonyms": ["출고량"],
        "note": "",
    },
    {
        "term": "수주일",
        "columns": ["수주일"],
        "synonyms": ["주문일", "오더일"],
        "note": "",
    },
    {
        "term": "결제조건",
        "columns": ["결제조건"],
        "synonyms": ["결제", "지급조건"],
        "note": "",
    },
    {
        "term": "납품실적",
        "columns": ["1분기 납품실적", "2분기 납품실적", "3분기 납품실적", "4분기 납품실적"],
        "synonyms": ["납품"],
        "note": "영업팀 수기 입력",
    },
]

# Columns that hold money (shown as 억/만원 and unit-normalised to 원)
MONEY_COLUMNS = {
    "매출실적",
    "목표",
    "공급가액",
    "부가세",
    "합계",
    "단가",
    "금액",
    "계약금액",
    "당월발생",
    "당월입금",
    "당월잔액",
    "전월잔액",
}


def is_money_column(name: str) -> bool:
    return name in MONEY_COLUMNS or name.endswith("납품실적")


def match_term(term: str, all_columns: set[str], extra: list[dict] | None = None) -> dict:
    """Map one user term to a standard term and candidate column names.
    `extra` are the company's own entries (confirmed by users); they are checked first."""
    t = term.strip()
    entries = [*(extra or []), *GLOSSARY]
    for g in entries:
        if t == g["term"] or t in g["columns"]:
            return _hit("정확", g, all_columns)
    for g in entries:
        if t in g["synonyms"]:
            return _hit("동의어", g, all_columns)
    guess = sorted(c for c in all_columns if t and (t in c or c in t))[:6]
    return {"term": t, "grade": "미등록", "standard_term": None, "columns": guess, "note": ""}


def _hit(grade: str, g: dict, all_columns: set[str]) -> dict:
    return {
        "term": g["term"] if grade == "정확" else None,
        "grade": grade,
        "standard_term": g["term"],
        "columns": [c for c in g["columns"] if c in all_columns],
        "note": g["note"],
    }


_YEAR = re.compile(r"(20\d{2})\s*년")
_QUARTER = re.compile(r"([1-4])\s*분기")
_MONTH = re.compile(r"(?<!\d)(1[0-2]|[1-9])\s*월")
_HALF = re.compile(r"(상반기|하반기)")


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def parse_period(text: str, latest_year: int) -> dict | None:
    """Turn '3분기', '2025년 상반기', '9월' ... into a date range. None if no period words."""
    y = _YEAR.search(text)
    year = int(y.group(1)) if y else latest_year
    default_year = y is None
    if q := _QUARTER.search(text):
        n = int(q.group(1))
        start, end = date(year, 3 * n - 2, 1), _month_end(year, 3 * n)
        label = f"{n}분기"
    elif h := _HALF.search(text):
        first = h.group(1) == "상반기"
        start, end = (
            (date(year, 1, 1), date(year, 6, 30))
            if first
            else (date(year, 7, 1), date(year, 12, 31))
        )
        label = h.group(1)
    elif m := _MONTH.search(text):
        n = int(m.group(1))
        start, end = date(year, n, 1), _month_end(year, n)
        label = f"{n}월"
    elif y:
        start, end, label = date(year, 1, 1), date(year, 12, 31), "연간"
    else:
        return None
    return {
        "text": label,
        "year": year,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "year_defaulted": default_year,
        "months": [f"{mm}월" for mm in range(start.month, end.month + 1)],
    }


def standard_terms_of(column: str | None) -> set[str]:
    """Which standard terms (지표) a column name belongs to. Empty if the column is not in the glossary."""
    if not column:
        return set()
    return {g["term"] for g in GLOSSARY if column in g["columns"]}
