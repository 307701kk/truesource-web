from __future__ import annotations

from datetime import datetime

from app.catalog import (
    FileFacts,
    assign_freshness,
    classify_department,
    data_date,
    family_stem,
)


def test_department_rules():
    assert classify_department("영업팀/2026/실적/a.xlsx") == "영업팀"
    assert classify_department("백업/2026-06-30/영업팀/2026/수주대장/a.xlsx") == "영업팀"
    assert classify_department("개인/김대리/실적_참고용.xlsx") == "미분류"
    assert classify_department("임시/영업팀_메모.xlsx") == "영업팀"  # file name keyword
    assert classify_department("a.xlsx") == "미분류"


def test_family_stem():
    assert family_stem("실적집계_v2.xlsx") == "실적집계"
    assert family_stem("실적집계_v2_천원.xlsx") == "실적집계"
    assert family_stem("실적집계_최종(1).xlsx") == "실적집계"
    assert family_stem("재고현황_20260831 - 복사본.xlsx") == "재고현황_20260831"
    assert family_stem("수주대장_2026_0915.xlsx") == "수주대장_2026"
    assert family_stem("수주대장_2026.xlsx") == "수주대장_2026"
    assert family_stem("매출원장_2026_old.xlsx") == "매출원장_2026"
    assert family_stem("재고현황_20260930.xlsx") == "재고현황_20260930"  # snapshots stay separate


def test_data_date_prefers_sheet_header_over_mtime():
    mt = datetime(2026, 9, 30, 10, 0)
    assert data_date(["제목", "기준일: 2026-09-28"], mt) == "2026-09-28"
    assert data_date(["최종수정일: 2026-09-30"], mt) == "2026-09-30"
    assert data_date(["제목만"], mt) == "2026-09-30"
    assert data_date(["기준일: 2026-13-45"], mt) == "2026-09-30"  # impossible date -> fall back


def facts(rel, mtime, sha, preamble=()):
    return FileFacts(
        rel, rel.rsplit("/", 1)[-1], datetime.strptime(mtime, "%Y-%m-%d %H:%M"), sha, list(preamble)
    )


def test_freshness_latest_stale_and_copy():
    files = [
        facts("영업팀/실적집계_최종.xlsx", "2026-08-31 14:26", "a", ["기준일: 2026-08-31"]),
        facts("영업팀/실적집계_v2.xlsx", "2026-09-28 13:10", "b", ["기준일: 2026-09-28"]),
        facts("공용/받은자료/실적집계_v2.xlsx", "2026-09-29 11:42", "b", ["기준일: 2026-09-28"]),
        facts("개인/김대리/실적_참고용.xlsx", "2026-09-28 13:10", "b", ["기준일: 2026-09-28"]),
        facts("물류팀/재고현황_20260930.xlsx", "2026-09-30 18:00", "c"),
    ]
    out = assign_freshness(files)
    assert out["영업팀/실적집계_최종.xlsx"]["fresh"] == "stale"
    assert out["영업팀/실적집계_v2.xlsx"]["fresh"] == "ok"
    assert out["공용/받은자료/실적집계_v2.xlsx"] == {
        "fresh": "copy",
        "copy_of": "영업팀/실적집계_v2.xlsx",
        "data_date": "2026-09-28",
        "newer": None,
        "tie_with": [],
    }
    assert out["개인/김대리/실적_참고용.xlsx"]["copy_of"] == "영업팀/실적집계_v2.xlsx"
    assert out["물류팀/재고현황_20260930.xlsx"]["fresh"] == "ok"


def test_same_content_and_same_mtime_original_is_the_department_copy():
    files = [
        facts("개인/a/x.xlsx", "2026-01-01 00:00", "h"),
        facts("영업팀/x.xlsx", "2026-01-01 00:00", "h"),
    ]
    out = assign_freshness(files)
    assert out["영업팀/x.xlsx"]["fresh"] == "ok"
    assert out["개인/a/x.xlsx"]["copy_of"] == "영업팀/x.xlsx"


