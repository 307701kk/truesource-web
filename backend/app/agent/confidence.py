"""Confidence = the worst of the signals, decided by code from rules (never by the LLM).

Signals: ① 매핑 확신도  ② 교차검증  ③ 데이터 품질  ④ 실행 이상  (+ 숫자 검증, 근거 확보 when they fail)
Every rule that fired is a finding (rules.py) with a title, "what to check" and where the proof is.
The grade of a signal is the worst severity among its findings; the final grade is the worst signal.
`evaluate` returns the grade, a one-sentence reason, per-signal grades, the warning list for the
screen and a plain message telling the reader what to check.
"""

from __future__ import annotations

from . import rules

ORDER = {"높음": 2, "보통": 1, "낮음": 0}
SIGNALS = [rules.MAPPING, rules.CROSS, rules.QUALITY, rules.EXEC]
GOOD = {
    rules.MAPPING: "모든 용어가 사전과 일치",
    rules.CROSS: "다른 파일 재계산과 일치(오차 1% 이내)",
    rules.QUALITY: "결측·중복·버전 문제 없음",
    rules.EXEC: "이상 없음",
    rules.NUMBERS: "답변 속 숫자가 모두 계산값과 일치",
    rules.EVIDENCE: "근거 파일 있음",
}
MAX_REASONS = 3


def _legacy_findings(ctx) -> list[dict]:
    """Facts the tools record directly on the context (kept simple) -> findings."""
    mk, out = rules.make_finding, []
    # ① mapping
    if ctx.term_grades:
        if "미등록" in ctx.term_grades:
            out.append(mk("M1", terms=", ".join(sorted(ctx.unregistered)) or "일부 용어"))
        if ctx.defaults_applied:
            out.append(mk("M2", defaults=", ".join(ctx.defaults_applied)))
    elif ctx.queries:
        out.append(mk("M3"))
    # ② cross-check
    c = ctx.cross
    if c is None:
        out.append(mk("X1" if ctx.queries else "X9"))
    else:
        rel = c.get("max_rel", 0.0)
        shown = f"{rel * 100:.1f}%"
        if c["rank_changed"]:
            out.append(mk("X4"))
        elif not c["match"]:
            out.append(mk("X3" if rel > 0.05 else "X2", max_rel=shown))
        if not c["match"] and not ctx.traced:
            out.append(mk("X8"))
    # legacy version / quality facts (simple contexts built without the audit module)
    for name in sorted(ctx.stale_used):
        out.append(mk("V1", file=name, newer="(확인 필요)"))
    for name in sorted(ctx.copy_used):
        out.append(mk("V2", file=name, orig="원본 파일"))
    for note in ctx.quality_notes:
        out.append({**mk("D1", rows="?", pct="?", cells=""), "title": note, "where": None})
    # ④ execution
    if ctx.anomalies.get("retry_failed"):
        out.append(mk("E1"))
    if ctx.anomalies.get("retry_ok"):
        out.append(mk("E2"))
    if ctx.anomalies.get("empty"):
        out.append(mk("E3"))
    if ctx.anomalies.get("negative"):
        out.append(mk("E4"))
    if ctx.budget_hit:
        out.append(mk("E5"))
    if ctx.unstructured:
        out.append(mk("E6"))
    if ctx.number_check == "failed":
        out.append(mk("N1", numbers=", ".join(ctx.bad_numbers) or "일부"))
    if ctx.final is not None and not ctx.unstructured and not ctx.sources and not ctx.searched:
        out.append(mk("S1"))
    return out


def evaluate(ctx) -> dict:
    if getattr(ctx, "no_data", None):  # nothing relevant found: the only open point is "not found"
        n = len(ctx.no_data["not_analyzed"])
        f = rules.make_finding("S2")
        if n:
            f["action"] += (
                f" (분석하지 못한 파일 유형 {n}가지: {', '.join(ctx.no_data['not_analyzed'])})"
            )
        return {
            "level": "보통",
            "reason": f["title"],
            "signals": [{"name": rules.EVIDENCE, "grade": "보통", "reason": f["title"]}],
            "warnings": [f],
            "check_message": _check_message("보통", [f]),
        }
    findings = [*ctx.findings, *_legacy_findings(ctx)]
    if ctx.cross and ctx.cross.get("date_gap"):
        findings.append(rules.make_finding("X7", dates=ctx.cross["date_gap"]))
    # a more specific finding replaces the generic one for the same place
    graded = [f for f in findings if f["severity"] != rules.INFO]

    signals = []
    for name in [*SIGNALS, rules.NUMBERS, rules.EVIDENCE]:
        mine = [f for f in graded if f["signal"] == name]
        if not mine and name in (rules.NUMBERS, rules.EVIDENCE):
            continue
        worst = min(
            (ORDER["높음"] if not mine else ORDER[_grade(f)] for f in mine), default=ORDER["높음"]
        )
        grade = next(g for g, v in ORDER.items() if v == worst)
        top = [f for f in mine if _grade(f) == grade and grade != "높음"]
        reason = "; ".join(f["title"] for f in top[:MAX_REASONS]) if top else GOOD[name]
        if name == rules.CROSS and not mine and ctx.cross is None:
            reason = GOOD[name]
        signals.append({"name": name, "grade": grade, "reason": reason})

    worst_level = min(ORDER[s["grade"]] for s in signals)
    level = next(g for g, v in ORDER.items() if v == worst_level)
    worst = [f for f in graded if _grade(f) == level] if level != "높음" else []
    seen: list[str] = []
    for f in sorted(worst, key=lambda f: SIGNAL_RANK.get(f["signal"], 9)):
        if f["title"] not in seen:
            seen.append(f["title"])
    reason = "; ".join(seen[:MAX_REASONS]) if seen else "모든 신호가 양호"
    if len(seen) > MAX_REASONS:
        reason += f" 외 {len(seen) - MAX_REASONS}건"
    if level != "높음" and ctx.traced and any(f["signal"] == rules.CROSS for f in worst):
        reason += " (원인 확인됨)"

    warnings = sorted(
        findings,
        key=lambda f: (rules.severity_rank(f["severity"]), SIGNAL_RANK.get(f["signal"], 9)),
    )
    return {
        "level": level,
        "reason": reason,
        "signals": signals,
        "warnings": warnings,
        "check_message": _check_message(level, warnings),
    }


SIGNAL_RANK = {name: i for i, name in enumerate([*SIGNALS, rules.NUMBERS, rules.EVIDENCE])}


def _grade(f: dict) -> str:
    return (
        "낮음" if f["severity"] == rules.LOW else "보통" if f["severity"] == rules.MID else "높음"
    )


def _check_message(level: str, warnings: list[dict]) -> str | None:
    """Plain text for the reader: why the grade is not 높음 and what to check first."""
    if level == "높음":
        return None
    actions: list[str] = []
    for f in warnings:
        if f["severity"] == rules.INFO or f["action"] in actions:
            continue
        actions.append(f["action"])
    head = (
        "신뢰도가 낮습니다. 이 숫자를 보고서나 의사결정에 쓰기 전에 아래를 확인하세요."
        if level == "낮음"
        else "사용할 수는 있지만 아래 항목을 확인하면 더 확실합니다."
    )
    shown = actions[:4]
    more = f" (그 외 {len(actions) - 4}건은 '확인할 항목'에 있습니다)" if len(actions) > 4 else ""
    return head + " " + " ".join(f"{i}) {a}" for i, a in enumerate(shown, 1)) + more
