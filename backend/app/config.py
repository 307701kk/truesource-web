"""Settings. Edit KNOWN_DEPARTMENTS to match the company's folder names."""

from __future__ import annotations

import os
from pathlib import Path

# Department names. A file's department is the first of these found in its
# relative path (folder names, then the file name). If none match -> UNCLASSIFIED.
KNOWN_DEPARTMENTS: list[str] = ["영업팀", "경영지원팀", "회계팀", "물류팀", "구매팀", "공용"]
UNCLASSIFIED = "미분류"

# Extensions that are read. (.xls is the old binary format; it is only counted as skipped.)
EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
LEGACY_EXTENSIONS = {".xls"}

MAX_FILE_BYTES = 100 * 1024 * 1024  # larger files are skipped
MAX_UNCOMPRESSED_BYTES = 600 * 1024 * 1024  # zip-bomb guard (size after unzipping)
MAX_FILES = 5000  # scan stops collecting after this many files
MAX_ROWS_PER_SHEET = 300_000  # non-empty rows beyond this are dropped (with a warning)
MAX_TOTAL_ROWS = 3_000_000  # stop loading more files after this many rows in one scan

# Value index: only text cells whose length is within these bounds are indexed.
INDEX_MIN_LEN = 2
INDEX_MAX_LEN = 120

# Folders that may never be scanned (reading them is pointless and risky).
BLOCKED_DIRS_WINDOWS = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
]
BLOCKED_DIRS_POSIX = ["/etc", "/proc", "/sys", "/dev", "/boot"]


def allowed_roots() -> list[Path]:
    """Optional allow-list. If TRUESOURCE_ALLOWED_ROOTS is set (os.pathsep separated),
    a scan path must be inside one of them."""
    raw = os.environ.get("TRUESOURCE_ALLOWED_ROOTS", "").strip()
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def cors_origins() -> list[str]:
    raw = os.environ.get("TRUESOURCE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [o.strip() for o in raw.split(",") if o.strip()]


# ---- agent (LLM) ----
GEMINI_DEFAULT_MODEL = "gemini-3.8-flash"
MAX_TOOL_CALLS = 10  # tool calls per question (final_answer / ask_user are not counted)
MAX_QUERY_RETRIES = 2  # failed run_query attempts allowed per question
MAX_NUDGES = 3  # times the model may answer in plain text instead of calling a tool
MAX_NUMBER_RETRIES = 2  # rewrites allowed when a number in the answer does not match
CROSS_TOLERANCE = 0.01  # cross-check: relative difference regarded as "same"
MAX_GROUP_ROWS = 30  # a query may return at most this many aggregate rows to the LLM
MAX_HISTORY_TURNS = 6  # earlier questions kept in a session (compact form)
AUDIT_MAX_ENTRIES = 500


def gemini_api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", "").strip() or GEMINI_DEFAULT_MODEL


# ---- per-company storage ----
def data_dir() -> Path:
    """Where company databases live (one SQLite file per company). Local only, never committed."""
    raw = os.environ.get("TRUESOURCE_DATA_DIR", "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent / "data"


def sync_interval_seconds() -> int:
    """Periodic folder re-check (design: every ~10 min). 0 turns it off."""
    try:
        return int(os.environ.get("TRUESOURCE_SYNC_SECONDS", "600"))
    except ValueError:
        return 600
