import { request } from './backend'

// 질문을 백엔드 에이전트(POST /api/query)에 보내고 응답(JSON)을 돌려받는다.
// 화면(React)은 이 함수만 호출한다. 응답 형태는 README 의 "응답 스키마" 참고.
export async function askQuestion(question, { user, sessionId }) {
  return request('/api/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, user, session_id: sessionId ?? null }),
  })
}
