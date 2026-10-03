"""Confidence = the worst of four signals, decided by code from facts (never by the LLM).

① mapping    term-dictionary match grades          높음 / 보통(기본값 적용) / 낮음(미등록 추정)
② cross      cross-check against another file      높음(일치) / 보통(불일치·결론불변, 또는 검증 불가) / 낮음(결론 변경)
③ quality    nulls / duplicates / version state    높음 / 보통(경미) / 낮음(구버전 사용)
④ execution  empty / negative / retried            높음 / 보통(재시도 후 해결·이상값) / 낮음(재시도 후에도 실패)
"""

from __future__ import annotations

ORDER = {"높음": 2, "보통": 1, "낮음": 0}


def _signal(name: str, grade: str, reason: str) -> dict:
    return {"name": name, "grade": grade, "reason": reason}


def evaluate(ctx) -> dict:
    sig: list[dict] = []

    # ① mapping
    if ctx.term_grades:
        if "미등록" in ctx.term_grades:
            sig.append(_signal("매핑 확신도", "낮음", "용어사전에 없는 표현을 추정해 매핑함"))
        elif ctx.defaults_applied:
            sig.append(
                _signal(
                    "매핑 확신도", "보통", "기본값 규칙 적용: " + ", ".join(ctx.defaults_applied)
                )
            )
        else:
            sig.append(_signal("매핑 확신도", "높음", "모든 용어가 사전과 일치"))
    elif ctx.queries:
        sig.append(_signal("매핑 확신도", "보통", "용어 조회 없이 질의함"))
    else:
        sig.append(_signal("매핑 확신도", "높음", "계산 없는 질문"))

    # ② cross-check
    c = ctx.cross
    if c is None:
        if ctx.queries:
            sig.append(_signal("교차검증", "낮음", "계산했지만 교차검증을 하지 못함"))
        else:
            sig.append(_signal("교차검증", "보통", "교차검증 불가 (계산 없는 질문)"))
    elif c["rank_changed"]:
        sig.append(_signal("교차검증", "낮음", "교차검증 불일치로 순위가 바뀜"))
    elif not c["match"]:
        sig.append(_signal("교차검증", "보통", "교차검증 불일치가 있으나 순위는 같음"))
    else:
        sig.append(_signal("교차검증", "높음", "다른 파일 재계산과 일치(오차 1% 이내)"))

    # ③ data quality
    if ctx.stale_used:
        sig.append(
            _signal(
                "데이터 품질",
                "낮음",
                "구버전 파일을 기준으로 사용함: " + ", ".join(sorted(ctx.stale_used)),
            )
        )
    elif ctx.quality_notes or ctx.copy_used:
        notes = [*ctx.quality_notes, *(f"사본 사용: {n}" for n in sorted(ctx.copy_used))]
        sig.append(_signal("데이터 품질", "보통", "; ".join(notes)))
    else:
        sig.append(_signal("데이터 품질", "높음", "결측·중복·버전 문제 없음"))

    # ④ execution
    if ctx.anomalies.get("retry_failed"):
        sig.append(_signal("실행 이상", "낮음", "쿼리가 재시도 후에도 실패함"))
    elif ctx.anomalies:
        names = {
            "retry_ok": "쿼리 재시도 후 해결",
            "empty": "빈 결과 발생",
            "negative": "음수 합계 발생",
        }
        sig.append(
            _signal("실행 이상", "보통", ", ".join(names[k] for k in ctx.anomalies if k in names))
        )
    else:
        sig.append(_signal("실행 이상", "높음", "이상 없음"))

    # number check (code re-checks every figure in the written answer)
    if ctx.number_check == "failed":
        sig.append(_signal("숫자 검증", "낮음", "답변 속 일부 숫자가 계산값과 일치하지 않음"))

    worst = min(sig, key=lambda s: ORDER[s["grade"]])
    level = worst["grade"]
    reasons = [s["reason"] for s in sig if s["grade"] == level and level != "높음"] or [
        worst["reason"]
    ]
    reason = "; ".join(reasons)
    if level != "높음" and ctx.traced:
        reason += " (원인 확인됨)"
    return {"level": level, "reason": reason, "signals": sig}
