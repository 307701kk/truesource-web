// 백엔드(FastAPI)가 돌려줄 응답의 "더미 버전".
// 형태는 확정한 API 데이터 모델(트루소스_API데이터모델.pdf)을 따른다.
// 팀명·숫자는 임시 값이라 나중에 실제 데이터셋(answer_key.json) 기준으로 교체하면 된다.
//
// 스키마에서 이번에 구체화한 부분(백엔드와 맞춰야 함):
//   - claim        : 검증형일 때만 객체, 아니면 null  → { statement, verdict, checks[] }
//   - flag         : comparison_table / claim.checks 의 행에 "warn"을 넣으면 빨간 강조
//   - search_results: 찾기형일 때만 배열, 아니면 null → { file, location, modified, snippet, relevance }

export const questionResponse = {
  type: '질문형',
  question: '3분기 실적 3등이 무슨 팀이야?',
  answer:
    '3분기 실적 3위는 기준에 따라 다릅니다. 실적집계_v2 기준으로는 영업2팀(3.02억)이지만, 매출원장 기준으로 재계산하면 영업3팀(2.96억)이 3위이고 영업2팀은 4위가 됩니다.',
  confidence: '낮음',
  confidence_reason: '교차검증 불일치로 순위가 바뀜 (원인 확인됨)',
  claim: null,
  comparison_table: [
    { basis: '실적집계_v2.xlsx (9/28)', rank_team: '영업2팀', value: '3.02억' },
    {
      basis: '매출원장_2026.xlsx (9/30)',
      rank_team: '영업3팀',
      value: '2.96억 (영업2팀 4위)',
      flag: 'warn',
    },
  ],
  cause:
    '9월 30일자 영업2팀 반품 1건(-0.08억, 전표 S260930-R01)이 9/28 수정된 집계 파일에 반영되지 않았습니다.',
  sources: [
    { file: '실적집계_v2.xlsx', sheet: '팀별실적', row: '4행', modified: '09-28 17:40' },
    { file: '매출원장_2026.xlsx', sheet: '거래내역', row: '3,412행', modified: '09-30 09:12' },
  ],
  trace: [
    { step: '용어 조회', detail: '실적→매출실적, 3분기→7/1~9/30', status: 'ok' },
    { step: '카탈로그 검색', detail: '후보 파일 3개 발견', status: 'ok' },
    { step: '쿼리 실행', detail: '팀별 3분기 집계', status: 'ok' },
    { step: '교차검증', detail: '불일치 발견 (0.08억)', status: 'warn' },
    { step: '차이 추적', detail: '반품 미반영 확인', status: 'ok' },
  ],
  search_results: null,
}

export const verifyResponse = {
  type: '검증형',
  question: '보고서에 영업2팀이 3위라던데 맞아?',
  answer: '기준 파일로는 맞지만, 최신 매출원장 기준으로는 4위로 바뀝니다.',
  confidence: '낮음',
  confidence_reason: '기준 시점에 따라 판정이 달라짐',
  claim: {
    statement: '영업2팀이 3위',
    verdict: '부분적으로 틀림',
    checks: [
      { basis: '사용자 주장 (보고서)', result: '3위', note: '출처 미상' },
      { basis: '실적집계_v2.xlsx (9/28)', result: '3위', note: '주장과 일치' },
      {
        basis: '매출원장_2026.xlsx (9/30)',
        result: '4위',
        note: '반품 반영 시 순위 하락',
        flag: 'warn',
      },
    ],
  },
  comparison_table: null,
  cause:
    '9월 30일자 영업2팀 반품 1건(-0.08억, 전표 S260930-R01)이 보고서 작성 시점(9/28 집계) 이후 발생해 반영되지 않았습니다.',
  sources: [
    { file: '실적집계_v2.xlsx', sheet: '팀별실적', row: '4행', modified: '09-28 17:40' },
    { file: '매출원장_2026.xlsx', sheet: '거래내역', row: '3,412행', modified: '09-30 09:12' },
  ],
  trace: [
    { step: '용어 조회', detail: '영업2팀·3위 매핑', status: 'ok' },
    { step: '쿼리 실행', detail: '기준 파일 값 확인', status: 'ok' },
    { step: '교차검증', detail: '원장 재계산 시 불일치', status: 'warn' },
    { step: '차이 추적', detail: '반품 시점 확인', status: 'ok' },
  ],
  search_results: null,
}

export const findResponse = {
  type: '찾기형',
  question: '대성기계 관련 자료 찾아줘',
  answer: '거래처명 값검색 결과 3건 · 관련도순',
  confidence: '높음',
  confidence_reason: '값 역색인 기반 검색 · 계산 없음 · 오류 위험 낮음',
  claim: null,
  comparison_table: null,
  cause: null,
  sources: null,
  trace: [
    { step: '용어 조회', detail: '"대성기계"를 거래처명으로 인식', status: 'ok' },
    { step: '값 검색', detail: '값 역색인에서 3개 파일 위치 확인', status: 'ok' },
  ],
  search_results: [
    {
      file: '거래처마스터.xlsx',
      location: '경영지원팀 / 기준정보',
      modified: '09-15 수정',
      snippet: '"대성기계㈜ · 거래처코드 C-0182 · 결제조건 30일 · 담당 영업2팀 …"',
      relevance: '높음',
    },
    {
      file: '계약현황_2026.xlsx',
      location: '경영지원팀 / 계약현황',
      modified: '08-31 수정',
      snippet: '"… 대성기계 3분기 납품실적 0.41억, 계약기간 2026-01~12 …"',
      relevance: '보통',
    },
    {
      file: '수주대장_2026.xlsx',
      location: '영업팀 / 9월 시트',
      modified: '09-15 수정',
      snippet: '"… 대성기계 발주 2건, 합계 0.19억 …"',
      relevance: '보통',
    },
  ],
}
