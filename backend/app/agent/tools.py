"""The agent's tools. All of them are local code over the DuckDB store.

The LLM only ever receives each tool's summary (aggregates, locations, counts) - never raw rows.
Per-question state lives in QuestionCtx; the confidence rules and the source list are built from it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from .. import config
from ..store import Store, quote_ident
from . import glossary

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
    "거래처코드",
    "품목코드",
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

    def step(self, tool: str, detail: str, status: str = "ok") -> None:
        self.trace.append({"step": TOOL_LABELS.get(tool, tool), "detail": detail, "status": status})


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


class ToolBox:
    def __init__(
        self, store: Store, ctx: QuestionCtx, glossary_extra: list[dict] | None = None
    ) -> None:
        self.store = store
        self.ctx = ctx
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
            " s.sheet_name, s.preamble FROM sheets s JOIN files f ON f.file_id=s.file_id"
            " WHERE s.table_name=?",
            [table],
        )
        if not rows:
            raise ValueError(f"해당 표가 없습니다: {table}")
        r = rows[0]
        preamble = json.loads(r[8] or "[]")
        cols = {
            c[0]: c[1]
            for c in self.store.query(
                "SELECT name, dtype FROM columns WHERE table_name=? ORDER BY ordinal", [table]
            )
        }
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

    def _flag_version(self, meta: dict) -> None:
        if meta["fresh"] == "stale":
            self.ctx.stale_used.add(meta["file"])
        elif meta["fresh"] == "copy":
            self.ctx.copy_used.add(meta["file"])

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
            mappings.append(m | {"input": t})
            self.ctx.term_grades.add(m["grade"])
        detail = ", ".join(
            f"{m['input']}→{'/'.join(m['columns'][:2]) or '?'}({m['grade']})" for m in mappings
        )
        if period:
            detail += f"{', ' if detail else ''}{period['text']}→{period['start']}~{period['end']}"
        self.ctx.step(
            "lookup_terms", detail or "-", "warn" if "미등록" in self.ctx.term_grades else "ok"
        )
        return {"mappings": mappings, "period": period, "latest_data_year": latest}

    # ------------------------------------------------------------------ 2. search_catalog
    def search_catalog(
        self, columns: list[str], year: int | None = None, dept: str | None = None
    ) -> dict:
        want = [c for c in (columns or []) if c]
        if not want:
            return {"error": "columns 를 지정하세요."}
        rows = self.store.query(
            "SELECT s.table_name, f.rel_path, f.name, f.dept, f.modified, f.fresh, f.copy_of, f.data_date,"
            " s.sheet_name, s.n_data_rows, s.preamble FROM sheets s JOIN files f ON f.file_id=s.file_id"
            " WHERE s.table_name IS NOT NULL AND f.status IN ('ok','partial') ORDER BY f.rel_path, s.sheet_index"
        )
        cands, excluded = [], []
        for t, path, name, d, modified, fresh, copy_of, data_date, sheet, n, pre in rows:
            if dept and d != dept:
                continue
            cols = {
                c[0]: c[1]
                for c in self.store.query("SELECT name, dtype FROM columns WHERE table_name=?", [t])
            }
            if not all(any(w == c or w in c for c in cols) for w in want):
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
        # newest data first, so the current base file is the first candidate
        cands.sort(key=lambda c: c["data_date"] or c["modified"], reverse=True)
        cands = cands[:12]
        self.ctx.step(
            "search_catalog",
            f"후보 {len(cands)}개, 제외 {len(excluded)}개",
            "ok" if cands else "warn",
        )
        return {
            "candidates": cands,
            "excluded": excluded[:10],
            "note": "excluded 는 구버전·사본이라 기준으로 쓰지 않음",
        }

    # ------------------------------------------------------------------ 3. search_value
    def search_value(self, text: str, exact: bool = False) -> dict:
        self.ctx.searched = True
        hits = self.store.search_value(text, exact=exact, limit=300)
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
        for g in out:  # local-only snippet for the UI (never sent to the LLM)
            self.ctx.search_hits.append(
                {**g, "snippet": self._snippet(g["_table"], g["first_rows"][0]), "exact": exact}
            )
        self.ctx.step(
            "search_value", f"'{text}' 위치 {len(out)}곳 ({len(hits)}건)", "ok" if out else "warn"
        )
        return {
            "locations": [{k: v for k, v in g.items() if not k.startswith("_")} for g in out],
            "total_hits": len(hits),
        }

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
        limit: int = 20,
    ) -> dict:
        if self.ctx.failures.get("run_query", 0) > config.MAX_QUERY_RETRIES:
            return {
                "error": f"쿼리 재시도 한도({config.MAX_QUERY_RETRIES}회)를 넘었습니다. 지금까지 얻은 근거로 답하세요."
            }
        try:
            res = self._run_query(table, metrics, group_by or [], filters or [], order_desc, limit)
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

    def _run_query(self, table, metrics, group_by, filters, order_desc, limit) -> dict:
        meta = self.table_meta(table)
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
            if agg in ("sum", "avg") and cols.get(col) not in (
                "BIGINT",
                "INTEGER",
                "DOUBLE",
                "DECIMAL",
            ):
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
        where, params = self._where(meta, filters)
        sel = ", ".join([*(quote_ident(g) for g in group_by), *selects])
        grp = f" GROUP BY {', '.join(str(i + 1) for i in range(len(group_by)))}" if group_by else ""
        order_col = len(group_by) + 1
        order = f" ORDER BY {order_col} {'DESC' if order_desc else 'ASC'}" if group_by else ""
        n_limit = max(1, min(int(limit or 20), config.MAX_GROUP_ROWS))
        total_groups = self.store.query(
            f"SELECT count(*) FROM (SELECT 1 FROM {table} WHERE {where}{' GROUP BY ' + ', '.join(quote_ident(g) for g in group_by) if group_by else ''})",
            params,
        )[0][0]
        if total_groups > config.MAX_GROUP_ROWS and group_by:
            raise ValueError(
                f"결과가 {total_groups}개 그룹이라 너무 많습니다. 필터를 더 걸거나 다른 컬럼으로 묶으세요."
            )
        raw = self.store.query(
            f"SELECT {sel}{(' FROM ' + table)} WHERE {where}{grp}{order} LIMIT {n_limit}", params
        )
        src = self.store.query(
            f"SELECT min(_row), max(_row), count(*) FROM {table} WHERE {where}", params
        )[0]
        mult = meta["unit_mult"]
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
        if not out_rows or src[2] == 0:
            self.ctx.anomalies["empty"] = True
        self._flag_version(meta)
        res = {
            "query_id": qid,
            "table": table,
            "file": meta["file"],
            "sheet": meta["sheet"],
            "file_status": meta["fresh"],
            "data_date": meta["data_date"],
            "unit": next((p for p in meta["preamble"] if "단위" in p), None),
            "rows_used": src[2],
            "group_by": group_by,
            "metrics": [s["label"] for s in specs],
            "result": out_rows,
        }
        self.ctx.queries[qid] = res | {
            "_group_by": group_by,
            "_metric": specs[0]["label"],
            "_money": specs[0]["money"],
        }
        first, last = src[0], src[1]
        self.ctx.sources.append(
            {
                "file": meta["file"],
                "sheet": meta["sheet"],
                "row": f"{first}~{last}행" if first != last else f"{first}행",
                "modified": meta["modified"].strftime("%m-%d %H:%M"),
                "indexed": self.ctx.indexed_at,
                "path": meta["path"],
            }
        )
        self.ctx.step(
            "run_query",
            f"{meta['file']} [{meta['sheet']}] {src[2]}행 사용 → {len(out_rows)}개 결과",
            "warn" if self.ctx.anomalies.get("empty") else "ok",
        )
        return res

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
        money = a["_money"] and b["_money"]
        rank_a = {k: i for i, k in enumerate(sorted(common, key=lambda k: -(ka[k] or 0)), 1)}
        rank_b = {k: i for i, k in enumerate(sorted(common, key=lambda k: -(kb[k] or 0)), 1)}
        diffs, match = [], True
        for k in common:
            va, vb = ka[k] or 0, kb[k] or 0
            rel = abs(va - vb) / max(abs(va), abs(vb), 1e-9)
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
        self.ctx.cross = {"match": match, "rank_changed": rank_changed}
        mismatches = [d for d in diffs if not d["same"]]
        self.ctx.step(
            "cross_verify",
            ("전부 일치" if match else f"불일치 {len(mismatches)}건")
            + (", 순위 변동" if rank_changed else ""),
            "ok" if match else "warn",
        )
        return {
            "files": {"a": a["file"], "b": b["file"]},
            "match": match,
            "rank_changed": rank_changed,
            "tolerance": "1%",
            "items": diffs,
            "note": "rank_changed=true 이면 결론이 바뀌는 불일치입니다. 원인을 trace_difference 로 조사하세요.",
        }

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
            ev = self.store.query(
                f"SELECT min(_row), max(_row), count(*) FROM {table} WHERE {where}", params
            )[0]
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
                {
                    "file": meta["file"],
                    "sheet": meta["sheet"],
                    "row": f"{ev[0]}~{ev[1]}행",
                    "modified": meta["modified"].strftime("%m-%d %H:%M"),
                    "indexed": self.ctx.indexed_at,
                    "path": meta["path"],
                }
            )
            self._flag_version(meta)
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
        self.note_quality(qual)
        issues = [k for k in ("missing_values", "duplicate_rows") if qual[k]]
        self.ctx.step(
            "check_quality",
            f"{qual['file']}: " + (", ".join(issues) if issues else "문제 없음"),
            "warn" if issues else "ok",
        )
        return qual

    def note_quality(self, qual: dict) -> None:
        if qual["missing_values"]:
            self.ctx.quality_notes.append(
                f"{qual['file']} 결측 {sum(qual['missing_values'].values())}건 (제외 기록)"
            )
        if qual["duplicate_rows"]:
            self.ctx.quality_notes.append(f"{qual['file']} 완전중복 행 {qual['duplicate_rows']}개")

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
