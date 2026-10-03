"""Find Excel files under a folder. Read-only: nothing is opened for writing."""

from __future__ import annotations

import hashlib
import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import config


class ScanPathError(ValueError):
    """The requested scan path is not allowed or does not exist."""


@dataclass
class ScannedFile:
    abs_path: Path
    rel_path: str  # posix style, relative to the scan root
    size: int
    mtime: float


@dataclass
class ScanListing:
    root: Path
    files: list[ScannedFile] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _is_inside(path: Path, parent: Path) -> bool:
    a = os.path.normcase(str(path))
    b = os.path.normcase(str(parent))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def _is_blocked(path: Path) -> bool:
    blocked = config.BLOCKED_DIRS_WINDOWS if os.name == "nt" else config.BLOCKED_DIRS_POSIX
    # also compare against the resolved form (macOS: /etc -> /private/etc)
    return any(_is_inside(path, Path(b)) or _is_inside(path, Path(b).resolve()) for b in blocked)


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", lambda _p: False)
    return path.is_symlink() or is_junction(path)


def validate_scan_path(raw: str) -> Path:
    """Resolve and check a user-supplied folder path. Raises ScanPathError."""
    raw = (raw or "").strip().strip('"')
    if not raw:
        raise ScanPathError("경로가 비어 있습니다.")
    try:
        path = Path(raw).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ScanPathError(f"경로를 해석할 수 없습니다: {exc.__class__.__name__}") from exc
    if not path.exists():
        raise ScanPathError("경로가 존재하지 않거나 접근할 수 없습니다.")
    if not path.is_dir():
        raise ScanPathError("폴더 경로를 입력하세요 (파일 경로는 안 됩니다).")
    if path.parent == path:
        raise ScanPathError("드라이브/루트 전체는 스캔할 수 없습니다. 하위 폴더를 지정하세요.")
    if _is_blocked(path):
        raise ScanPathError("시스템 폴더는 스캔할 수 없습니다.")
    roots = config.allowed_roots()
    if roots and not any(_is_inside(path, r.resolve()) for r in roots):
        raise ScanPathError("허용된 폴더 범위 밖입니다.")
    return path


def list_excel_files(root: Path) -> ScanListing:
    """Walk root (no symlink following) and collect readable Excel files."""
    listing = ScanListing(root=root)

    def on_error(exc: OSError) -> None:
        listing.skip("unreadable_dir")
        listing.warnings.append(f"읽을 수 없는 폴더를 건너뜀: {exc.filename or ''}")

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=on_error):
        keep = []
        for d in sorted(dirnames):  # prune links/junctions and system folders inside the tree
            sub = Path(dirpath) / d
            if _is_link_or_junction(sub) or _is_blocked(sub):
                listing.skip("skipped_dir")
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            if name.startswith("._"):
                continue  # macOS AppleDouble sidecar (not a real workbook); not counted
            ext = os.path.splitext(name)[1].lower()
            if ext in config.LEGACY_EXTENSIONS:
                listing.skip("unsupported_xls")
                continue
            if ext not in config.EXCEL_EXTENSIONS:
                continue
            if name.startswith("~$"):
                listing.skip("lock_file")
                continue
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                listing.skip("unreadable")
                continue
            if full.is_symlink():
                listing.skip("symlink")
                continue
            if st.st_size > config.MAX_FILE_BYTES:
                listing.skip("too_large")
                continue
            if len(listing.files) >= config.MAX_FILES:
                listing.warnings.append(f"파일이 {config.MAX_FILES}개를 넘어 나머지는 건너뜁니다.")
                return listing
            # macOS returns decomposed Hangul (NFD); compose it so names match config/regexes
            rel = unicodedata.normalize("NFC", full.relative_to(root).as_posix())
            listing.files.append(ScannedFile(full, rel, st.st_size, st.st_mtime))
    return listing


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
