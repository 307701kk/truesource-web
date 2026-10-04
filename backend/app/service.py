"""Scan orchestration: runs a scan in a background thread and exposes progress + results."""

from __future__ import annotations

import hashlib
import io
import logging
import threading
from datetime import datetime
from pathlib import Path

from . import catalog, config
from .converter import check_archive, convert_workbook, uncached_formula_cells
from .scanner import ScanListing, ScanPathError, list_excel_files, validate_scan_path
from .store import Store

log = logging.getLogger("truesource.scan")

__all__ = ["ScanBusyError", "ScanPathError", "ScanService"]

MAX_REPORTED = 50


class ScanBusyError(RuntimeError):
    """A scan is already running."""


def _fmt_time(ts: datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if ts else None


class ScanService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.store: Store | None = None  # last completed scan
        self._scan_id = 0
        self._status = self._blank_status()
        self.scanned_path: str | None = None
        self.scan_company: str | None = None  # company whose folder is loaded
        self.sync: dict | None = None  # last sync summary (added / modified / deleted / moved)
        self.on_complete = None  # callback(company, path, fingerprints, sheets, rows) -> sync dict
        self._listing_fp: dict = {}  # cheap fingerprint of the last scan (periodic check)

    @staticmethod
    def _blank_status() -> dict:
        return {
            "state": "idle",
            "scan_id": 0,
            "path": None,
            "files_total": 0,
            "files_done": 0,
            "percent": 0,
            "current_file": None,
            "sheets": 0,
            "data_rows": 0,
            "skipped": {},
            "warnings": [],
            "errors": [],
            "started_at": None,
            "finished_at": None,
            "message": None,
            "company": None,
            "sync": None,
        }

    # ------------------------------------------------------------ control
    def start(
        self,
        raw_path: str,
        *,
        background: bool = True,
        company: str | None = None,
        trigger: str = "scan",
    ) -> dict:
        root = validate_scan_path(raw_path)  # raises ScanPathError
        with self._lock:
            if self._status["state"] == "running":
                raise ScanBusyError("이미 스캔이 진행 중입니다.")
            self._scan_id += 1
            self._status = self._blank_status()
            self._status.update(
                state="running",
                scan_id=self._scan_id,
                path=str(root),
                company=company,
                started_at=_fmt_time(datetime.now()),
            )
            scan_id = self._scan_id
        if background:
            self._thread = threading.Thread(
                target=self._run, args=(root, scan_id, company, trigger), daemon=True, name="scan"
            )
            self._thread.start()
        else:
            self._run(root, scan_id, company, trigger)
        return self.status()

    def sync_now(self) -> dict:
        """Manual "지금 동기화": re-read the loaded folder even if nothing looks changed."""
        with self._lock:
            path, company = self.scanned_path, self.scan_company
        if not path:
            raise ScanPathError("먼저 공유폴더를 분석하세요.")
        return self.start(path, company=company, trigger="manual")

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def status(self) -> dict:
        with self._lock:
            s = dict(self._status)
            s["skipped"] = dict(s["skipped"])
            s["warnings"] = list(s["warnings"])
            s["errors"] = list(s["errors"])
            return s

    def _update(self, **kw) -> None:
        with self._lock:
            self._status.update(kw)

    def _warn(self, msg: str) -> None:
        with self._lock:
            if len(self._status["warnings"]) < MAX_REPORTED:
                self._status["warnings"].append(msg)

    def _error(self, rel: str, msg: str) -> None:
        with self._lock:
            if len(self._status["errors"]) < MAX_REPORTED:
                self._status["errors"].append({"file": rel, "message": msg})

    # ------------------------------------------------------------ the scan
    def _run(
        self, root: Path, scan_id: int, company: str | None = None, trigger: str = "scan"
    ) -> None:
        try:
            listing = list_excel_files(root)
            self._update(files_total=len(listing.files), skipped=dict(listing.skipped))
            for w in listing.warnings:
                self._warn(w)
            store = self._load(listing)
            sync = self._finish_sync(company, root, store)
            if sync:
                sync["trigger"] = trigger
            with self._lock:
                self.store = store
                self.scanned_path = str(root)
                self.scan_company = company
                self.sync = sync
                self._listing_fp = {f.rel_path: (f.size, f.mtime) for f in listing.files}
                self._status.update(
                    sync=sync,
                    state="done",
                    percent=100,
                    current_file=None,
                    finished_at=_fmt_time(datetime.now()),
                )
        except Exception as exc:  # never let the thread die silently
            log.exception("scan failed")
            self._update(
                state="error",
                finished_at=_fmt_time(datetime.now()),
                message=f"스캔 중 오류: {exc.__class__.__name__}",
            )

    def _finish_sync(self, company: str | None, root: Path, store: Store) -> dict | None:
        """Save the scan for the company (fingerprints + sync log); returns the diff summary."""
        if not (company and self.on_complete):
            return None
        fp = {
            r[0]: (r[1], r[2].isoformat(timespec="seconds"), r[3])
            for r in store.query("SELECT rel_path, size, modified, sha256 FROM files")
        }
        st = self.status()
        columns: dict[str, list[str]] = {}
        for name, rel in store.query(
            "SELECT DISTINCT c.name, f.rel_path FROM columns c"
            " JOIN sheets s ON s.table_name = c.table_name JOIN files f ON f.file_id = s.file_id"
        ):
            columns.setdefault(name, []).append(rel)
        try:
            return self.on_complete(
                company, str(root), fp, st["sheets"], st["data_rows"], columns=columns
            )
        except Exception:  # a storage problem must not fail the scan itself
            log.exception("could not record scan for company")
            return None

    def auto_sync_once(self) -> bool:
        """Periodic check: re-scan if files in the loaded folder changed. True if a scan started."""
        with self._lock:
            path, company, last, busy = (
                self.scanned_path,
                self.scan_company,
                self._listing_fp,
                self._status["state"] == "running",
            )
        if not path or busy:
            return False
        try:
            listing = list_excel_files(Path(path))
        except OSError:
            return False
        if {f.rel_path: (f.size, f.mtime) for f in listing.files} == last:
            return False
        try:
            self.start(path, company=company, trigger="auto")
        except (ScanBusyError, ScanPathError):
            return False
        return True

    def _load(self, listing: ScanListing) -> Store:
        store = Store()
        facts: list[catalog.FileFacts] = []
        total_sheets = total_rows = 0
        uncached_by_file: dict[str, int] = {}
        for i, f in enumerate(listing.files, start=1):
            self._update(current_file=f.rel_path)
            modified = datetime.fromtimestamp(f.mtime)
            name = f.rel_path.rsplit("/", 1)[-1]
            common = dict(
                rel_path=f.rel_path,
                name=name,
                dept=catalog.classify_department(f.rel_path),
                top_folder=catalog.top_folder(f.rel_path),
                size=f.size,
                modified=modified,
            )
            if total_rows >= config.MAX_TOTAL_ROWS:
                msg = f"한 번에 읽을 수 있는 행 수({config.MAX_TOTAL_ROWS:,})를 넘어 건너뜀"
                store.add_file(sha256=None, status="error", error=msg, **common)
                self._error(f.rel_path, msg)
                self._update(files_done=i, percent=int(i * 100 / len(listing.files)))
                continue
            try:
                data = (
                    f.abs_path.read_bytes()
                )  # one read: the hash and the parse see the same bytes
                digest = hashlib.sha256(data).hexdigest()
                check_archive(io.BytesIO(data))
                uncached = uncached_formula_cells(io.BytesIO(data))
                sheets = convert_workbook(io.BytesIO(data))
                del data
            except Exception as exc:
                msg = f"{exc.__class__.__name__}: {str(exc)[:150]}"
                store.add_file(sha256=None, status="error", error=msg, **common)
                self._error(f.rel_path, msg)
            else:
                file_id = store.add_file(sha256=digest, **common)
                if uncached:
                    uncached_by_file[f.rel_path] = uncached
                    self._warn(
                        f"{f.rel_path}: 계산값이 저장되지 않은 수식 {uncached}개는 빈 값으로 처리됨"
                        " (엑셀에서 열어 저장하면 해결)"
                    )
                rows = loaded = 0
                failed: list[str] = []
                for sh in sheets:
                    try:
                        store.add_sheet(file_id, sh)
                    except Exception as exc:
                        log.exception("sheet failed: %s [%s]", f.rel_path, sh.sheet_name)
                        failed.append(sh.sheet_name)
                        self._error(f.rel_path, f"[{sh.sheet_name}] {exc.__class__.__name__}")
                        continue
                    loaded += 1
                    rows += sh.n_data_rows
                    self._sheet_warnings(f.rel_path, sh)
                if failed:
                    store.finish_file(
                        file_id,
                        loaded,
                        rows,
                        status="partial",
                        error=f"일부 시트를 읽지 못함: {', '.join(failed)}"[:200],
                    )
                else:
                    store.finish_file(file_id, loaded, rows)
                total_sheets += loaded
                total_rows += rows
                preamble = next((s.preamble for s in sheets if s.preamble), [])
                facts.append(catalog.FileFacts(f.rel_path, name, modified, digest, preamble))
            self._update(
                files_done=i,
                sheets=total_sheets,
                data_rows=total_rows,
                percent=int(i * 100 / max(len(listing.files), 1)),
            )
        for rel, info in catalog.assign_freshness(facts).items():
            store.set_freshness(rel, info["fresh"], info["copy_of"], info["data_date"])
            store.file_info[rel] = {
                "tie_with": info.get("tie_with", []),
                "newer": info.get("newer"),
                "locked": listing.locked.get(rel),
                "uncached": uncached_by_file.get(rel, 0),
            }
        return store

    def _sheet_warnings(self, rel: str, sh) -> None:
        if sh.truncated:
            self._warn(f"{rel} [{sh.sheet_name}]: {config.MAX_ROWS_PER_SHEET}행 이후는 읽지 않음")
        if sh.error_cells:
            self._warn(f"{rel} [{sh.sheet_name}]: 엑셀 오류 셀 {sh.error_cells}개를 빈 값으로 처리")
        if sh.mixed_columns:
            self._warn(
                f"{rel} [{sh.sheet_name}]: 자료형이 섞인 열 {sh.mixed_columns} (문자열로 저장)"
            )

    # ------------------------------------------------------------ results
    def _snapshot(self) -> tuple[Store | None, str | None, dict]:
        """store, scanned_path and status taken together, so they belong to the same scan."""
        with self._lock:
            status = dict(self._status)
            status["skipped"] = dict(status["skipped"])
            return self.store, self.scanned_path, status

    def catalog(self) -> dict:
        store, scanned_path, status = self._snapshot()
        out = self._catalog_from(store, scanned_path, status)
        with self._lock:
            out.update(company=self.scan_company, sync=self.sync)
        return out

    @staticmethod
    def _catalog_from(store: Store | None, scanned_path: str | None, status: dict) -> dict:
        base = {
            "state": status["state"],
            "scanned_path": scanned_path,
            "departments": [*config.KNOWN_DEPARTMENTS, config.UNCLASSIFIED],
            "skipped": status["skipped"],
            "total": 0,
            "files": [],
        }
        if store is None:
            return base
        files = []
        for f in store.files():
            files.append(
                {
                    "id": f["file_id"],
                    "name": f["name"],
                    "path": f["rel_path"],
                    "dept": f["dept"],
                    "top_folder": f["top_folder"],
                    "modified": f["modified"].strftime("%Y-%m-%d %H:%M"),
                    "size": f["size"],
                    "sheets": f["sheet_count"],
                    "rows": f["data_rows"],
                    "fresh": f["fresh"],
                    "copy_of": f["copy_of"],
                    "data_date": f["data_date"],
                    "error": f["error"],
                    "editing": store.file_info.get(f["rel_path"], {}).get("locked"),
                }
            )
        base.update(total=len(files), files=files)
        return base

    def column_names(self) -> set[str]:
        store = self.store
        return {r[0] for r in store.query("SELECT DISTINCT name FROM columns")} if store else set()

    def stamps_for(self, paths) -> dict[str, str]:
        """{path: sha256[:12]} of the files an answer was built from (to spot later changes)."""
        store = self.store
        if store is None:
            return {}
        sha = {r[0]: r[1] for r in store.query("SELECT rel_path, sha256 FROM files")}
        return {p: sha[p][:12] for p in dict.fromkeys(paths) if sha.get(p)}

    def check_stamps(self, stamps: dict[str, str]) -> dict[str, dict]:
        """Compare stamps of an old answer with the files now loaded:
        ok | changed (same path, new content) | moved (same content elsewhere) | deleted."""
        store = self.store
        sha = (
            {r[0]: (r[1] or "")[:12] for r in store.query("SELECT rel_path, sha256 FROM files")}
            if store
            else {}
        )
        by_sha = {v: k for k, v in sha.items() if v}
        out: dict[str, dict] = {}
        for path, stamp in stamps.items():
            if path in sha:
                out[path] = {"status": "ok" if sha[path] == stamp else "changed"}
            elif stamp in by_sha:
                out[path] = {"status": "moved", "to": by_sha[stamp]}
            else:
                out[path] = {"status": "deleted"}
        return out

    def file_detail(self, file_id: int) -> dict | None:
        store, scanned_path, status = self._snapshot()
        if store is None:
            return None
        for f in self._catalog_from(store, scanned_path, status)["files"]:
            if f["id"] == file_id:
                return {**f, "sheet_list": store.sheets_of(file_id)}
        return None
