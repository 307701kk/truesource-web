// 사이드바 "카탈로그"에 보여줄 파일 목록 (더미).
// 나중에는 백엔드가 폴더를 스캔한 결과(파일명·부서·수정시각)로 교체한다.
//
// fresh: 'ok' = 최신(초록 점) / 'stale' = 구버전 의심(노란 점)
// modified: 문자열 "YYYY-MM-DD HH:MM" — 최신순 정렬에 그대로 쓴다.

export const mockCatalog = [
  { name: '실적집계_v2.xlsx', dept: '영업팀', modified: '2026-09-28 17:40', fresh: 'ok' },
  { name: '실적집계_최종.xlsx', dept: '영업팀', modified: '2026-08-31 10:12', fresh: 'stale' },
  { name: '수주대장_2026.xlsx', dept: '영업팀', modified: '2026-09-15 14:03', fresh: 'ok' },
  { name: '매출원장_2026.xlsx', dept: '경영지원팀', modified: '2026-09-30 09:12', fresh: 'ok' },
  { name: '계약현황_2026.xlsx', dept: '경영지원팀', modified: '2026-08-31 16:45', fresh: 'ok' },
  { name: '거래처마스터.xlsx', dept: '경영지원팀', modified: '2026-09-15 11:20', fresh: 'ok' },
  { name: '매출장_202607.xlsx', dept: '회계팀', modified: '2026-08-05 09:30', fresh: 'ok' },
  { name: '미수금현황_202607.xlsx', dept: '회계팀', modified: '2026-08-05 09:41', fresh: 'ok' },
  { name: '재고현황_20260930.xlsx', dept: '물류팀', modified: '2026-09-30 18:00', fresh: 'ok' },
  { name: '발주대장_2026.xlsx', dept: '구매팀', modified: '2026-09-22 13:15', fresh: 'ok' },
  { name: '월간경영보고_8월.xlsx', dept: '공용', modified: '2026-09-03 10:00', fresh: 'ok' },
  { name: '실적집계_v2.xlsx(받은자료)', dept: '공용', modified: '2026-09-29 08:50', fresh: 'stale' },
]

// 부서별 정렬에서 그룹이 나오는 순서
export const DEPT_ORDER = ['영업팀', '경영지원팀', '회계팀', '물류팀', '구매팀', '공용']
