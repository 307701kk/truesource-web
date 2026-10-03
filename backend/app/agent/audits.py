"""Rule triggers: look at the file / sheet / rows a query used and raise confidence rules.

File level  (once per file):  version, copy, same-date conflict, folder, name, lock, changed on disk,
                              other year, hidden sheet, formula / error / truncation notes.
Row level   (per query):      empty cells in used columns, duplicate rows, total row that does not
                              match the data, mixed text/number columns, VAT column, instruction-like
                              cells, period not covered by the table.
Everything is local code. Findings carry where the proof is (file / sheet / rows / columns).
"""

from __future__ import annotations

import re
from datetime import date

from ..opener import resolve_catalog_path
from ..store import quote_ident
from .gateway import _INJECTION

INFORMAL_DIR = re.compile(
    r"백업|개인|새 폴더|임시|정리중|받은자료|보관|archive|backup|\bold\b|\btemp\b|\btmp\b", re.I
)
INFORMAL_NAME = re.compile(
    r"임시|초안|발췌|테스트|샘플|작업중|참고용|(?<![a-z])(draft|temp|sample|test)(?![a-z])", re.I
)
YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
EXCERPT = re.compile(r"발췌|복사|인용|재편집")
NUMERIC = ("BIGINT", "INTEGER", "DOUBLE", "DECIMAL")
BIG_GAP_DAYS = 31
EDGE_GAP_DAYS = 7
LATE_EDIT_DAYS = 14


def compress_rows(rows: list[int], limit: int = 6) -> str:
    """[6,7,8,30,31] -> '6-8, 30-31' (long lists are shortened)."""
    if not rows:
        return ""
    rows = sorted(set(rows))
    parts, start, prev = [], rows[0], rows[0]
    for r in [*rows[1:], None]:
        if r is not None and r == prev + 1:
            prev = r
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if r is not None:
            start = prev = r
    if len(parts) > limit:
        return ", ".join(parts[:limit]) + f" 외 {len(parts) - limit}곳"
    return ", ".join(parts)


