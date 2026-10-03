"""Number check: every figure with a unit (억/만원/원/%) or a decimal point in the written answer
must also appear in the tool results (or the question). Code decides, not the LLM."""

from __future__ import annotations

import json
import re

_DATE = re.compile(
    r"\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}|\d{1,2}\s*/\s*\d{1,2}|\d{4}\.\d{1,2}"
)
_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억원|억|만원|천원|만|원|%)?")
_ID = re.compile(r"[A-Za-z]+\d[\w-]*|\d{3}-\d{2}-\d{5}")  # S260930-R01, 사업자번호 ...


def _norm(tok: str) -> float | None:
    try:
        return round(float(tok.replace(",", "")), 4)
    except ValueError:
        return None


def numbers_in(text: str, *, strict: bool) -> set[float]:
    """strict=True keeps only figures that carry a unit or a decimal point (those are checked)."""
    text = _ID.sub(" ", _DATE.sub(" ", text))
    out: set[float] = set()
    for m in _NUM.finditer(text):
        tok, unit = m.group(1), m.group(2)
        if strict and not unit and "." not in tok:
            continue
        if (v := _norm(tok)) is not None:
            out.add(v)
    return out


def allowed_numbers(tool_results: list[dict], question: str) -> set[float]:
    blob = " ".join(json.dumps(r, ensure_ascii=False) for r in tool_results) + " " + question
    allowed = numbers_in(blob, strict=False)
    # a figure shown as "3.02억" is also fine written as 302,000,000 / 3.02 ... and vice versa
    return allowed | {round(v / 1e8, 4) for v in allowed} | {round(v / 1e4, 4) for v in allowed}


def _readable(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def check(texts: list[str], allowed: set[float]) -> list[str]:
    """Return the figures that cannot be traced to a tool result (in a form people can read)."""
    bad = []
    for t in texts:
        for v in sorted(numbers_in(t or "", strict=True)):
            if v not in allowed:
                bad.append(_readable(v))
    return sorted(set(bad))