def test_copy_named_file_is_never_the_original_on_ties():
    files = [
        facts("영업팀/실적집계_최종(1).xlsx", "2026-08-31 14:26", "z"),
        facts("영업팀/실적집계_최종.xlsx", "2026-08-31 14:26", "z"),
        facts("물류팀/재고현황_20260831 - 복사본.xlsx", "2026-08-31 10:43", "y"),
        facts("물류팀/재고현황_20260831.xlsx", "2026-08-31 10:43", "y"),
    ]
    out = assign_freshness(files)
    assert out["영업팀/실적집계_최종(1).xlsx"]["copy_of"] == "영업팀/실적집계_최종.xlsx"
    assert (
        out["물류팀/재고현황_20260831 - 복사본.xlsx"]["copy_of"] == "물류팀/재고현황_20260831.xlsx"
    )


# ---- regressions found in code review ------------------------------------------------------
def test_same_name_in_different_departments_are_different_documents():
    files = [
        facts("영업팀/주간보고.xlsx", "2026-09-01 09:00", "a"),
        facts("물류팀/주간보고.xlsx", "2026-09-20 09:00", "b"),
        facts("새폴더/주간보고.xlsx", "2026-09-10 09:00", "c"),
    ]
    out = assign_freshness(files)
    assert out["영업팀/주간보고.xlsx"]["fresh"] == "ok"
    assert out["물류팀/주간보고.xlsx"]["fresh"] == "ok"
    assert out["새폴더/주간보고.xlsx"]["fresh"] == "ok"  # unclassified stands on its own


def test_old_copy_in_unclassified_folder_still_joins_a_single_department_family():
    files = [
        facts("경영지원팀/매출원장_2026.xlsx", "2026-09-30 18:21", "a"),
        facts("새 폴더 (2)/매출원장_2026_old.xlsx", "2026-08-31 16:08", "b"),
    ]
    out = assign_freshness(files)
    assert out["새 폴더 (2)/매출원장_2026_old.xlsx"]["fresh"] == "stale"


def test_data_date_formats_and_future_guard():
    mt = datetime(2026, 9, 30, 10, 0)
    assert data_date(["기준일 | 2026-09-15"], mt) == "2026-09-15"
    assert data_date(["기준일: 2026년 9월 15일"], mt) == "2026-09-15"
    assert data_date(["최종 수정일: 2026.09.01"], mt) == "2026-09-01"
    assert data_date(["마감일: 2027-12-31"], mt) == "2026-09-30"  # a due date is not trusted


def _ff(path, day, sha=None):
    return FileFacts(
        rel_path=path,
        name=path.split("/")[-1],
        mtime=datetime(2026, 9, day),
        sha256=sha or path,
        preamble=[],
    )


def test_same_name_files_in_different_folders_of_one_department_are_separate():
    res = assign_freshness(
        [
            _ff("영업팀/고객A/주간보고.xlsx", 1),
            _ff("영업팀/고객B/주간보고.xlsx", 20),
        ]
    )
    assert res["영업팀/고객A/주간보고.xlsx"]["fresh"] == "ok"
    assert res["영업팀/고객B/주간보고.xlsx"]["fresh"] == "ok"


def test_backup_attaches_to_single_matching_family_and_is_stale():
    res = assign_freshness(
        [
            _ff("영업팀/수주대장.xlsx", 20),
            _ff("백업/0630/영업팀/수주대장.xlsx", 5),
            _ff("새 폴더/수주대장_old.xlsx", 3),
        ]
    )
    assert res["영업팀/수주대장.xlsx"]["fresh"] == "ok"
    assert res["백업/0630/영업팀/수주대장.xlsx"]["fresh"] == "stale"
    assert res["새 폴더/수주대장_old.xlsx"]["fresh"] == "stale"


def test_loose_file_stays_standalone_when_match_is_ambiguous():
    res = assign_freshness(
        [
            _ff("영업팀/고객A/보고.xlsx", 20),
            _ff("영업팀/고객B/보고.xlsx", 21),
            _ff("개인/보고.xlsx", 2),
        ]
    )
    assert res["개인/보고.xlsx"]["fresh"] == "ok"
