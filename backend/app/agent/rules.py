"""Confidence rule registry: every way an answer can be less trustworthy, in one place.

Each rule has a signal (which of the confidence signals it belongs to), a severity, a title and an
"action": what the person should check. Tools raise a rule with `ctx.flag(rule_id, ...)`; the
confidence module takes the worst severity per signal. Nothing here is decided by the LLM.

Severity:  낮음  = do not rely on the number before checking
           보통  = usable, but check the point
           정보  = shown, does not change the grade
"""

from __future__ import annotations

from dataclasses import dataclass

LOW, MID, INFO = "낮음", "보통", "정보"

MAPPING = "매핑 확신도"
CROSS = "교차검증"
QUALITY = "데이터 품질"
EXEC = "실행 이상"
NUMBERS = "숫자 검증"
EVIDENCE = "근거 확보"


@dataclass(frozen=True)
class Rule:
    id: str
    signal: str
    severity: str
    title: str  # may use {placeholders} filled from the finding's detail
    action: str  # what to check


def _r(id, signal, severity, title, action):  # noqa: A002
    return Rule(id, signal, severity, title, action)


RULES: dict[str, Rule] = {
    r.id: r
    for r in [
        # ---------------------------------------------------------------- ① 매핑
        _r(
            "M1",
            MAPPING,
            LOW,
            "사전에 없는 표현을 추정해 컬럼에 연결함: {terms}",
            "'{terms}'가 실제로 어떤 컬럼을 뜻하는지 확인하고, 맞다면 용어사전에 등록하세요.",
        ),
        _r(
            "M2",
            MAPPING,
            MID,
            "기본값 규칙을 적용함: {defaults}",
            "질문에 연도·기간을 적지 않아 기본값을 썼습니다. 원하는 기간이 맞는지 확인하세요.",
        ),
        _r(
            "M3",
            MAPPING,
            MID,
            "용어 조회 없이 질의함",
            "용어가 올바른 컬럼에 연결됐는지 확인되지 않았습니다. 사용한 컬럼이 질문과 맞는지 보세요.",
        ),
        # ---------------------------------------------------------------- ② 교차검증
        _r(
            "X1",
            CROSS,
            LOW,
            "계산했지만 다른 파일로 교차검증을 하지 못함",
            "다른 파일의 같은 지표와 직접 비교해 보세요. 비교할 두 번째 자료가 있는지도 확인하세요.",
        ),
        _r(
            "X2",
            CROSS,
            MID,
            "교차검증 불일치가 있으나 순위는 같음 (최대 차이 {max_rel})",
            "두 파일의 차이 항목을 확인하세요. 기준일·단위·집계 범위가 다른지 보면 됩니다.",
        ),
        _r(
            "X3",
            CROSS,
            LOW,
            "교차검증에서 큰 불일치가 있음 (최대 차이 {max_rel})",
            "같은 내용의 두 파일 값이 {max_rel} 넘게 다릅니다. 어느 쪽이 기준인지 담당자에게 확인하세요.",
        ),
        _r(
            "X4",
            CROSS,
            LOW,
            "교차검증 불일치로 순위가 바뀜",
            "기준에 따라 순위가 달라집니다. 어느 파일을 기준으로 볼지 정하고, 차이 항목의 원본 행을 열어 확인하세요.",
        ),
        _r(
            "X5",
            CROSS,
            MID,
            "한쪽 파일에만 있는 항목이 있어 비교하지 못함: {keys}",
            "같은 대상이 표기만 다르게(㈜ 유무, 띄어쓰기 등) 적혔을 수 있습니다. 항목 이름을 맞춰 보세요.",
        ),
        _r(
            "X6",
            CROSS,
            LOW,
            "두 자료의 단위가 다를 가능성이 큼 (약 {ratio}배 차이)",
            "한쪽 파일이 천원·백만원 단위일 수 있습니다. 각 파일 맨 위의 '단위' 표기를 확인하세요.",
        ),
        _r(
            "X7",
            CROSS,
            INFO,
            "비교한 두 파일의 기준일이 다름: {dates}",
            "기준일이 다르면 그 사이에 입력된 거래 때문에 값이 달라질 수 있습니다.",
        ),
        _r(
            "X8",
            CROSS,
            MID,
            "불일치의 원인을 특정하지 못함",
            "차이 항목의 행을 날짜별로 대조해 어느 쪽에 빠진 거래가 있는지 확인하세요.",
        ),
        _r(
            "X9",
            CROSS,
            MID,
            "교차검증 불가 (계산 없는 질문)",
            "계산 없이 위치만 찾은 결과입니다. 열어 본 파일의 내용을 직접 확인하세요.",
        ),
        # ---------------------------------------------------------------- ③ 데이터 품질
        _r(
            "V1",
            QUALITY,
            LOW,
            "구버전 파일을 기준으로 사용함: {file}",
            "같은 계열의 더 최신 파일({newer})이 있습니다. 그 파일과 값을 비교해 보세요.",
        ),
        _r(
            "V2",
            QUALITY,
            MID,
            "원본이 아닌 사본 파일을 사용함: {file}",
            "이 파일은 {orig}와 내용이 같은 사본입니다. 원본 위치의 파일을 기준으로 삼으세요.",
        ),
        _r(
            "V3",
            QUALITY,
            LOW,
            "같은 계열·같은 기준일({date})인데 내용이 다른 파일이 있어 기준 파일을 정할 수 없음: {others}",
            "어느 파일이 맞는 최신본인지 담당자에게 확인하고, 이름에 버전을 붙여 정리하세요.",
        ),
        _r(
            "V3F",
            QUALITY,
            MID,
            "같은 자료로 보이지만 서식(단위·열 순서)만 다른 파일이 있음: {others}",
            "어느 쪽이 정식 파일인지 정하고 나머지는 정리하세요. 단위({units})가 다르면 값이 달라 보일 수 있습니다.",
        ),
        _r(
            "V4",
            QUALITY,
            MID,
            "시트 기준일({data_date})보다 {gap}일 뒤에 수정된 파일",
            "기준일 이후에 고친 내용이 있습니다. 시트 상단의 기준일 표기가 갱신됐는지 확인하세요.",
        ),
        _r(
            "V5",
            QUALITY,
            MID,
            "백업·개인·임시 위치의 파일을 사용함: {folder}",
            "정식 부서 폴더의 파일이 아닙니다. 같은 자료의 정식 파일이 있는지 확인하세요.",
        ),
        _r(
            "V6",
            QUALITY,
            MID,
            "초안·임시·발췌 성격의 파일로 보임: {marker}",
            "파일 이름이나 제목에 '{marker}' 표시가 있습니다. 확정된 자료인지 확인하세요.",
        ),
        _r(
            "V7",
            QUALITY,
            MID,
            "{who} 이 파일을 열어 두고 있습니다{since_text}. 수정 가능성이 있습니다",
            "저장되지 않은 수정이 있을 수 있습니다. {owner}에게 저장 여부를 확인한 뒤 숫자를 사용하세요.",
        ),
        _r(
            "V8",
            QUALITY,
            LOW,
            "분석 이후 파일이 바뀜 (수정시각·크기 변화)",
            "분석한 내용이 최신이 아닙니다. 폴더를 다시 분석한 뒤 다시 질문하세요.",
        ),
        _r(
            "V9",
            QUALITY,
            LOW,
            "분석 이후 파일을 찾을 수 없음 (삭제·이동됨)",
            "파일이 이동되거나 삭제됐습니다. 폴더를 다시 분석하세요.",
        ),
        _r(
            "V10",
            QUALITY,
            LOW,
            "질문한 연도({year})와 다른 연도의 파일로 보임",
            "파일 제목·이름에 {years}가 적혀 있습니다. 올바른 연도 파일인지 확인하세요.",
        ),
        _r(
            "V11",
            QUALITY,
            MID,
            "숨겨진 시트의 값을 사용함: {sheet}",
            "숨김 처리된 시트는 작성자가 일부러 감춘 자료일 수 있습니다. 사용해도 되는 자료인지 확인하세요.",
        ),
        _r(
            "P1",
            QUALITY,
            LOW,
            "표의 날짜 범위({have})가 질문 기간({want})을 다 덮지 못함",
            "해당 기간의 데이터 일부가 파일에 없습니다. 누락된 기간의 자료를 따로 확인하세요.",
        ),
        _r(
            "P1M",
            QUALITY,
            MID,
            "표의 날짜 범위({have})가 질문 기간({want})의 끝부분을 덮지 못함 ({gap}일)",
            "기간 끝쪽의 며칠분이 파일에 없을 수 있습니다. 마지막 입력 날짜가 맞는지 확인하세요.",
        ),
        _r(
            "P2",
            QUALITY,
            MID,
            "파일 기준일({data_date})이 질문 기간 끝({end})보다 앞섬",
            "기준일 이후에 입력된 거래는 이 파일에 없을 수 있습니다. 최신 원장과 비교해 보세요.",
        ),
        _r(
            "D1",
            QUALITY,
            MID,
            "사용한 컬럼에 빈 값이 있어 계산에서 빠짐: {rows}행 ({pct})",
            "비어 있는 칸({cells})을 확인하고 채워야 할 값인지 보세요.",
        ),
        _r(
            "D1H",
            QUALITY,
            LOW,
            "사용한 컬럼의 빈 값이 많음: {rows}행 ({pct})",
            "전체의 {pct}가 비어 있어 합계가 크게 달라질 수 있습니다. 빈 칸({cells})을 확인하세요.",
        ),
        _r(
            "D2",
            QUALITY,
            MID,
            "완전히 같은 행이 중복됨: {rows}행",
            "같은 거래가 두 번 입력됐을 수 있습니다. 중복 행({cells})이 실제 별개 거래인지 확인하세요.",
        ),
        _r(
            "D2H",
            QUALITY,
            LOW,
            "중복 행이 합계에 큰 영향을 줌: {rows}행, 합계의 {pct}",
            "중복 입력이면 합계가 부풀려집니다. 중복 행({cells})을 확인하세요.",
        ),
        _r(
            "D3",
            QUALITY,
            LOW,
            "파일의 합계 행({total})이 실제 데이터 합({actual})과 다름",
            "합계가 값으로 붙여넣어져 갱신되지 않았을 수 있습니다. 합계 행({cells})을 확인하세요.",
        ),
        _r(
            "D4",
            QUALITY,
            LOW,
            "숫자와 문자가 섞인 열을 사용함: {columns}",
            "문자로 입력된 숫자는 계산에서 빠질 수 있습니다. 해당 열의 값 형식을 확인하세요.",
        ),
        _r(
            "D5",
            QUALITY,
            MID,
            "수식 오류 셀 {n}개를 빈 값으로 처리함",
            "#REF!, #DIV/0! 같은 오류 셀이 있습니다. 원본에서 오류를 고친 뒤 다시 분석하세요.",
        ),
        _r(
            "D6",
            QUALITY,
            MID,
            "계산 결과가 저장되지 않은 수식 {n}개를 빈 값으로 처리함",
            "엑셀에서 파일을 한 번 열어 저장하면 해결됩니다.",
        ),
        _r(
            "D7",
            QUALITY,
            LOW,
            "행이 너무 많아 일부만 읽음 ({limit:,}행까지)",
            "뒷부분 데이터가 계산에서 빠졌습니다. 파일을 나눠 저장하거나 행 한도를 늘리세요.",
        ),
        _r(
            "D8",
            QUALITY,
            MID,
            "부가세가 포함된 '{column}' 컬럼을 사용함",
            "사내 기준은 부가세 제외입니다. 공급가액 컬럼과 값이 맞는지 확인하세요.",
        ),
        _r(
            "D9",
            QUALITY,
            MID,
            "파일 안에 지시문처럼 보이는 문장이 있어 무시함 ({cell})",
            "누군가 일부러 넣은 문장일 수 있습니다. 해당 셀({cell})을 확인하고 지우세요.",
        ),
        # ---------------------------------------------------------------- ④ 실행
        _r(
            "E1",
            EXEC,
            LOW,
            "쿼리가 재시도 후에도 실패함",
            "필요한 컬럼이 없거나 형식이 달라 계산하지 못했습니다. 질문을 더 구체적으로 바꿔 보세요.",
        ),
        _r(
            "E2",
            EXEC,
            MID,
            "쿼리를 한 번 고쳐서 다시 실행함",
            "처음 계산이 실패해 조건을 바꿨습니다. 사용한 조건이 질문과 맞는지 확인하세요.",
        ),
        _r(
            "E3",
            EXEC,
            MID,
            "조건에 맞는 데이터가 없는 빈 결과가 있음",
            "기간·이름 조건이 데이터와 맞는지 확인하세요. 이름 표기가 다를 수 있습니다.",
        ),
        _r(
            "E4",
            EXEC,
            MID,
            "음수 합계가 나옴",
            "반품·취소가 합산됐는지 확인하세요. 의도한 값이 아니면 구분 조건을 점검하세요.",
        ),
        _r(
            "E5",
            EXEC,
            MID,
            "도구 호출 상한에 도달해 조사가 중간에 끝남",
            "더 확인할 것이 남았을 수 있습니다. 질문을 나눠서 다시 물어보세요.",
        ),
        _r(
            "E6",
            EXEC,
            LOW,
            "AI가 정해진 형식으로 답하지 못해 요약만 표시함",
            "근거 표가 없는 답변입니다. 같은 질문을 다시 해 보세요.",
        ),
        # ---------------------------------------------------------------- 숫자 · 근거
        _r(
            "N1",
            NUMBERS,
            LOW,
            "답변 속 일부 숫자가 계산값과 일치하지 않음: {numbers}",
            "표시된 숫자를 그대로 믿지 말고 출처 파일의 값과 직접 대조하세요.",
        ),
        _r(
            "S2",
            EVIDENCE,
            MID,
            "분석된 파일에서 관련 내용을 찾지 못함",
            "다른 표현으로 다시 질문하거나, 찾는 자료가 공유폴더에 있는지 확인하세요. 분석되지 않은 파일이 있다면 그 안에 있을 수 있습니다.",
        ),
        _r(
            "S1",
            EVIDENCE,
            LOW,
            "답의 근거가 되는 파일이 없음",
            "근거 없이 작성된 답입니다. 사용하지 말고 질문을 다시 해 보세요.",
        ),
    ]
}


def severity_rank(sev: str) -> int:
    return {LOW: 0, MID: 1, INFO: 2}.get(sev, 2)


def make_finding(rule_id: str, where: dict | None = None, **detail) -> dict:
    """Build a finding (what the screen shows): title, what to check, and where the proof is."""
    rule = RULES[rule_id]

    class _Safe(dict):
        def __missing__(self, key):
            return "?"

    return {
        "rule": rule.id,
        "signal": rule.signal,
        "severity": rule.severity,
        "title": rule.title.format_map(_Safe(detail)),
        "action": rule.action.format_map(_Safe(detail)),
        "where": where or None,
    }
