// 질문 유형 → CSS 클래스 이름 (색은 index.css 에서 정함)
export const TYPE_CLASS = {
  질문형: 'q',
  검증형: 'v',
  찾기형: 'f',
}

// 신뢰도 → CSS 클래스 이름 / 게이지 채움 각도(도)
export const CONFIDENCE_CLASS = {
  낮음: 'low',
  보통: 'mid',
  높음: 'high',
}

export const CONFIDENCE_DEGREE = {
  낮음: 120,
  보통: 240,
  높음: 324,
}

// 사이드바 정렬 방식
export const SORT_MODES = ['부서별', '최신순', '형식별']

// 입력창 아래 예시 질문 (유형별 1개씩)
export const EXAMPLE_QUESTIONS = [
  '3분기 실적 3등이 무슨 팀이야?',
  '보고서에 영업2팀이 3위라던데 맞아?',
  '대성기계 관련 자료 찾아줘',
]
