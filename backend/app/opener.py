"""Open a scanned Excel file in the user's own spreadsheet app.

Only files that belong to the loaded scan (inside its root, an Excel extension) can be opened,
and the request must come from this machine. Nothing is written or modified.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unicodedata
from pathlib import Path

from . import config

OPENABLE = config.EXCEL_EXTENSIONS | config.LEGACY_EXTENSIONS


class OpenError(ValueError):
    """The file cannot be opened (message is safe to show)."""


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def resolve_catalog_path(root: str | Path, rel_path: str) -> Path | None:
    """Real path of a catalog entry. macOS may list Hangul names decomposed (NFD) while the
    catalog stores them composed, so match each path part by its NFC form. None if missing."""
    root = Path(root)
    direct = root / rel_path
    if direct.exists():
        return direct
    cur = root
    for part in rel_path.split("/"):
        try:
            names = os.listdir(cur)
        except OSError:
            return None
        hit = next((n for n in names if _nfc(n) == _nfc(part)), None)
        if hit is None:
            return None
        cur = cur / hit
    return cur if cur.exists() else None


def open_file(root: str | Path | None, rel_path: str, known_paths: set[str]) -> Path:
    """Validate and open. Raises OpenError."""
    if not root:
        raise OpenError("먼저 공유폴더를 분석하세요.")
    rel = _nfc((rel_path or "").strip())
    if rel not in known_paths:  # only what the scan saw: no arbitrary path, no '..'
        raise OpenError("분석된 파일 목록에 없는 파일입니다.")
    path = resolve_catalog_path(root, rel)
    if path is None:
        raise OpenError(
            "파일을 찾을 수 없습니다 (이동·삭제됐을 수 있습니다). 폴더를 다시 분석하세요."
        )
    root_resolved, real = Path(root).resolve(), path.resolve()
    if root_resolved not in real.parents:
        raise OpenError("공유폴더 밖의 파일은 열 수 없습니다.")
    if real.suffix.lower() not in OPENABLE:
        raise OpenError("엑셀 파일만 열 수 있습니다.")
    _launch(real)
    return real


def _launch(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607
    except OSError as exc:
        raise OpenError(f"엑셀을 실행하지 못했습니다: {exc.__class__.__name__}") from exc
