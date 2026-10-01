import { questionResponse, verifyResponse, findResponse } from '../data/mockResponses'

// ─────────────────────────────────────────────────────────────
// 질문을 백엔드에 보내고 응답(JSON)을 돌려받는 함수.
// 화면(React)은 이 함수만 호출한다. 백엔드가 준비되면 이 파일만 바꾸면 된다.
//
// 지금은 "가짜 백엔드"다: 질문에 들어 있는 단어로 더미 응답을 골라준다.
// (진짜 백엔드에서는 이런 글자 판별을 하지 않는다. 에이전트가 쓴 도구에 따라
//  type 값이 정해져서 내려온다.)
// ─────────────────────────────────────────────────────────────

const FAKE_DELAY_MS = 500

function pickMockResponse(question) {
  if (question.includes('찾아')) return findResponse
  if (question.includes('맞아')) return verifyResponse
  return questionResponse
}

export async function askQuestion(question) {
  // ▼▼ 백엔드(FastAPI) 연결 시 이 부분을 아래 fetch 코드로 교체 ▼▼
  //
  // const res = await fetch('http://localhost:8000/api/query', {
  //   method: 'POST',
  //   headers: { 'Content-Type': 'application/json' },
  //   body: JSON.stringify({ question }),
  // })
  // if (!res.ok) throw new Error(`서버 오류 (${res.status})`)
  // return await res.json()
  //
  // ▲▲ ----------------------------------------------------- ▲▲

  await new Promise((resolve) => setTimeout(resolve, FAKE_DELAY_MS))
  return { ...pickMockResponse(question), question }
}
