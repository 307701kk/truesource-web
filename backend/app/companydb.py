"""Per-company storage (SQLite, one file per company under data/).

Holds what must survive a restart:  users, question history, the company's own glossary,
file fingerprints of the last scan and a sync log ("추가 3, 수정 1, 삭제 0").
The scanned workbook data itself stays in the in-memory DuckDB store (rebuilt by a scan).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, dept TEXT, title TEXT,
    created_at TEXT, last_seen TEXT, UNIQUE(name, dept, title));
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY, user_name TEXT, session_id TEXT, question TEXT NOT NULL,
    type TEXT, answer TEXT, confidence TEXT, response TEXT NOT NULL, created_at TEXT);
CREATE TABLE IF NOT EXISTS glossary (
    id INTEGER PRIMARY KEY, term TEXT NOT NULL UNIQUE, synonyms TEXT, columns TEXT,
    note TEXT, created_by TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS file_fp (
    rel_path TEXT PRIMARY KEY, size INTEGER, modified TEXT, sha256 TEXT);
CREATE TABLE IF NOT EXISTS scan_log (
    id INTEGER PRIMARY KEY, path TEXT, finished_at TEXT, files INTEGER, sheets INTEGER,
    data_rows INTEGER, added INTEGER, modified INTEGER, deleted INTEGER, moved INTEGER,
    detail TEXT);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


_NOT_A_TERM = re.compile(r"^[\d\W_]+$|^\d+\s*(월|분기|주|일|년)$")


def suggestable(column: str) -> bool:
    """Can this column name be offered as a glossary candidate? (not internal, not a bare number/month)"""
    c = column.strip()
    return 2 <= len(c) <= 30 and not c.startswith("_") and not _NOT_A_TERM.match(c)


def normalize_name(name: str) -> str:
    return unicodedata.normalize("NFC", (name or "").strip())


def diff_fingerprints(prev: dict[str, tuple], new: dict[str, tuple]) -> dict:
    """Compare two {rel_path: (size, modified, sha256)} maps the way the design table says:
    add = new path; modify = same path, different hash; delete = gone path;
    move/rename = delete + add with the same hash (kept as one 'moved' item, memory is kept)."""
    added = [p for p in new if p not in prev]
    deleted = [p for p in prev if p not in new]
    modified = [p for p in new if p in prev and new[p][2] != prev[p][2]]
    moved = []
    for d in list(deleted):
        match = next((a for a in added if new[a][2] and new[a][2] == prev[d][2]), None)
        if match:
            moved.append({"from": d, "to": match})
            deleted.remove(d)
            added.remove(match)
    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "moved": moved,
    }


class CompanyDB:
    def __init__(self, path: Path, name: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.name = name
        self._lock = threading.RLock()
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self.journal_mode = self._pick_journal_mode(self._con)
        with self._lock:
            self._con.executescript(_DDL)
            if self.get_meta("name") is None:
                self.set_meta("name", name)
                self.set_meta("created_at", _now())

    @staticmethod
    def _pick_journal_mode(con: sqlite3.Connection) -> str:
        """SQLite's default rollback journal creates and deletes a file on every write. Some drives
        (e.g. an exFAT external disk) fail that with "attempt to write a readonly database" from the
        second write on. WAL (and, failing that, an in-memory journal) never does."""
        for mode in ("WAL", "MEMORY"):
            try:
                got = con.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]
            except sqlite3.DatabaseError:
                continue
            if str(got).upper() == mode:
                return mode.lower()
        return "delete"

    def _run(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._con.execute(sql, params)
            self._con.commit()
            return cur

    def _all(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._con.execute(sql, params).fetchall()]

    # ---------------------------------------------------------------- meta / users
    def get_meta(self, key: str) -> str | None:
        rows = self._all("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    def set_meta(self, key: str, value: str) -> None:
        self._run(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def upsert_user(self, name: str, dept: str, title: str) -> dict:
        name, dept, title = normalize_name(name), normalize_name(dept), normalize_name(title)
        self._run(
            "INSERT INTO users(name,dept,title,created_at,last_seen) VALUES(?,?,?,?,?)"
            " ON CONFLICT(name,dept,title) DO UPDATE SET last_seen=excluded.last_seen",
            (name, dept, title, _now(), _now()),
        )
        return self._all(
            "SELECT * FROM users WHERE name=? AND dept=? AND title=?", (name, dept, title)
        )[0]

    # ---------------------------------------------------------------- question history
    def add_question(self, user_name: str, session_id: str | None, response: dict) -> int:
        cur = self._run(
            "INSERT INTO questions(user_name,session_id,question,type,answer,confidence,response,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                user_name,
                session_id,
                response.get("question", ""),
                response.get("type"),
                response.get("answer"),
                response.get("confidence"),
                json.dumps(response, ensure_ascii=False),
                _now(),
            ),
        )
        return cur.lastrowid

    def recent_questions(self, user_name: str | None = None, limit: int = 50) -> list[dict]:
        where, params = ("WHERE user_name=?", [user_name]) if user_name else ("", [])
        return self._all(
            "SELECT id,user_name,session_id,question,type,answer,confidence,created_at FROM questions "
            f"{where} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        )

    def get_question(self, qid: int) -> dict | None:
        rows = self._all("SELECT * FROM questions WHERE id=?", (qid,))
        if not rows:
            return None
        r = rows[0]
        return {
            **{k: r[k] for k in ("id", "user_name", "created_at")},
            "response": json.loads(r["response"]),
        }

    def delete_question(self, qid: int) -> bool:
        return self._run("DELETE FROM questions WHERE id=?", (qid,)).rowcount > 0

    # ---------------------------------------------------------------- company glossary
    def glossary_list(self) -> list[dict]:
        out = []
        for r in self._all("SELECT * FROM glossary ORDER BY term"):
            out.append(
                {
                    "id": r["id"],
                    "term": r["term"],
                    "synonyms": json.loads(r["synonyms"] or "[]"),
                    "columns": json.loads(r["columns"] or "[]"),
                    "note": r["note"] or "",
                    "created_by": r["created_by"],
                    "created_at": r["created_at"],
                }
            )
        return out

    def glossary_save(
        self, term: str, synonyms: list[str], columns: list[str], note: str, by: str
    ) -> dict:
        self._run(
            "INSERT INTO glossary(term,synonyms,columns,note,created_by,created_at) VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(term) DO UPDATE SET synonyms=excluded.synonyms, columns=excluded.columns,"
            " note=excluded.note",
            (
                term,
                json.dumps(synonyms, ensure_ascii=False),
                json.dumps(columns, ensure_ascii=False),
                note,
                by,
                _now(),
            ),
        )
        return next(g for g in self.glossary_list() if g["term"] == term)

    def glossary_delete(self, gid: int) -> bool:
        return self._run("DELETE FROM glossary WHERE id=?", (gid,)).rowcount > 0

    # ---------------------------------------------------------------- scans
    def fingerprints(self) -> dict[str, tuple]:
        return {
            r["rel_path"]: (r["size"], r["modified"], r["sha256"])
            for r in self._all("SELECT * FROM file_fp")
        }

    def record_scan(
        self,
        path: str,
        new_fp: dict[str, tuple],
        sheets: int,
        data_rows: int,
        columns: dict[str, list[str]] | None = None,
    ) -> dict:
        """Store the new fingerprints and a sync-log row; returns the diff against the previous scan,
        the data `version` (bumped only when something changed) and the columns never seen before."""
        first = self.get_meta("last_folder") != path or not self.fingerprints()
        diff = diff_fingerprints({} if first else self.fingerprints(), new_fp)
        changed = first or any(diff.values())
        with self._lock:
            self._con.execute("DELETE FROM file_fp")
            self._con.executemany(
                "INSERT INTO file_fp VALUES(?,?,?,?)", [(p, *v) for p, v in new_fp.items()]
            )
            self._con.commit()
        self.set_meta("last_folder", path)
        version = int(self.get_meta("data_version") or 0) + (1 if changed else 0)
        version = max(version, 1)
        self.set_meta("data_version", str(version))
        at = _now()
        self._run(
            "INSERT INTO scan_log(path,finished_at,files,sheets,data_rows,added,modified,deleted,moved,detail)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                path,
                at,
                len(new_fp),
                sheets,
                data_rows,
                len(diff["added"]),
                len(diff["modified"]),
                len(diff["deleted"]),
                len(diff["moved"]),
                json.dumps(diff, ensure_ascii=False),
            ),
        )
        return {
            "first_scan": first,
            **{k: len(v) for k, v in diff.items()},
            "detail": diff,
            "version": version,
            "changed": changed,
            "at": at,
            "new_columns": self._track_columns(columns, first),
        }

    # ---------------------------------------------------------------- new columns -> glossary candidates
    def _meta_json(self, key: str, default):
        raw = self.get_meta(key)
        return json.loads(raw) if raw else default

    def _track_columns(self, columns: dict[str, list[str]] | None, first: bool) -> list[str]:
        """Remember every column name seen; the ones never seen before (after the first scan of a
        folder) become glossary candidates."""
        if columns is None:
            return []
        known = self._meta_json("known_columns", None)
        cands = self._meta_json("col_candidates", {})
        new: list[str] = []
        if known is not None and not first:
            for name, files in columns.items():
                if name not in known and suggestable(name):
                    new.append(name)
                    cands[name] = {"files": sorted(files)[:5], "first_seen": _now()}
        self.set_meta(
            "known_columns", json.dumps(sorted({*(known or []), *columns}), ensure_ascii=False)
        )
        self.set_meta("col_candidates", json.dumps(cands, ensure_ascii=False))
        return sorted(new)

    def glossary_candidates(self, covered: set[str], present: set[str]) -> list[dict]:
        """New columns still worth a glossary entry: not dismissed, not already covered by a glossary
        entry, and still present in the loaded files."""
        dismissed = set(self._meta_json("col_dismissed", []))
        out = []
        for name, info in self._meta_json("col_candidates", {}).items():
            if name in dismissed or name.casefold() in covered or name not in present:
                continue
            out.append({"column": name, **info})
        return sorted(out, key=lambda c: c["first_seen"], reverse=True)

    def dismiss_candidate(self, column: str) -> None:
        dismissed = self._meta_json("col_dismissed", [])
        if column not in dismissed:
            self.set_meta("col_dismissed", json.dumps([*dismissed, column], ensure_ascii=False))

    def scan_logs(self, limit: int = 20) -> list[dict]:
        rows = self._all("SELECT * FROM scan_log ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["detail"] = json.loads(r["detail"] or "{}")
        return rows

    def close(self) -> None:
        with self._lock:
            self._con.close()


class CompanyManager:
    """Opens (and creates) the database of a company by its name."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._open: dict[str, CompanyDB] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(name: str) -> str:
        return hashlib.sha1(normalize_name(name).casefold().encode()).hexdigest()[:12]

    def open(self, name: str) -> CompanyDB:
        name = normalize_name(name)
        if not name:
            raise ValueError("회사명이 비었습니다.")
        k = self.key(name)
        with self._lock:
            if k not in self._open:
                self._open[k] = CompanyDB(self.root / k / "company.db", name)
            return self._open[k]

    def list_companies(self) -> list[str]:
        names = []
        for p in sorted(self.root.glob("*/company.db")):
            try:
                names.append(self.open_by_dir(p.parent.name).name)
            except Exception:
                continue
        return names

    def open_by_dir(self, dirname: str) -> CompanyDB:
        with self._lock:
            if dirname not in self._open:
                con = sqlite3.connect(self.root / dirname / "company.db")
                row = con.execute("SELECT value FROM meta WHERE key='name'").fetchone()
                con.close()
                self._open[dirname] = CompanyDB(
                    self.root / dirname / "company.db", row[0] if row else dirname
                )
            return self._open[dirname]
