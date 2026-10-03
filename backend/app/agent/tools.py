"""The agent's tools. All of them are local code over the DuckDB store.

The LLM only ever receives each tool's summary (aggregates, locations, counts) - never raw rows.
Per-question state lives in QuestionCtx; the confidence rules and the source list are built from it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from openpyxl.utils import get_column_letter

from .. import catalog, config
from ..store import Store, normalize_value, quote_ident
from . import glossary, rules
from .audits import EXCERPT, AuditMixin

TOOL_LABELS = {
    "lookup_terms": "용어 조회",
    "search_catalog": "카탈로그 검색",
    "search_value": "값 검색",
    "run_query": "쿼리 실행",
    "cross_verify": "교차검증",
    "trace_difference": "차이 추적",
    "check_quality": "품질 조회",
    "ask_user": "사용자에게 묻기",
    "final_answer": "답변 작성",
}
ID_COLUMNS = {
    "전표번호",
    "원전표번호",
    "사업자번호",
    "수주번호",
    "발주번호",
    "계약번호",
    "No",
}
AGGS = {
    "sum": "SUM",
    "avg": "AVG",
    "min": "MIN",
    "max": "MAX",
    "count": "COUNT",
    "count_distinct": "COUNT(DISTINCT",
}
OPS = {"=", "!=", ">", ">=", "<", "<=", "in", "contains", "between"}
NUMERIC_TYPES = ("BIGINT", "INTEGER", "DOUBLE", "DECIMAL")
MAX_UNION_TABLES = 40
_RANKING = re.compile(
    r"\d+\s*(?:위|등)|1\s*등|가장|제일|최고|최대|최저|최소|상위|하위|순위|랭킹|top\s*\d*", re.I
)
MIN_RANK_ITEMS = 3  # a ranking can only be checked on several items, not on the winner alone
# metrics that describe the same thing recorded at different points (shipped vs invoiced)
RELATED_METRICS = [{"매출실적", "납품실적"}]
# stock numbers (a level at a date) can never be added across dates or files
STOCK_COLUMNS = {"기말재고", "기초재고", "안전재고", "전월잔액", "당월잔액"}
_DAY_DATE = re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일|\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}")
_FIGURE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:억|만원|원)")


@dataclass
class QuestionCtx:
    question: str
    indexed_at: str | None = None
    calls: int = 0
    trace: list[dict] = field(default_factory=list)
    term_grades: set[str] = field(default_factory=set)
    defaults_applied: list[str] = field(default_factory=list)
    queries: dict[str, dict] = field(default_factory=dict)
    cross: dict | None = None
    traced: bool = False
    quality_notes: list[str] = field(default_factory=list)
    stale_used: set[str] = field(default_factory=set)
    copy_used: set[str] = field(default_factory=set)
    anomalies: dict[str, bool] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    search_hits: list[dict] = field(default_factory=list)
    searched: bool = False
    tool_results: list[dict] = field(default_factory=list)
    ask: dict | None = None
    final: dict | None = None
    number_check: str = "none"  # none | ok | failed
    number_retries: int = 0
    bad_numbers: list[str] = field(default_factory=list)
    unregistered: list[str] = field(default_factory=list)
    no_data: dict | None = None  # set by the runner when nothing relevant was found
    catalog_searches: list[dict] = field(default_factory=list)  # [{columns, found}]
    value_searches: list[dict] = field(default_factory=list)  # [{text, found, fuzzy}]
    similar_names: list[str] = field(default_factory=list)  # near-miss names for the no-data screen
    call_cache: dict = field(default_factory=dict)  # identical tool calls are answered from here
    cross_unavailable: str | None = None  # why no second independent source exists (model's reason)
    refunds: int = 0  # failed / empty calls that did not use the call budget
    cross_rejections: int = 0  # times a final answer was refused for lacking a cross-check
    rank_rejections: int = 0  # times a cross-check of a ranking compared too few items
    llm_calls: int = 0
    llm_wait: float = 0.0  # seconds the model calls waited for rate limits
    findings: list[dict] = field(default_factory=list)  # raised confidence rules (rules.py)
    period: dict | None = None
    audited_files: set[str] = field(default_factory=set)
    audited_tables: set[str] = field(default_factory=set)
    audited_inject: set[str] = field(default_factory=set)
    budget_hit: bool = False
    unstructured: bool = False
    _seen: set = field(default_factory=set)

    @property
    def asks_for_ranking(self) -> bool:
        """The question wants a rank / the top / the most: then who comes first must be verified."""
        return bool(_RANKING.search(self.question))

    def flag(self, rule_id: str, where: dict | None = None, **detail) -> dict | None:
        """Raise a confidence rule once per (rule, place). Returns the finding if it is new."""
        f = rules.make_finding(rule_id, where, **detail)
        key = (
            rule_id,
            (where or {}).get("path"),
            (where or {}).get("sheet"),
            (where or {}).get("rows"),
            f["title"],
        )
        if key in self._seen:
            return None
        self._seen.add(key)
        self.findings.append(f)
        return f

    def step(self, tool: str, detail: str, status: str = "ok") -> None:
        self.trace.append({"step": TOOL_LABELS.get(tool, tool), "detail": detail, "status": status})


def axis_of(entry: dict) -> dict | None:
    """Time axis of a catalog entry: its date range or its months (None for a snapshot table)."""
    if entry.get("date_range"):
        lo, _, hi = entry["date_range"].partition("~")
        try:
            return {"start": date.fromisoformat(lo), "end": date.fromisoformat(hi), "months": None}
        except ValueError:
            return None
    if entry.get("month_values"):
        return {"start": None, "end": None, "months": set(entry["month_values"])}
    return None


def axes_overlap(a: dict, b: dict) -> bool:
    """Do two tables cover the same time? Summing them would count that time twice."""
    if a["months"] is not None and b["months"] is not None:
        return bool(a["months"] & b["months"])
    if a["months"] is None and b["months"] is None:
        return a["start"] <= b["end"] and b["start"] <= a["end"]
    return False


def fmt_won(v: float | None) -> str:
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:.2f}억"
    if a >= 1e4:
        return f"{v / 1e4:,.0f}만원"
    return f"{v:,.0f}원"


def fmt_num(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.2f}"
    return f"{v:,.0f}"


def _unit_mult(preamble: list[str]) -> int:
    for line in preamble:
        m = re.search(r"단위\s*:\s*([^,)\s]+)", line)
        if m:
            return {"천원": 1_000, "백만원": 1_000_000, "억원": 100_000_000}.get(m.group(1), 1)
    return 1


class ToolBox(AuditMixin):
    def __init__(
        self,
        store: Store,
        ctx: QuestionCtx,
        glossary_extra: list[dict] | None = None,
        root: str | None = None,
    ) -> None:
        self.store = store
        self.ctx = ctx
        self.root = root
        self.glossary_extra = glossary_extra or []
        self._meta: dict[str, dict] = {}
        self._all_columns: set[str] | None = None

    # ------------------------------------------------------------------ helpers
    def table_meta(self, table: str) -> dict:
        if table in self._meta:
            return self._meta[table]
        if not re.fullmatch(r"t_\d{4}", table or ""):
            raise ValueError(f"표 이름이 올바르지 않습니다: {table}")
        rows = self.store.query(
            "SELECT f.rel_path, f.name, f.dept, f.modified, f.fresh, f.copy_of, f.data_date,"
            " s.sheet_name, s.preamble, f.size, s.hidden, s.error_cells, s.mixed_columns, s.truncated,"
            " s.header_row FROM sheets s JOIN files f ON f.file_id=s.file_id"
            " WHERE s.table_name=?",
            [table],
        )
        if not rows:
            raise ValueError(f"해당 표가 없습니다: {table}")
        r = rows[0]
        preamble = json.loads(r[8] or "[]")
        col_rows = self.store.query(
            "SELECT name, dtype, source_col FROM columns WHERE table_name=? ORDER BY ordinal",
            [table],
        )
        cols = {c[0]: c[1] for c in col_rows}
        letters = {c[0]: get_column_letter(c[2]) for c in col_rows}
        meta = {
            "table": table,
            "path": r[0],
            "file": r[1],
            "dept": r[2],
            "modified": r[3],
            "fresh": r[4],
            "copy_of": r[5],
            "data_date": r[6],
            "sheet": r[7],
            "preamble": preamble,
            "unit_mult": _unit_mult(preamble),
            "columns": cols,
            "letters": letters,
            "size": r[9],
            "hidden": bool(r[10]),
            "error_cells": r[11] or 0,
            "mixed": json.loads(r[12] or "[]"),
            "truncated": bool(r[13]),
            "header_row": r[14],
        }
        self._meta[table] = meta
        return meta

    def all_columns(self) -> set[str]:
        if self._all_columns is None:
            self._all_columns = {
                r[0] for r in self.store.query("SELECT DISTINCT name FROM columns")
            }
        return self._all_columns

    def latest_year(self) -> int:
        rows = self.store.query(
            "SELECT max(data_date) FROM files WHERE fresh='ok' AND data_date IS NOT NULL"
        )
        if rows and rows[0][0]:
            return int(str(rows[0][0])[:4])
        return date.today().year

    def _title(self, meta: dict) -> str:
        return meta["preamble"][0] if meta["preamble"] else ""

    # ------------------------------------------------------------------ 1. lookup_terms
    def lookup_terms(self, terms: list[str]) -> dict:
        cols = self.all_columns()
        latest = self.latest_year()
        mappings, period = [], None
        for t in terms or []:
            p = glossary.parse_period(t, latest)
            if p:
                period = period or p
                if p["year_defaulted"]:
                    note = f"연도 미지정 → {p['year']} 기본값"
                    if note not in self.ctx.defaults_applied:
                        self.ctx.defaults_applied.append(note)
                continue
            m = glossary.match_term(t, cols, self.glossary_extra)
            where_found = self._value_columns(t) if m["grade"] == "미등록" else []
            if (
                where_found
            ):  # a name that exists in the data (거래처, 팀 ...): a value, not a column term
                mappings.append(
                    {
                        "input": t,
                        "grade": "값",
                        "standard_term": None,
                        "columns": where_found,
                        "note": "데이터 안에 있는 값(이름)입니다. 컬럼이 아니라 filters 의 value 로 쓰세요.",
                    }
                )
                continue
            if m["grade"] == "미등록" and self._names_file_or_sheet(t):
                mappings.append(
                    {
                        "input": t,
                        "grade": "파일",
                        "standard_term": None,
                        "columns": [],
                        "note": "파일·시트·부서 이름입니다. 컬럼이 아니라 search_catalog 의 dept 나 파일 선택에 쓰세요.",
                    }
                )
                continue
            mappings.append(m | {"input": t})
            self.ctx.term_grades.add(m["grade"])
            if m["grade"] == "미등록" and t not in self.ctx.unregistered:
                self.ctx.unregistered.append(t)
        detail = ", ".join(
            f"{m['input']}→{'/'.join(m['columns'][:2]) or '?'}({m['grade']})" for m in mappings
        )
        if period:
            detail += f"{', ' if detail else ''}{period['text']}→{period['start']}~{period['end']}"
        self.ctx.period = period or self.ctx.period
        self.ctx.step(
            "lookup_terms", detail or "-", "warn" if "미등록" in self.ctx.term_grades else "ok"
        )
        return {"mappings": mappings, "period": period, "latest_data_year": latest}

    def _names_file_or_sheet(self, text: str) -> bool:
        t = text.strip()
        if not t:
            return False
        if t in config.KNOWN_DEPARTMENTS:
            return True
        like = f"%{t}%"
        return bool(
            self.store.query(
                "SELECT 1 FROM files WHERE name LIKE ? UNION ALL SELECT 1 FROM sheets WHERE sheet_name LIKE ? LIMIT 1",
                [like, like],
            )
        )

    def _value_columns(self, text: str) -> list[str]:
        """Columns in which this exact text occurs as a value (empty if it is not data)."""
        norm = normalize_value(text)
        if not norm:
            return []
        rows = self.store.query(
            "SELECT DISTINCT column_name FROM value_index WHERE value_norm = ? LIMIT 5", [norm]
        )
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ 2. search_catalog
    def pre_lookup(self, question: str) -> dict | None:
        """What code can already tell from the question itself: glossary words, a period, and files it
        names. Handed to the model with the question so it does not need extra round trips."""
        words: list[str] = []
        for g in [*self.glossary_extra, *glossary.GLOSSARY]:
            for w in [g["term"], *g["synonyms"], *g["columns"]]:
                if len(w) >= 2 and w in question and w not in words:
                    words.append(w)
        words = [w for w in words if not any(w != o and w in o for o in words)][:6]
        periods = re.findall(
            r"(?:\d{4}\s*년\s*)?(?:[1-4]\s*분기|상반기|하반기|\d{1,2}\s*월)|\d{4}\s*년", question
        )
        terms = [*words, *dict.fromkeys(periods[:2])]
        files: list[str] = []
        for tok in re.findall(r"[가-힣A-Za-z0-9_]{3,}", question):
            for k in range(len(tok), 2, -1):  # "계약현황에서" -> "계약현황"
                rows = self.store.query(
                    "SELECT DISTINCT name FROM files WHERE fresh='ok' AND name LIKE ? LIMIT 3",
                    [f"%{tok[:k]}%"],
                )
                if rows:
                    files += [r[0] for r in rows if r[0] not in files]
                    break
        if not terms and not files:
            return None
        out: dict = {}
        if terms:
            res = self.lookup_terms(terms)
            out = {"mappings": res["mappings"], "period": res["period"]}
        if files:
            out["mentioned_files"] = files[:5]
            out["note"] = (
                "질문에 파일 이름이 있으면 search_catalog 의 file 로 그 파일을 지정하세요."
            )
        return out

    def search_catalog(
        self,
        columns: list[str],
        year: int | None = None,
        dept: str | None = None,
        file: str | None = None,
    ) -> dict:
        want = [c for c in (columns or []) if c]
        if not want:
            return {"error": "columns 를 지정하세요."}
        # "공급가액|매출실적" means either column will do
        alts = [[a.strip() for a in w.split("|") if a.strip()] for w in want]
        year = year or (self.ctx.period or {}).get("year")  # a question about 2026 wants 2026 files
        rows = self.store.query(
            "SELECT s.table_name, f.rel_path, f.name, f.dept, f.modified, f.fresh, f.copy_of, f.data_date,"
            " s.sheet_name, s.n_data_rows, s.preamble FROM sheets s JOIN files f ON f.file_id=s.file_id"
            " WHERE s.table_name IS NOT NULL AND f.status IN ('ok','partial') ORDER BY f.rel_path, s.sheet_index"
        )
        cands, excluded, near = [], [], []
        layouts: dict[tuple, list[dict]] = {}
        for t, path, name, d, modified, fresh, copy_of, data_date, sheet, n, pre in rows:
            if dept and d != dept:
                continue
            if file and file.strip() not in path:  # the question named a file (e.g. 계약현황)
                continue
            cols = {
                c[0]: c[1]
                for c in self.store.query("SELECT name, dtype FROM columns WHERE table_name=?", [t])
            }
            matched = [
                w
                for w, alt in zip(want, alts, strict=True)
                if any(a == c or a in c for a in alt for c in cols)
            ]
            if len(matched) < len(want):
                if (matched or file) and fresh == "ok":  # a named file: always show its columns
                    near.append(
                        {
                            "n": len(matched),
                            "table": t,
                            "file": name,
                            "sheet": sheet,
                            "dept": d,
                            "has": matched,
                            "missing": [w for w in want if w not in matched],
                            "data_date": data_date,
                            "columns": list(cols),
                        }
                    )
                continue
            preamble = json.loads(pre or "[]")
            title = preamble[0] if preamble else ""
            if (
                year
                and (m := re.search(r"(20\d{2})", title + name + path))
                and int(m.group(1)) != year
            ):
                continue
            if fresh != "ok":
                excluded.append(
                    {
                        "file": name,
                        "path": path,
                        "reason": "구버전" if fresh == "stale" else f"사본({copy_of})",
                        "data_date": data_date,
                    }
                )
                continue
            info = self.store.file_info.get(path, {})
            if info.get("tie_with"):  # same numbers in another layout (e.g. unit 천원): keep one
                group = [path, *info["tie_with"]]
                primary = min(group, key=lambda p: (len(p.rsplit("/", 1)[-1]), p))
                if path != primary and self._same_data(path, info["tie_with"][0])[0]:
                    excluded.append(
                        {
                            "file": name,
                            "path": path,
                            "reason": f"서식만 다른 사본(같은 자료, 기준은 {primary.rsplit('/', 1)[-1]})",
                            "data_date": data_date,
                        }
                    )
                    continue
            entry = {
                "table": t,
                "file": name,
                "path": path,
                "dept": d,
                "sheet": sheet,
                "modified": modified.strftime("%Y-%m-%d %H:%M"),
                "data_date": data_date,
                "title": title,
                "unit": next((p for p in preamble if "단위" in p), None),
                "rows": n,
                "columns": list(cols),
                "excerpt": bool(
                    EXCERPT.search(title)
                ),  # an excerpt copies another file: not independent
            }
            dcols = [c for c, ty in cols.items() if ty == "DATE"]
            if dcols:
                lo, hi = self.store.query(
                    f"SELECT min({quote_ident(dcols[0])}), max({quote_ident(dcols[0])}) FROM {t} WHERE _row_kind='data'"
                )[0]
                entry["date_column"], entry["date_range"] = dcols[0], f"{lo}~{hi}"
            elif "월" in cols:
                months = [
                    r[0]
                    for r in self.store.query(f"SELECT DISTINCT 월 FROM {t} WHERE _row_kind='data'")
                ]
                entry["month_values"] = sorted(months, key=lambda x: int(re.sub(r"\D", "", x) or 0))
            cands.append(entry)
            layouts.setdefault(tuple(cols), []).append(
                {
                    "table": t,
                    "file": name,
                    "sheet": sheet,
                    "data_date": data_date,
                    "_axis": axis_of(entry),
                }
            )
        # newest data first, so the current base file is the first candidate; excerpts go last
        cands.sort(key=lambda c: c["data_date"] or c["modified"], reverse=True)
        cands.sort(key=lambda c: c["excerpt"])
        cands = cands[:12]
        for c in cands:
            # Tables with the very same columns may be summed with also_tables - but only sheets of the
            # same workbook (e.g. warehouses) or files for DIFFERENT periods (monthly files). Anything
            # covering the same time (excerpts, other versions, other snapshot dates) would be counted twice.
            mine = axis_of(c)
            same = [
                x
                for x in layouts.get(tuple(c["columns"]), [])
                if x["table"] != c["table"]
                and (
                    x["file"] == c["file"]
                    or (mine and x["_axis"] and not axes_overlap(mine, x["_axis"]))
                )
            ]
            same.sort(key=lambda x: x["data_date"] or "", reverse=True)  # newest files first
            same.sort(
                key=lambda x: x["file"] != c["file"]
            )  # sheets of the same workbook come first
            if same:
                c["same_layout_tables"] = [
                    {k: v for k, v in x.items() if not k.startswith("_")} for x in same[:40]
                ]
        self.ctx.catalog_searches.append({"columns": want, "found": len(cands)})
        self.ctx.step(
            "search_catalog",
            f"후보 {len(cands)}개, 제외 {len(excluded)}개",
            "ok" if cands else "warn",
        )
        out = {
            "candidates": cands,
            "excluded": excluded[:10],
            "note": "excluded 는 구버전·사본이라 기준으로 쓰지 않음. same_layout_tables 가 있으면 run_query 의 "
            "also_tables 로 합쳐서 집계할 수 있음(예: 창고별 시트, 월별 파일).",
        }
        if not cands:  # help the model find the right column names instead of guessing again
            near.sort(key=lambda x: (-x["n"], x["data_date"] or ""), reverse=False)
            near.sort(key=lambda x: -x["n"])
            out["hint"] = (
                "요청한 컬럼을 모두 가진 표가 없습니다. 아래 가장 가까운 표(closest_tables)와 실제 컬럼명"
                "(known_columns)을 보고 컬럼을 바꿔 다시 검색하세요. 두 가지 컬럼 중 하나면 되면 'A|B'로 쓰세요."
            )
            out["closest_tables"] = [
                {k: x[k] for k in ("table", "file", "sheet", "dept", "has", "missing", "columns")}
                for x in near[:5]
            ]
            out["known_columns"] = [
                r[0]
                for r in self.store.query(
                    "SELECT c.name FROM columns c JOIN sheets s ON s.table_name=c.table_name"
                    " JOIN files f ON f.file_id=s.file_id WHERE f.fresh='ok'"
                    " GROUP BY c.name ORDER BY count(*) DESC LIMIT 50"
                )
            ]
        return out

    # ------------------------------------------------------------------ 3. search_value
    def search_value(self, text: str, exact: bool = False) -> dict:
        self.ctx.searched = True
        hits = self.store.search_value(text, exact=exact, limit=300)
        # a shorter text searched after the full name found nothing is a guess ("전자" for "가나다전자"):
        # its hits are similar names, not evidence for the thing that was asked about
        norm = normalize_value(text)
        fuzzy = any(
            v["found"] == 0
            and norm != normalize_value(v["text"])
            and norm in normalize_value(v["text"])
            for v in self.ctx.value_searches
        )
        groups: dict[tuple, dict] = {}
        for h in hits:
            meta = self.table_meta(h["table"])
            g = groups.setdefault(
                (h["file"], h["sheet"], h["column"]),
                {
                    "file": meta["file"],
                    "path": h["file"],
                    "sheet": h["sheet"],
                    "column": h["column"],
                    "letter": meta["letters"].get(h["column"], "?"),
                    "dept": meta["dept"],
                    "fresh": meta["fresh"],
                    "modified": meta["modified"].strftime("%m-%d"),
                    "hits": 0,
                    "first_rows": [],
                    "_table": h["table"],
                },
            )
            g["hits"] += 1
            if len(g["first_rows"]) < 3:
                g["first_rows"].append(h["row"])
        out = sorted(groups.values(), key=lambda g: (g["fresh"] != "ok", -g["hits"]))[:15]
        if fuzzy:
            for h in hits:
                if h["value"] not in self.ctx.similar_names and len(self.ctx.similar_names) < 8:
                    self.ctx.similar_names.append(h["value"])
        else:
            for g in out:  # local-only snippet for the UI (never sent to the LLM)
                self.file_audit(self.table_meta(g["_table"]), check_period=False)
                self.ctx.search_hits.append(
                    {**g, "snippet": self._snippet(g["_table"], g["first_rows"][0]), "exact": exact}
                )
        self.ctx.value_searches.append({"text": text, "found": len(out), "fuzzy": fuzzy})
        self.ctx.step(
            "search_value", f"'{text}' 위치 {len(out)}곳 ({len(hits)}건)", "ok" if out else "warn"
        )
        res = {
            "locations": [{k: v for k, v in g.items() if not k.startswith("_")} for g in out],
            "total_hits": len(hits),
        }
        if fuzzy:
            res["fuzzy"] = True
            res["similar_values"] = list(dict.fromkeys(h["value"] for h in hits))[:8]
            res["note"] = (
                "앞서 찾은 이름이 없어 일부만 일치시킨 결과입니다. 같은 대상이라고 단정하지 말고, "
                "원래 이름은 없고 비슷한 이름만 있다고 답하세요."
            )
        return res

    def _snippet(self, table: str, row_no: int) -> str:
        cols = list(self.table_meta(table)["columns"])
        r = self.store.query(f"SELECT * FROM {table} WHERE _row=? LIMIT 1", [row_no])
        if not r:
            return ""
        vals = r[0][3:]
        return (
            "… "
            + " · ".join(
                f"{c} {v}" for c, v in zip(cols, vals, strict=False) if v not in (None, "")
            )[:160]
            + " …"
        )

    # ------------------------------------------------------------------ 4. run_query
    def _coerce(self, dtype: str, value: str):
        v = value.strip()
        if dtype == "DATE":
            return date.fromisoformat(v)
        if dtype in ("BIGINT", "INTEGER"):
            return int(float(v)) if re.fullmatch(r"-?\d+(\.0+)?", v) else float(v)
        if dtype == "DOUBLE":
            return float(v)
        return v

    def _where(self, meta: dict, filters: list[dict]) -> tuple[str, list]:
        parts, params = ["_row_kind='data'"], []
        for f in filters or []:
            col, op, val = f.get("column"), f.get("op", "="), str(f.get("value", ""))
            if col not in meta["columns"]:
                raise ValueError(f"컬럼이 없습니다: {col} (가능: {', '.join(meta['columns'])})")
            if op not in OPS:
                raise ValueError(f"지원하지 않는 연산자: {op}")
            q, ty = quote_ident(col), meta["columns"][col]
            if other := f.get("value_column"):  # compare with another column of the same row
                if other not in meta["columns"]:
                    raise ValueError(
                        f"컬럼이 없습니다: {other} (가능: {', '.join(meta['columns'])})"
                    )
                if op not in ("=", "!=", ">", ">=", "<", "<="):
                    raise ValueError("열끼리 비교는 =, !=, >, >=, <, <= 만 쓸 수 있습니다.")
                if meta["columns"][other] != ty:
                    raise ValueError(
                        f"'{col}'({ty})와 '{other}'({meta['columns'][other]})는 형식이 달라 비교할 수 없습니다."
                    )
                parts.append(f"{q} {op} {quote_ident(other)}")
                continue
            if op == "in":
                items = [self._coerce(ty, x) for x in val.split(",") if x.strip()]
                parts.append(f"{q} IN ({','.join('?' * len(items))})")
                params += items
            elif op == "between":
                a, b = (x for x in val.split(",", 1))
                parts.append(f"{q} BETWEEN ? AND ?")
                params += [self._coerce(ty, a), self._coerce(ty, b)]
            elif op == "contains":
                parts.append(f"{q} LIKE ?")
                params.append(f"%{val}%")
            else:
                parts.append(f"{q} {op} ?")
                params.append(self._coerce(ty, val))
        return " AND ".join(parts), params

    def run_query(
        self,
        table: str,
        metrics: list[dict],
        group_by: list[str] | None = None,
        filters: list[dict] | None = None,
        order_desc: bool = True,
        limit: int | None = None,
        also_tables: list[str] | None = None,
    ) -> dict:
        if self.ctx.failures.get("run_query", 0) > config.MAX_QUERY_RETRIES:
            return {
                "error": f"쿼리 재시도 한도({config.MAX_QUERY_RETRIES}회)를 넘었습니다. 지금까지 얻은 근거로 답하세요."
            }
        try:
            res = self._run_query(
                table, metrics, group_by or [], filters or [], order_desc, limit, also_tables or []
            )
        except Exception as exc:  # validation or SQL errors -> the LLM may fix the spec and retry
            n = self.ctx.failures["run_query"] = self.ctx.failures.get("run_query", 0) + 1
            if n > config.MAX_QUERY_RETRIES:
                self.ctx.anomalies["retry_failed"] = True
            self.ctx.step("run_query", f"실패 {n}회: {str(exc)[:80]}", "warn")
            return {
                "error": str(exc)[:300],
                "retries_left": max(config.MAX_QUERY_RETRIES - n + 1, 0),
            }
        if self.ctx.failures.get("run_query"):
            self.ctx.anomalies["retry_ok"] = True
        return res

    def _run_query(self, table, metrics, group_by, filters, order_desc, limit, also_tables) -> dict:
        tables = list(dict.fromkeys([table, *also_tables]))
        if len(tables) > MAX_UNION_TABLES:
            raise ValueError(f"한 번에 합칠 수 있는 표는 {MAX_UNION_TABLES}개까지입니다.")
        metas = [self.table_meta(t) for t in tables]
        meta = metas[0]
        cols = meta["columns"]
        if not metrics:
            raise ValueError("metrics 가 비었습니다.")
        for g in group_by:
            if g not in cols:
                raise ValueError(f"group_by 컬럼이 없습니다: {g} (가능: {', '.join(cols)})")
            if g in ID_COLUMNS:
                raise ValueError(f"식별자 컬럼 '{g}' 로는 묶을 수 없습니다 (원본 행 노출 방지).")
        selects, specs = [], []
        for m in metrics:
            agg, col = m.get("agg", "sum"), m.get("column")
            if agg not in AGGS:
                raise ValueError(f"지원하지 않는 집계: {agg} (가능: {', '.join(AGGS)})")
            if agg != "count" and col not in cols:
                raise ValueError(f"컬럼이 없습니다: {col} (가능: {', '.join(cols)})")
            if agg in ("sum", "avg") and cols.get(col) not in NUMERIC_TYPES:
                raise ValueError(f"'{col}' 은 숫자 컬럼이 아니라 {agg} 를 쓸 수 없습니다.")
            q = quote_ident(col) if col else "*"
            expr = f"COUNT(DISTINCT {q})" if agg == "count_distinct" else f"{AGGS[agg]}({q})"
            selects.append(expr)
            money = glossary.is_money_column(col or "") and agg != "count"
            specs.append(
                {
                    "label": m.get("alias") or f"{agg}_{col or 'rows'}",
                    "agg": agg,
                    "column": col,
                    "money": money,
                }
            )
        used = list(dict.fromkeys([*group_by, *(s["column"] for s in specs if s["column"])]))
        # one (where, params, rows) per table
        parts = []
        for mt in metas:
            for c in used:
                if c not in mt["columns"]:
                    raise ValueError(
                        f"합치려는 표 {mt['table']}({mt['file']} [{mt['sheet']}])에 '{c}' 컬럼이 없습니다. "
                        "같은 컬럼 구조의 표끼리만 합칠 수 있습니다."
                    )
            where_i, params_i = self._where(mt, filters)
            rows_i = [
                r[0]
                for r in self.store.query(
                    f"SELECT _row FROM {mt['table']} WHERE {where_i} ORDER BY _row LIMIT 20000",
                    params_i,
                )
            ]
            parts.append((mt, where_i, params_i, rows_i))
        ranges = [self._effective_range(mt, filters) for mt in metas]
        stock_metric = any(c in STOCK_COLUMNS for c in (s["column"] for s in specs))
        for i in range(len(metas)):
            for j in range(i + 1, len(metas)):
                a, b = ranges[i], ranges[j]
                label = f"{metas[i]['file']} [{metas[i]['sheet']}] 와 {metas[j]['file']} [{metas[j]['sheet']}]"
                if a and b and axes_overlap(a, b):
                    raise ValueError(
                        f"기간이 겹치는 표를 합치면 같은 거래가 두 번 더해집니다: {label}"
                    )
                if (a is None or b is None) and metas[i]["path"] != metas[j]["path"]:
                    # no time axis: fine for flow numbers of different files (monthly ledgers),
                    # never for stock numbers (inventory, balances) or for files of the same date
                    if stock_metric or metas[i]["data_date"] == metas[j]["data_date"]:
                        raise ValueError(
                            f"서로 다른 파일의 현황표(재고·잔액 등)를 합치면 이중 집계됩니다: {label}. "
                            "같은 통합문서의 시트끼리만 합칠 수 있습니다."
                        )
        mult = meta["unit_mult"]
        if len(tables) == 1:
            from_sql, where, params = table, parts[0][1], parts[0][2]
        else:  # same-layout sheets / files summed together; money columns are put on one unit first
            branches, params = [], []
            money_cols = {s["column"] for s in specs if s["money"] and s["column"]}
            for mt, where_i, params_i, _ in parts:
                sel_cols = [
                    f"{quote_ident(c)} * {mt['unit_mult']} AS {quote_ident(c)}"
                    if c in money_cols and mt["columns"][c] in NUMERIC_TYPES
                    else quote_ident(c)
                    for c in used
                ] or ["1 AS _one"]
                branches.append(f"SELECT {', '.join(sel_cols)} FROM {mt['table']} WHERE {where_i}")
                params += params_i
            from_sql, where, mult = "(" + " UNION ALL ".join(branches) + ") u", "TRUE", 1
        sel = ", ".join([*(quote_ident(g) for g in group_by), *selects])
        grp = f" GROUP BY {', '.join(str(i + 1) for i in range(len(group_by)))}" if group_by else ""
        order_col = len(group_by) + 1
        order = f" ORDER BY {order_col} {'DESC' if order_desc else 'ASC'}" if group_by else ""
        n_limit = max(1, min(int(limit or 20), config.MAX_GROUP_ROWS))
        total_groups = self.store.query(
            f"SELECT count(*) FROM (SELECT 1 FROM {from_sql} WHERE {where}"
            f"{' GROUP BY ' + ', '.join(quote_ident(g) for g in group_by) if group_by else ''})",
            params,
        )[0][0]
        if total_groups > config.MAX_GROUP_ROWS and group_by and not limit:
            raise ValueError(
                f"결과가 {total_groups}개 그룹이라 너무 많습니다. 상위 N개만 보려면 limit(최대 "
                f"{config.MAX_GROUP_ROWS})를 지정하고, 아니면 필터를 더 걸거나 다른 컬럼으로 묶으세요."
            )
        raw = self.store.query(
            f"SELECT {sel} FROM {from_sql} WHERE {where}{grp}{order} LIMIT {n_limit}", params
        )
        n_rows = sum(len(p[3]) for p in parts)
        out_rows = []
        for r in raw:
            row = dict(zip(group_by, r[: len(group_by)], strict=True))
            for s, v in zip(specs, r[len(group_by) :], strict=True):
                if v is not None and s["money"]:
                    v = v * mult
                row[s["label"]] = (
                    None if v is None else (round(v, 2) if isinstance(v, float) else v)
                )
                row[s["label"] + "_display"] = fmt_won(v) if s["money"] else fmt_num(v)
                if s["agg"] == "sum" and v is not None and v < 0:
                    self.ctx.anomalies["negative"] = True
            out_rows.append(row)
        if group_by:
            for i, row in enumerate(out_rows, 1):
                row["rank"] = i if order_desc else None
        qid = f"q{len(self.ctx.queries) + 1}"
        if not out_rows or n_rows == 0:
            self.ctx.anomalies["empty"] = True
        before = len(self.ctx.findings)
        sums = [s["column"] for s in specs if s["agg"] == "sum" and s["column"]]
        for mt, where_i, params_i, rows_i in parts:
            self.file_audit(mt)
            self.row_audit(mt, where_i, params_i, used, sums, rows_i)
        eff = self._merge_ranges(ranges)
        if eff and not eff["filtered"] and self.ctx.period is None and eff["span_days"] > 40:
            self.ctx.flag(
                "M4",
                self.where_dict(meta),
                span=eff["text"],
            )
        raised = [f["title"] for f in self.ctx.findings[before:] if f["severity"] != rules.INFO]
        res = {
            "query_id": qid,
            "table": table,
            "file": meta["file"],
            "sheet": meta["sheet"],
            "file_status": meta["fresh"],
            "data_date": meta["data_date"],
            "unit": next((p for p in meta["preamble"] if "단위" in p), None),
            "rows_used": n_rows,
            "group_by": group_by,
            "metrics": [s["label"] for s in specs],
            "result": out_rows,
            # what the rules found in this data (the LLM may mention them; code shows them anyway)
            "data_warnings": raised[:6],
        }
        if group_by and total_groups > len(out_rows):
            res["groups_total"] = total_groups  # only the top rows are returned (limit)
            res["truncated"] = True
        if eff:
            res["period_covered"] = eff["text"]  # the real range these numbers come from
        if len(tables) > 1:
            res["tables_combined"] = [f"{m['file']} [{m['sheet']}]" for m in metas]
        self.ctx.queries[qid] = res | {
            "_group_by": group_by,
            "_metric": specs[0]["label"],
            "_metric_column": specs[0]["column"],
            "_money": specs[0]["money"],
            "_path": meta["path"],
            "_data_date": meta["data_date"],
            "_title": self._title(meta),
            "_family": catalog.family_stem(meta["file"]),
            "_dept": meta["dept"],
            "_range": eff,
        }
        for mt, _w, _p, rows_i in parts:
            if rows_i or len(tables) == 1:
                self.ctx.sources.append(self._source(mt, rows_i, used, qid, "계산 근거"))
        label = (
            f"{meta['file']} [{meta['sheet']}]"
            if len(tables) == 1
            else f"{len(tables)}개 표 합산({meta['file']} 외)"
        )
        self.ctx.step(
            "run_query",
            f"{label} {n_rows}행 사용 → {len(out_rows)}개 결과"
            + (f" · 조회 기간 {eff['text']}" if eff else ""),
            "warn" if self.ctx.anomalies.get("empty") else "ok",
        )
        return res

    # ---- which dates / months the numbers of a query really cover
    def _effective_range(self, meta: dict, filters: list[dict]) -> dict | None:
        """Date range (or months) a query used: the filters on the table's date / 월 column, and the
        table's own range for the side that is not filtered. None if the table has no time axis."""
        cols = meta["columns"]
        t = meta["table"]
        dcols = [c for c, ty in cols.items() if ty == "DATE"]
        fcols = {f.get("column") for f in filters}
        if dcols:
            dcol = next((c for c in dcols if c in fcols), dcols[0])
            lo, hi = self.store.query(
                f"SELECT min({quote_ident(dcol)}), max({quote_ident(dcol)}) FROM {t} WHERE _row_kind='data'"
            )[0]
            if lo is None or hi is None:
                return None
            start, end, filtered = lo, hi, dcol in fcols
            for f in filters:
                if f.get("column") != dcol:
                    continue
                op, val = f.get("op", "="), str(f.get("value", ""))
                try:
                    if op == "between":
                        a, b = (date.fromisoformat(x.strip()) for x in val.split(",", 1))
                        start, end = max(start, a), min(end, b)
                    elif op in (">=", ">"):
                        start = max(start, date.fromisoformat(val.strip()))
                    elif op in ("<=", "<"):
                        end = min(end, date.fromisoformat(val.strip()))
                    elif op == "=":
                        start = end = date.fromisoformat(val.strip())
                except ValueError:
                    continue
            return {
                "start": start,
                "end": end,
                "months": None,
                "filtered": filtered,
                "span_days": (end - start).days,
                "text": f"{start}~{end}",
            }
        if "월" in cols:
            present = {
                r[0]
                for r in self.store.query(
                    f"SELECT DISTINCT {quote_ident('월')} FROM {t} WHERE _row_kind='data'"
                )
            }
            months, filtered = set(present), "월" in fcols
            for f in filters:
                if f.get("column") == "월" and f.get("op") in ("in", "="):
                    months &= {x.strip() for x in str(f.get("value", "")).split(",")}
            if not months or not all(re.fullmatch(r"\d{1,2}월", str(m)) for m in months):
                return None
            order = sorted(months, key=lambda x: int(re.sub(r"\D", "", x)))
            return {
                "start": None,
                "end": None,
                "months": set(months),
                "filtered": filtered,
                "span_days": 31 * (len(months) - 1) + 1,
                "text": order[0] if len(order) == 1 else f"{order[0]}~{order[-1]}",
            }
        return None

    @staticmethod
    def _merge_ranges(ranges: list[dict | None]) -> dict | None:
        ranges = [r for r in ranges if r]
        if not ranges:
            return None
        if all(r["months"] is not None for r in ranges):
            months = set().union(*(r["months"] for r in ranges))
            order = sorted(months, key=lambda x: int(re.sub(r"\D", "", x)))
            return {
                "start": None,
                "end": None,
                "months": months,
                "filtered": all(r["filtered"] for r in ranges),
                "span_days": 31 * (len(months) - 1) + 1,
                "text": order[0] if len(order) == 1 else f"{order[0]}~{order[-1]}",
            }
        dated = [r for r in ranges if r["months"] is None]
        start, end = min(r["start"] for r in dated), max(r["end"] for r in dated)
        return {
            "start": start,
            "end": end,
            "months": None,
            "filtered": all(r["filtered"] for r in dated),
            "span_days": (end - start).days,
            "text": f"{start}~{end}",
        }

    def check_period_claim(self, texts: list[str]) -> None:
        """The answer says '3분기' but the numbers it quotes come from Jan-Sep? Compare the period
        named in the answer with the range of the queries whose figures the answer quotes."""
        text = " ".join(t for t in texts if t)
        cleaned = _DAY_DATE.sub(" ", text)  # '9월 30일', '2026-09-28' are days, not periods
        labels = {f"{n}분기" for n in re.findall(r"([1-4])\s*분기", cleaned)}
        labels |= {f"{n}월" for n in re.findall(r"(?<!\d)(1[0-2]|[1-9])\s*월", cleaned)}
        labels |= set(re.findall(r"상반기|하반기", cleaned))
        if len(labels) != 1:
            return  # no period named, or several: cannot tell which one the numbers belong to
        claimed = glossary.parse_period(cleaned, self.latest_year())
        if not claimed:
            return
        figures = {re.sub(r"\s+", "", m.group(0)) for m in _FIGURE.finditer(text)}
        if not figures:
            return
        cs, ce = date.fromisoformat(claimed["start"]), date.fromisoformat(claimed["end"])
        for q in self.ctx.queries.values():
            eff = q.get("_range")
            if not eff:
                continue
            shown = {
                str(v).replace(" ", "")
                for row in q["result"]
                for k, v in row.items()
                if k.endswith("_display")
            }
            if not (figures & shown):
                continue
            ok = (
                eff["months"] <= set(claimed["months"])
                if eff["months"] is not None
                else cs <= eff["start"] and eff["end"] <= ce
            )
            if not ok:
                self.ctx.flag(
                    "P3",
                    {"file": q["file"], "path": q["_path"], "sheet": q["sheet"]},
                    claimed=claimed["text"],
                    actual=eff["text"],
                )

    def _source(
        self, meta: dict, rows: list[int], cols: list[str], qid: str | None, role: str
    ) -> dict:
        """One evidence entry: which file, sheet, rows and columns, and how fresh the file is."""
        w = self.where_dict(meta, rows, cols)
        info = self.store.file_info.get(meta["path"], {})
        return {
            "file": meta["file"],
            "path": meta["path"],
            "sheet": meta["sheet"],
            "row": (w.get("rows", "") + "행") if rows else "-",
            "row_count": len(rows),
            "cells": w.get("cells", ""),
            "header_row": meta["header_row"],
            "modified": meta["modified"].strftime("%m-%d %H:%M"),
            "indexed": self.ctx.indexed_at,
            "fresh": meta["fresh"],
            "editing": info.get("locked"),
            "role": role,
            "query_id": qid,
        }

    # ------------------------------------------------------------------ 5. cross_verify
    def cross_verify(self, query_a: str, query_b: str) -> dict:
        a, b = self.ctx.queries.get(query_a), self.ctx.queries.get(query_b)
        if not a or not b:
            return {
                "error": f"query_id 를 확인하세요 (가능: {', '.join(self.ctx.queries) or '없음'})"
            }
        if a["table"] == b["table"]:
            return {
                "error": "같은 표를 다시 계산하는 것은 교차검증이 아닙니다. 다른 파일(다른 표)로 같은 지표를 계산하세요."
            }
        if a["_range"] is None and b["_range"] is None and a["_data_date"] != b["_data_date"]:
            return {
                "error": f"기준일이 다른 현황표({a['_data_date']} 대 {b['_data_date']})는 같은 시점의 값이 아니라 "
                "교차검증이 아닙니다. 독립된 두 번째 자료가 없으면 final_answer 의 no_cross_reason 에 이유를 적으세요."
            }
        ta, tb = (glossary.standard_terms_of(q.get("_metric_column")) for q in (a, b))
        related = bool(ta and tb and not (ta & tb) and any(p <= (ta | tb) for p in RELATED_METRICS))
        if ta and tb and not (ta & tb) and not related:
            return {
                "error": f"서로 다른 지표라 교차검증이 아닙니다: {a['_metric_column']}({'/'.join(ta)}) 대 "
                f"{b['_metric_column']}({'/'.join(tb)}). 같은 지표를 계산하는 다른 파일"
                "(예: 집계 파일의 매출실적 대 매출원장의 공급가액)로 비교하세요."
            }
        if a["_family"] == b["_family"] and a["_dept"] == b["_dept"]:
            return {
                "error": "같은 계열의 파일(버전·사본·서식만 다른 파일)끼리의 비교는 독립된 교차검증이 아닙니다. "
                "다른 부서·다른 종류의 파일(예: 집계 파일 대 거래 원장)로 계산하세요."
            }
        if EXCERPT.search(a["_title"]) or EXCERPT.search(b["_title"]):
            return {
                "error": "'발췌'·'복사'로 만든 파일은 원본을 옮겨 적은 것이라 독립된 검증이 아닙니다. 원본 자료와 비교하세요."
            }

        def keyed(q: dict) -> dict[str, float]:
            gb = q["_group_by"]
            return {
                " / ".join(str(r[g]) for g in gb) if gb else "(전체)": r[q["_metric"]]
                for r in q["result"]
            }

        ka, kb = keyed(a), keyed(b)
        common = [k for k in ka if k in kb]
        if not common:
            return {
                "error": "두 결과에 겹치는 항목이 없어 비교할 수 없습니다. group_by 를 맞추세요."
            }
        if self.ctx.asks_for_ranking and len(common) < MIN_RANK_ITEMS:
            self.ctx.rank_rejections += 1
            if (
                self.ctx.rank_rejections <= 2
            ):  # the model gets two chances to compare the whole ranking
                return {
                    "error": f"순위를 묻는 질문인데 겹치는 항목이 {len(common)}개뿐이라 순위를 검증할 수 없습니다. "
                    "한 항목만 필터링해 비교하지 말고, 두 자료를 같은 group_by 로 전체(또는 limit 5 이상 상위 항목)를 "
                    "집계해 다시 cross_verify 하세요."
                }
            self.ctx.flag("X12", {"file": f"{a['file']} ↔ {b['file']}"}, n=len(common))
        money = a["_money"] and b["_money"]
        rank_a = {k: i for i, k in enumerate(sorted(common, key=lambda k: -(ka[k] or 0)), 1)}
        rank_b = {k: i for i, k in enumerate(sorted(common, key=lambda k: -(kb[k] or 0)), 1)}
        diffs, match, max_rel = [], True, 0.0
        for k in common:
            va, vb = ka[k] or 0, kb[k] or 0
            rel = abs(va - vb) / max(abs(va), abs(vb), 1e-9)
            max_rel = max(max_rel, rel)
            same = rel <= config.CROSS_TOLERANCE
            match &= same
            diffs.append(
                {
                    "key": k,
                    "a": fmt_won(va) if money else fmt_num(va),
                    "b": fmt_won(vb) if money else fmt_num(vb),
                    "diff": fmt_won(vb - va),
                    "relative": f"{rel * 100:.1f}%",
                    "same": same,
                    "rank_a": rank_a[k],
                    "rank_b": rank_b[k],
                }
            )
        rank_changed = rank_a != rank_b
        only_a = [k for k in ka if k not in kb]
        only_b = [k for k in kb if k not in ka]
        sum_a, sum_b = sum(abs(ka[k] or 0) for k in common), sum(abs(kb[k] or 0) for k in common)
        ratio = (sum_a / sum_b) if sum_a and sum_b else 1.0
        unit_suspect = None
        if max_rel > 0.5:  # such a gap is usually a unit (천원/백만원) problem, not a data problem
            for k_pow in (3, 4, 6, 8):
                if any(abs(r / 10**k_pow - 1) < 0.05 for r in (ratio, 1 / ratio)):
                    unit_suspect = 10**k_pow
        dates = {a["_data_date"], b["_data_date"]}
        if related:
            self.ctx.flag(
                "X11",
                {"file": f"{a['file']} ↔ {b['file']}"},
                a=a["_metric_column"],
                b=b["_metric_column"],
            )
        self.ctx.cross = {
            "match": match,
            "rank_changed": rank_changed,
            "max_rel": max_rel,
            "date_gap": (
                f"{a['file']} {a['_data_date']} vs {b['file']} {b['_data_date']}"
                if len(dates) > 1
                else None
            ),
        }
        loc = {"file": f"{a['file']} ↔ {b['file']}"}
        if only_a or only_b:
            self.ctx.flag("X5", loc, keys=", ".join([*only_a, *only_b][:5]))
        if unit_suspect:
            self.ctx.flag("X6", loc, ratio=f"{unit_suspect:,}")
        for src in self.ctx.sources:  # label the evidence by role
            if src.get("query_id") == query_a:
                src["role"] = "계산 근거 (기준)"
            elif src.get("query_id") == query_b:
                src["role"] = "교차검증 대조"
        mismatches = [d for d in diffs if not d["same"]]
        self.ctx.step(
            "cross_verify",
            ("전부 일치" if match else f"불일치 {len(mismatches)}건")
            + (", 순위 변동" if rank_changed else ""),
            "ok" if match else "warn",
        )
        out = {
            "files": {"a": a["file"], "b": b["file"]},
            "data_dates": {"a": a["_data_date"], "b": b["_data_date"]},
            "match": match,
            "rank_changed": rank_changed,
            "tolerance": "1%",
            "items": diffs,
            "note": "rank_changed=true 이면 결론이 바뀌는 불일치입니다. 원인을 trace_difference 로 조사하세요.",
        }
        if only_a or only_b:
            out["only_in_a"], out["only_in_b"] = only_a[:10], only_b[:10]
        if unit_suspect:
            out["unit_suspect"] = f"약 {unit_suspect:,}배 차이: 단위(천원/백만원) 불일치 가능성"
        return out

    # ------------------------------------------------------------------ 6. trace_difference
    def trace_difference(
        self,
        table: str,
        date_column: str,
        amount_column: str,
        after_date: str,
        group_by: list[str] | None = None,
        filters: list[dict] | None = None,
    ) -> dict:
        try:
            meta = self.table_meta(table)
            for c in (date_column, amount_column, *(group_by or [])):
                if c not in meta["columns"]:
                    raise ValueError(f"컬럼이 없습니다: {c}")
            if meta["columns"][date_column] != "DATE":
                raise ValueError(f"'{date_column}' 은 날짜 컬럼이 아닙니다.")
            where, params = self._where(meta, filters or [])
            where += f" AND {quote_ident(date_column)} > ?"
            params.append(date.fromisoformat(after_date))
            gb = ", ".join(quote_ident(g) for g in (group_by or []))
            q = quote_ident(amount_column)
            rows = self.store.query(
                f"SELECT {gb + ', ' if gb else ''}count(*), sum({q}), min({quote_ident(date_column)}), max({quote_ident(date_column)})"
                f" FROM {table} WHERE {where}{' GROUP BY ' + gb if gb else ''} ORDER BY 2 LIMIT {config.MAX_GROUP_ROWS}",
                params,
            )
            ev_rows = [
                r[0]
                for r in self.store.query(
                    f"SELECT _row FROM {table} WHERE {where} ORDER BY _row LIMIT 5000", params
                )
            ]
            ev = (None, None, len(ev_rows))
        except Exception as exc:
            self.ctx.step("trace_difference", f"실패: {str(exc)[:80]}", "warn")
            return {"error": str(exc)[:300]}
        mult, k = meta["unit_mult"], len(group_by or [])
        groups = [
            dict(zip(group_by or [], r[:k], strict=True))
            | {
                "count": r[k],
                "sum": (r[k + 1] or 0) * mult,
                "sum_display": fmt_won((r[k + 1] or 0) * mult),
                "first_date": str(r[k + 2]),
                "last_date": str(r[k + 3]),
            }
            for r in rows
        ]
        total = sum(g["sum"] for g in groups)
        self.ctx.traced = True
        if ev[2]:
            self.ctx.sources.append(
                self._source(
                    meta,
                    ev_rows,
                    [date_column, amount_column, *(group_by or [])],
                    None,
                    "차이 추적 근거",
                )
            )
            self.file_audit(meta)
        self.ctx.step(
            "trace_difference", f"{after_date} 이후 {ev[2]}건, 합계 {fmt_won(total)}", "ok"
        )
        return {
            "file": meta["file"],
            "after_date": after_date,
            "total_count": ev[2],
            "total_display": fmt_won(total),
            "groups": groups,
            "note": "이 건들은 기준일 이후에 입력돼 이전 버전 집계 파일에 없을 수 있는 원인 후보입니다.",
        }

    # ------------------------------------------------------------------ 7. check_quality
    def quality_of(self, table: str) -> dict:
        meta = self.table_meta(table)
        cols = [c for c in meta["columns"]]
        q = ", ".join(quote_ident(c) for c in cols)
        nulls = {}
        for c in cols:
            if meta["columns"][c] == "VARCHAR" and c != "비고":
                n = self.store.query(
                    f"SELECT count(*) FROM {table} WHERE _row_kind='data' AND ({quote_ident(c)} IS NULL OR trim({quote_ident(c)})='')"
                )[0][0]
                if n:
                    nulls[c] = n
        dups = self.store.query(
            f"SELECT coalesce(sum(c-1),0) FROM (SELECT count(*) c FROM {table} WHERE _row_kind='data' GROUP BY {q} HAVING count(*)>1)"
        )[0][0]
        return {
            "file": meta["file"],
            "sheet": meta["sheet"],
            "file_status": meta["fresh"],
            "copy_of": meta["copy_of"],
            "data_date": meta["data_date"],
            "missing_values": nulls,
            "duplicate_rows": int(dups),
        }

    def check_quality(self, table: str) -> dict:
        try:
            qual = self.quality_of(table)
        except Exception as exc:
            return {"error": str(exc)[:300]}
        self.file_audit(self.table_meta(table))
        issues = [k for k in ("missing_values", "duplicate_rows") if qual[k]]
        self.ctx.step(
            "check_quality",
            f"{qual['file']}: " + (", ".join(issues) if issues else "문제 없음"),
            "warn" if issues else "ok",
        )
        return qual

    # ------------------------------------------------------------------ 8. ask_user
    def ask_user(self, question: str, options: list[str] | None = None) -> dict:
        self.ctx.ask = {"question": question, "options": list(options or [])}
        self.ctx.step("ask_user", question[:80], "warn")
        return {"ok": True}

    # ------------------------------------------------------------------ dispatch
    def execute(self, name: str, args: dict) -> dict:
        fn = getattr(self, name, None)
        if name not in TOOL_LABELS or name == "final_answer" or fn is None:
            return {"error": f"알 수 없는 도구: {name}"}
        try:
            return fn(**args)
        except TypeError as exc:
            return {"error": f"인자가 올바르지 않습니다: {exc}"}
