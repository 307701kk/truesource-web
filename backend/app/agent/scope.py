"""Scope gate: reject questions that have nothing to do with the loaded Excel files, before any LLM call.

Rule-based on purpose (no extra Gemini call, deterministic, explainable). Order:
  1. a word of the company's own vocabulary is in the question (department, file / sheet / column
     name, glossary term, a value found in the data)            -> pass
  2. follow-up of a running conversation ("그럼 2위는?")           -> pass
  3. an everyday off-topic word (날씨, 점심 ...)                   -> reject
  4. a business-intent word (합계, 순위, 찾아줘 ...) but no vocabulary -> pass (the agent says "내용 없음")
  5. otherwise                                                    -> reject
Doubt always means "pass": a wrongly rejected real question costs more than a wasted agent call.
"""

from __future__ import annotations

import re
import unicodedata
import uuid

from .. import config
from .glossary import GLOSSARY

OFF_TOPIC = (
    "날씨", "점심", "저녁", "아침식사", "맛집", "메뉴 추천", "메뉴추천", "농담", "노래", "영화", "드라마",
    "게임", "번역", "코딩", "프로그래밍", "로또", "주식", "비트코인", "코인", "운세", "연애", "사랑",
    "여행", "레시피", "요리", "축구", "야구", "대통령", "뉴스", "시 써", "소설", "너 누구", "안녕",
    "고마워", "심심", "별자리",
)  # fmt: skip
INTENT = (
    "합계", "총", "평균", "순위", "등", "위", "몇", "얼마", "최근", "최신", "찾", "목록", "현황",
    "비교", "증가", "감소", "차이", "맞아", "맞나", "보고서", "파일", "엑셀", "시트", "자료", "데이터",
    "실적", "매출", "재고", "금액", "수량", "분기", "상반기", "하반기", "월별", "누적", "건수", "누가",
    "어느", "어디", "언제", "가장", "제일", "높", "낮", "많", "적", "알려", "보여", "액", "량", "율",
)  # fmt: skip
FOLLOW_UP = (
    "그럼",
    "그러면",
    "그건",
    "그것",
    "그중",
    "그 중",
    "거기",
    "아까",
    "방금",
    "위에",
    "다시",
    "또",
)
GENERIC_FILE_TOKENS = {"xlsx", "xlsm", "copy", "final", "최종", "사본", "백업", "new", "old"}
_WORD = re.compile(r"[0-9A-Za-z가-힣]+")
_cache: dict[int, dict] = {}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def vocabulary(store, custom: list[dict] | None = None) -> dict:
    """Terms of this company's data, rebuilt only when a new scan produced a new store."""
    key = id(store)
    if key not in _cache:
        _cache.clear()
        terms: set[str] = set(config.KNOWN_DEPARTMENTS)
        for (name,) in store.query("SELECT DISTINCT name FROM columns"):
            terms.add(name)
        for (sheet,) in store.query("SELECT DISTINCT sheet_name FROM sheets"):
            terms.add(sheet)
        for (rel,) in store.query("SELECT rel_path FROM files"):
            for part in re.split(r"[/\\._\-\s()]+", rel):
                if (
                    len(part) >= 2
                    and not part.isdigit()
                    and part.casefold() not in GENERIC_FILE_TOKENS
                ):
                    terms.add(part)
        _cache[key] = {"data": {_norm(t) for t in terms if len(t.strip()) >= 2}}
    glossary: set[str] = set()
    for g in [*GLOSSARY, *(custom or [])]:
        glossary.update([g["term"], *g["synonyms"], *g["columns"]])
    return {"data": _cache[key]["data"], "glossary": {_norm(t) for t in glossary if len(t) >= 2}}


def _value_hit(store, question: str) -> bool:
    """Is a word of the question (with a trailing particle cut off) a value in the data?"""
    words = [w for w in _WORD.findall(_norm(question)) if len(w) >= 2][:15]
    variants = {v for w in words for v in (w, w[:-1], w[:-2]) if len(v) >= 2}
    for v in variants:
        like = v.replace("%", "").replace("_", "") + "%"
        if store.query("SELECT 1 FROM value_index WHERE value_norm LIKE ? LIMIT 1", [like]):
            return True
    return False


def check(
    question: str, store, custom: list[dict] | None = None, *, in_session: bool = False
) -> dict | None:
    """None = let the agent answer. Otherwise {"kind", "reason"} for the rejection card."""
    q = _norm(question)
    if not _WORD.search(q):
        return {"kind": "nonsense", "reason": "질문에서 알아볼 수 있는 내용이 없습니다."}
    vocab = vocabulary(store, custom)
    if any(t in q for t in vocab["data"] | vocab["glossary"]) or _value_hit(store, question):
        return None
    off = next((w for w in OFF_TOPIC if w in q), None)
    if in_session and len(q) <= 14 and not off:
        return None
    if any(w in q for w in FOLLOW_UP) and not off:
        return None
    if off:
        return {
            "kind": "offtopic",
            "reason": f"'{off}'은(는) 업로드된 사내 엑셀 자료와 관련이 없습니다.",
        }
    if any(w in q for w in INTENT):
        return None
    return {
        "kind": "offtopic",
        "reason": "질문이 업로드된 엑셀 파일의 내용(부서·파일·항목·거래처 등)과 이어지지 않습니다.",
    }


def build_response(question: str, session_id: str | None, store, rejection: dict) -> dict:
    """Response card for a rejected question: no confidence, no sources, just what can be asked."""
    depts = [d for (d,) in store.query("SELECT DISTINCT dept FROM files ORDER BY dept")]
    cols = [
        n
        for (n,) in store.query(
            "SELECT name FROM columns GROUP BY name ORDER BY COUNT(*) DESC, name LIMIT 12"
        )
    ]
    return {
        "question": question,
        "session_id": session_id,
        "question_id": uuid.uuid4().hex[:8],
        "type": "범위밖",
        "answer": "업로드된 엑셀 자료와 관련 없는 질문이라 답변하지 않았습니다. "
        + rejection["reason"],
        "scope": {
            "kind": rejection["kind"],
            "reason": rejection["reason"],
            "departments": depts,
            "columns": cols,
        },
        "confidence": None,
        "confidence_reason": None,
        "tool_calls": 0,
        "trace": [
            {"step": "범위 확인", "detail": rejection["reason"], "status": "warn"},
            {
                "step": "AI 호출 없음",
                "detail": "자료와 무관해 외부 AI로 아무것도 보내지 않았습니다",
                "status": "ok",
            },
        ],
        "claim": None,
        "comparison_table": None,
        "cause": None,
        "sources": None,
        "search_results": None,
        "warnings": [],
    }