class AuditMixin:
    """Mixed into ToolBox (uses self.store, self.ctx, self.root, self.table_meta, self._where)."""

    # ------------------------------------------------------------------ evidence location
    def where_dict(
        self, meta: dict, rows: list[int] | None = None, cols: list[str] | None = None
    ) -> dict:
        d = {"file": meta["file"], "path": meta["path"], "sheet": meta["sheet"]}
        if rows is not None:
            d["rows"] = compress_rows(rows)
            d["row_count"] = len(rows)
        if cols:
            letters = meta["letters"]
            d["cells"] = " · ".join(f"{letters.get(c, '?')}열({c})" for c in dict.fromkeys(cols))
        return d

    # ------------------------------------------------------------------ file level
    def file_audit(self, meta: dict) -> None:
        ctx = self.ctx
        fl = ctx.flag
        key = meta["table"]
        info = self.store.file_info.get(meta["path"], {})
        loc = {"file": meta["file"], "path": meta["path"], "sheet": meta["sheet"]}
        if meta["path"] not in ctx.audited_files:
            ctx.audited_files.add(meta["path"])
            title = meta["preamble"][0] if meta["preamble"] else ""
            if meta["fresh"] == "stale":
                newer = (info.get("newer") or "").rsplit("/", 1)[-1] or "(확인 필요)"
                fl("V1", loc, file=meta["file"], newer=newer)
            elif meta["fresh"] == "copy":
                fl("V2", loc, file=meta["file"], orig=meta["copy_of"])
            if meta["fresh"] == "ok" and info.get("tie_with"):
                others = ", ".join(p.rsplit("/", 1)[-1] for p in info["tie_with"])
                same, units = self._same_data(meta["path"], info["tie_with"][0])
                if same:
                    fl("V3F", loc, others=others, units=units)
                else:
                    fl("V3", loc, date=meta["data_date"], others=others)
            for part in meta["path"].split("/")[:-1]:
                if INFORMAL_DIR.search(part):
                    fl("V5", loc, folder=part)
                    break
            if m := INFORMAL_NAME.search(f"{meta['file'].rsplit('.', 1)[0]} {title}"):
                fl("V6", loc, marker=m.group(0))
            if lock := info.get("locked"):
                since = (lock.get("since") or "").replace("T", " ")[5:]
                known = lock.get("owner")
                fl(
                    "V7",
                    loc,
                    who=f"{known}님이" if known else "다른 사용자가",
                    owner=f"{known}님" if known else "파일을 열어 둔 사람",
                    since_text=f" ({since}부터)" if since else "",
                )
            if info.get("uncached"):
                fl("D6", loc, n=info["uncached"])
            self._disk_check(meta, loc)
            try:
                gap = (meta["modified"].date() - date.fromisoformat(meta["data_date"])).days
            except (TypeError, ValueError):
                gap = 0
            if gap > LATE_EDIT_DAYS:
                fl("V4", loc, data_date=meta["data_date"], gap=gap)
            period = ctx.period
            if period:
                years = set(YEAR.findall(f"{title} {meta['file']} {meta['path']}"))
                if years and str(period["year"]) not in years:
                    fl("V10", loc, year=period["year"], years=", ".join(sorted(years)))
        if key not in ctx.audited_tables:
            ctx.audited_tables.add(key)
            if meta["hidden"]:
                fl("V11", loc, sheet=meta["sheet"])
            if meta["error_cells"]:
                fl("D5", loc, n=meta["error_cells"])
            if meta["truncated"]:
                from .. import config

                fl("D7", loc, limit=config.MAX_ROWS_PER_SHEET)

    def _disk_check(self, meta: dict, loc: dict) -> None:
        if not getattr(self, "root", None):
            return
        real = resolve_catalog_path(self.root, meta["path"])
        if real is None:
            self.ctx.flag("V9", loc)
            return
        st = real.stat()
        if abs(st.st_mtime - meta["modified"].timestamp()) > 2 or st.st_size != meta["size"]:
            self.ctx.flag("V8", loc)

    def _numeric_totals(self, path: str) -> dict[str, dict[str, float]]:
        """{sheet: {numeric column: sum}} of a file, scaled by the sheet's unit (천원, 백만원 ...).
        Ratio columns (%, 률) are left as they are."""
        out: dict[str, dict[str, float]] = {}
        for (table,) in self.store.query(
            "SELECT s.table_name FROM sheets s JOIN files f ON f.file_id=s.file_id"
            " WHERE f.rel_path=? AND s.table_name IS NOT NULL",
            [path],
        ):
            m = self.table_meta(table)
            cols = {}
            for c, ty in m["columns"].items():
                if ty in NUMERIC:
                    v = self.store.query(
                        f"SELECT sum({quote_ident(c)}) FROM {table} WHERE _row_kind='data'"
                    )[0][0]
                    ratio = "%" in c or "률" in c
                    cols[c] = float(v or 0) * (1 if ratio else m["unit_mult"])
            out[m["sheet"]] = cols
        return out

    def _same_data(self, path_a: str, path_b: str) -> tuple[bool, str]:
        """True if two files hold the same numbers once units are normalised (format-only copy)."""
        a, b = self._numeric_totals(path_a), self._numeric_totals(path_b)
        units = []
        for p in (path_a, path_b):
            for (pre,) in self.store.query(
                "SELECT s.preamble FROM sheets s JOIN files f ON f.file_id=s.file_id WHERE f.rel_path=? LIMIT 1",
                [p],
            ):
                m = re.search(r"단위\s*:\s*([^,)\s\"]+)", pre or "")
                units.append(m.group(1) if m else "표기 없음")
        compared = 0
        for sheet, cols in a.items():
            for c, v in cols.items():
                w = b.get(sheet, {}).get(c)
                if w is None:
                    continue
                compared += 1
                if abs(v - w) > 0.005 * max(abs(v), abs(w), 1):
                    return False, " / ".join(units)
        return compared > 0, " / ".join(units)

    # ------------------------------------------------------------------ row level
    def row_audit(
        self,
        meta: dict,
        where: str,
        params: list,
        used: list[str],
        sum_cols: list[str],
        rows: list[int],
    ) -> None:
        from . import glossary
        from .tools import fmt_num, fmt_won

        fl, t, q = self.ctx.flag, meta["table"], quote_ident
        total = len(rows)
        if used and total:  # D1: empty cells in the columns the answer is built from
            cond = " OR ".join(f"{q(c)} IS NULL" for c in used)
            n = self.store.query(f"SELECT count(*) FROM {t} WHERE {where} AND ({cond})", params)[0][
                0
            ]
            if n:
                sample = [
                    r[0]
                    for r in self.store.query(
                        f"SELECT _row FROM {t} WHERE {where} AND ({cond}) ORDER BY _row LIMIT 500",
                        params,
                    )
                ]
                w = self.where_dict(meta, sample, used)
                fl(
                    "D1H" if n / total >= 0.05 else "D1",
                    w,
                    rows=n,
                    pct=f"{n / total * 100:.1f}%",
                    cells=_cells(w),
                )
        if total > 1:  # D2: identical rows
            allc = ", ".join(q(c) for c in meta["columns"])
            m = next((c for c in sum_cols if c in meta["columns"]), None)
            val = f"any_value({q(m)})" if m else "0"
            dups = self.store.query(
                f"SELECT count(*), {val}, min(_row) FROM {t} WHERE {where} GROUP BY {allc}"
                " HAVING count(*) > 1 ORDER BY min(_row) LIMIT 200",
                params,
            )
            if dups:
                n = sum(c - 1 for c, _, _ in dups)
                impact = sum((v or 0) * (c - 1) for c, v, _ in dups)
                tot = (
                    self.store.query(f"SELECT sum({q(m)}) FROM {t} WHERE {where}", params)[0][0]
                    if m
                    else 0
                )
                share = abs(impact) / abs(tot) if tot else 0
                w = self.where_dict(meta, [r for _, _, r in dups])
                fl(
                    "D2H" if share >= 0.01 else "D2",
                    w,
                    rows=n,
                    pct=f"{share * 100:.1f}%",
                    cells=_cells(w),
                )
        for m in sum_cols:  # D3: the file's own total row vs the data
            if m not in meta["columns"] or meta["columns"][m] not in NUMERIC:
                continue
            totals = self.store.query(
                f"SELECT _row, {q(m)} FROM {t} WHERE _row_kind='total' AND {q(m)} IS NOT NULL ORDER BY _row"
            )
            if not totals:
                continue
            actual = (
                self.store.query(f"SELECT sum({q(m)}) FROM {t} WHERE _row_kind='data'")[0][0] or 0
            )
            if not any(abs(v - actual) <= 0.01 * max(abs(actual), 1) for _, v in totals):
                mult = meta["unit_mult"] if glossary.is_money_column(m) else 1
                show = fmt_won if glossary.is_money_column(m) else fmt_num
                w = self.where_dict(meta, [totals[0][0]], [m])
                fl(
                    "D3",
                    w,
                    total=show(totals[0][1] * mult),
                    actual=show(actual * mult),
                    cells=_cells(w),
                )
            break
        if mixed := [c for c in used if c in meta["mixed"]]:  # D4
            fl("D4", self.where_dict(meta, None, mixed), columns=", ".join(mixed))
        if "합계" in sum_cols and "공급가액" in meta["columns"]:  # D8: VAT-included column
            fl("D8", self.where_dict(meta, None, ["합계"]), column="합계")
        self._injection_cells(meta)  # D9
        self._period_coverage(meta)  # P1 / P2

    def _injection_cells(self, meta: dict) -> None:
        """Cells that read like instructions to an AI ("이전 지시를 무시..."). They are plain data
        here (the LLM never sees cell text), but a person put them there on purpose: report them."""
        if meta["table"] in self.ctx.audited_inject:
            return
        self.ctx.audited_inject.add(meta["table"])
        for col, ty in meta["columns"].items():
            if ty != "VARCHAR":
                continue
            rows = self.store.query(
                f"SELECT _row, {quote_ident(col)} FROM {meta['table']}"
                f" WHERE {quote_ident(col)} IS NOT NULL AND length({quote_ident(col)}) > 8 LIMIT 20000"
            )
            for row_no, text in rows:
                if _INJECTION.search(text):
                    letter = meta["letters"].get(col, "?")
                    w = self.where_dict(meta, [row_no], [col])
                    self.ctx.flag("D9", w, cell=f"{letter}{row_no}")

    def _period_coverage(self, meta: dict) -> None:
        period = self.ctx.period
        if not period:
            return
        t, start, end = (
            meta["table"],
            date.fromisoformat(period["start"]),
            date.fromisoformat(period["end"]),
        )
        dcols = [c for c, ty in meta["columns"].items() if ty == "DATE"]
        loc = {"file": meta["file"], "path": meta["path"], "sheet": meta["sheet"]}
        if dcols:
            lo, hi = self.store.query(
                f"SELECT min({quote_ident(dcols[0])}), max({quote_ident(dcols[0])}) FROM {t} WHERE _row_kind='data'"
            )[0]
            if lo is None or hi is None:
                return
            gap = max((end - hi).days, (lo - start).days)
            have, want = f"{lo}~{hi}", f"{start}~{end}"
            if gap > BIG_GAP_DAYS:
                self.ctx.flag("P1", loc, have=have, want=want)
            elif gap > EDGE_GAP_DAYS:
                self.ctx.flag("P1M", loc, have=have, want=want, gap=gap)
        elif "월" in meta["columns"]:
            present = {
                r[0]
                for r in self.store.query(
                    f"SELECT DISTINCT {quote_ident('월')} FROM {t} WHERE _row_kind='data'"
                )
            }
            missing = [m for m in period["months"] if m not in present]
            if missing and any(re.fullmatch(r"\d{1,2}월", str(p)) for p in present):
                self.ctx.flag(
                    "P1", loc, have=", ".join(sorted(present)), want=", ".join(period["months"])
                )
            elif meta["data_date"] and date.fromisoformat(meta["data_date"]) < end:
                self.ctx.flag("P2", loc, data_date=meta["data_date"], end=end.isoformat())


def _cells(w: dict) -> str:
    return " ".join(
        x
        for x in (f"[{w['sheet']}]", w.get("rows", "") and f"{w['rows']}행", w.get("cells", ""))
        if x
    )
