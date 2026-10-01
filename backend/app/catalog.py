"""Catalog rules: department, version family and freshness of each scanned file.

Freshness (field `fresh`):
  ok     latest version in its family (same stem once suffixes like _v2/_최종/(1) are removed)
  stale  an older version of a family that has a newer one
  copy   byte-identical to another file (backup, renamed copy, mail attachment ...)
Known limits: a file whose name shares nothing with its newer version (e.g. 거래처목록 vs
거래처마스터) is not linked, and a "format only" copy (same data, different layout) is not
detected; both are shown as `ok`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from . import config

FRESH_OK = "ok"
FRESH_STALE = "stale"
FRESH_COPY = "copy"

_LABELS = r"(기준일|최종\s*수정일|업데이트|작성일|마감일)"
_DATE_LINE_RE = re.compile(
    _LABELS + r"\s*[:：|]?\s*(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})"
)
_SUFFIX_PATTERNS = [
    re.compile(r"\(\d+\)$"),
    re.compile(r"\s*-\s*(복사본|copy)$", re.I),
    re.compile(r"[_\s-]*(old|수정|최종|천원|final|copy|복사본)$", re.I),
    re.compile(r"[_\s-]*v\d+$", re.I),
]


@dataclass
class FileFacts:
    """What the freshness rule needs to know about one readable file."""

    rel_path: str
    name: str
    mtime: datetime
    sha256: str
    preamble: list[str]


def classify_department(rel_path: str) -> str:
    """First known department found in the folder names, then in the file name."""
    parts = rel_path.split("/")
    for part in parts[:-1]:
        if part in config.KNOWN_DEPARTMENTS:
            return part
    for dept in config.KNOWN_DEPARTMENTS:
        if dept in parts[-1]:
            return dept
    return config.UNCLASSIFIED


def top_folder(rel_path: str) -> str:
    parts = rel_path.split("/")
    return parts[0] if len(parts) > 1 else ""


def family_stem(file_name: str) -> str:
    """File name without extension and without version-ish suffixes."""
    stem = re.sub(r"\.[^.]+$", "", file_name).strip()
    prev = None
    while prev != stem:
        prev = stem
        for pat in _SUFFIX_PATTERNS:
            stem = pat.sub("", stem).strip()
        # 수주대장_2026_0915 -> 수주대장_2026 (a MMDD tag after a year tag)
        if re.search(r"_\d{4}_\d{4}$", stem):
            stem = re.sub(r"_\d{4}$", "", stem)
    return stem


def data_date(preamble: list[str], mtime: datetime) -> str:
    """Date the data refers to: '기준일/최종수정일 ...' in the sheet header, else file mtime.
    A date later than the file's own modification date is not trusted (e.g. a due date)."""
    for line in preamble:
        m = _DATE_LINE_RE.search(line)
        if m:
            y, mo, d = (int(m.group(i)) for i in (2, 3, 4))
            try:
                found = datetime(y, mo, d)
            except ValueError:
                continue
            if found.date() <= mtime.date():
                return found.date().isoformat()
    return mtime.date().isoformat()


_COPY_MARKER_RE = re.compile(r"\(\d+\)|복사본|[\s_-]copy", re.I)


def _original_sort_key(f: FileFacts):
    """Among byte-identical files the 'original' sorts first: it lives in a department folder,
    is the oldest, does not look like a copy ("(1)", "복사본"), then has the shorter name."""
    in_dept = f.rel_path.split("/")[0] in config.KNOWN_DEPARTMENTS
    return (
        0 if in_dept else 1,
        f.mtime,
        1 if _COPY_MARKER_RE.search(f.name) else 0,
        len(f.name),
        f.rel_path,
    )


def assign_freshness(files: list[FileFacts]) -> dict[str, dict]:
    """rel_path -> {fresh, copy_of, data_date}."""
    result: dict[str, dict] = {}
    by_hash: dict[str, list[FileFacts]] = {}
    for f in files:
        by_hash.setdefault(f.sha256, []).append(f)

    copies: dict[str, str] = {}
    for group in by_hash.values():
        group = sorted(group, key=_original_sort_key)
        for dup in group[1:]:
            copies[dup.rel_path] = group[0].rel_path

    dates = {f.rel_path: data_date(f.preamble, f.mtime) for f in files}

    def in_dept_folder(f: FileFacts) -> bool:
        parts = f.rel_path.split("/")
        return len(parts) > 1 and parts[0] in config.KNOWN_DEPARTMENTS

    # Files inside a department folder: family = (stem, department, parent folder), so
    # same-named files of different customers/projects are not compared with each other.
    families: dict[tuple, list[FileFacts]] = {}
    outside: list[FileFacts] = []
    for f in files:
        if f.rel_path in copies:
            continue
        if in_dept_folder(f):
            key = (
                family_stem(f.name),
                classify_department(f.rel_path),
                f.rel_path.rsplit("/", 1)[0],
            )
            families.setdefault(key, []).append(f)
        else:
            outside.append(f)
    # Files outside department folders (backup, personal, "새 폴더") join the one matching
    # in-department family; if that is ambiguous they only compare among themselves.
    loose: dict[tuple, list[FileFacts]] = {}
    for f in outside:
        stem, dept = family_stem(f.name), classify_department(f.rel_path)
        cands = [
            k for k in families if k[0] == stem and (dept == config.UNCLASSIFIED or k[1] == dept)
        ]
        if len(cands) == 1:
            families[cands[0]].append(f)
        else:
            loose.setdefault((stem, dept, ""), []).append(f)
    families.update(loose)

    for members in families.values():
        newest = max(dates[m.rel_path] for m in members)
        for m in members:
            fresh = FRESH_OK if dates[m.rel_path] == newest else FRESH_STALE
            result[m.rel_path] = {"fresh": fresh, "copy_of": None, "data_date": dates[m.rel_path]}
    for rel, original in copies.items():
        result[rel] = {"fresh": FRESH_COPY, "copy_of": original, "data_date": dates[rel]}
    return result
