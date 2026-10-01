from __future__ import annotations

import pytest

from app import config
from app.scanner import ScanPathError, list_excel_files, validate_scan_path


def test_lists_only_excel_and_reports_skips(share):
    listing = list_excel_files(share)
    names = {f.rel_path for f in listing.files}
    assert "영업팀/2026/실적/실적집계_v2.xlsx" in names
    assert "영업팀/깨진파일.xlsx" in names  # still listed; the converter reports the error
    assert not any(n.endswith((".txt", ".xls")) or "~$" in n for n in names)
    assert listing.skipped == {"unsupported_xls": 1, "lock_file": 1}


def test_rel_paths_are_posix_and_sorted_walk(share):
    listing = list_excel_files(share)
    assert all("\\" not in f.rel_path for f in listing.files)


def test_symlink_files_are_skipped(share):
    target = share / "개인/메모.xlsx"
    link = share / "개인/링크.xlsx"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported here")
    listing = list_excel_files(share)
    assert listing.skipped.get("symlink") == 1


def test_max_files_cap(share, monkeypatch):
    monkeypatch.setattr(config, "MAX_FILES", 2)
    listing = list_excel_files(share)
    assert len(listing.files) == 2 and listing.warnings


def test_validate_path_errors(tmp_path, share):
    with pytest.raises(ScanPathError):
        validate_scan_path("")
    with pytest.raises(ScanPathError):
        validate_scan_path(str(tmp_path / "nope"))
    with pytest.raises(ScanPathError):
        validate_scan_path(str(share / "개인/메모.xlsx"))  # a file, not a folder
    assert validate_scan_path(f'"{share}"') == share.resolve()  # surrounding quotes are tolerated


def test_system_folders_are_blocked():
    with pytest.raises(ScanPathError):
        validate_scan_path("/etc")


def test_allowed_roots(share, tmp_path, monkeypatch):
    monkeypatch.setenv("TRUESOURCE_ALLOWED_ROOTS", str(share))
    assert validate_scan_path(str(share / "영업팀")).is_dir()
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ScanPathError):
        validate_scan_path(str(other))


# ---- regressions found in code review ------------------------------------------------------
def test_nul_byte_and_root_paths_are_rejected():
    with pytest.raises(ScanPathError):
        validate_scan_path("abc\x00def")
    with pytest.raises(ScanPathError):
        validate_scan_path("/")


def test_unreadable_directory_is_reported(share, monkeypatch):
    import os

    real_walk = os.walk

    def fake_walk(top, followlinks=False, onerror=None):
        yield from real_walk(top, followlinks=followlinks, onerror=onerror)
        onerror(OSError(13, "denied", "비공개폴더"))

    monkeypatch.setattr("app.scanner.os.walk", fake_walk)
    listing = list_excel_files(share)
    assert listing.skipped["unreadable_dir"] == 1
    assert any("비공개폴더" in w for w in listing.warnings)


def test_junctions_and_blocked_subfolders_are_pruned(share, monkeypatch):
    import os

    from .conftest import write_workbook

    write_workbook(share / "연결폴더/a.xlsx", {"s": [["x", "y"], [1, 2]]})
    write_workbook(share / "금지폴더/b.xlsx", {"s": [["x", "y"], [1, 2]]})
    monkeypatch.setattr(os.path, "isjunction", lambda p: str(p).endswith("연결폴더"), raising=False)
    monkeypatch.setattr(config, "BLOCKED_DIRS_POSIX", [str(share / "금지폴더")])
    monkeypatch.setattr(config, "BLOCKED_DIRS_WINDOWS", [str(share / "금지폴더")])
    names = {f.rel_path for f in list_excel_files(share).files}
    assert "연결폴더/a.xlsx" not in names and "금지폴더/b.xlsx" not in names
    assert "개인/메모.xlsx" in names
