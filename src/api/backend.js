// 백엔드(FastAPI, backend/app) 호출 모음. 개발 중 /api 는 vite 프록시가 8000번 포트로 넘긴다.

export async function request(path, options) {
  let res
  try {
    res = await fetch(path, options)
  } catch {
    throw new Error('백엔드에 연결할 수 없습니다. 백엔드가 실행 중인지 확인하세요.')
  }
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = new Error(body.detail || `서버 오류 (${res.status})`)
    err.status = res.status
    throw err
  }
  return body
}

const json = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})
const qs = (params) => new URLSearchParams(Object.entries(params).filter(([, v]) => v != null)).toString()

export const login = (profile) => request('/api/login', json('POST', profile))
export const startScan = (path, company) => request('/api/scan', json('POST', { path, company }))

export const getScanStatus = () => request('/api/scan/status')
export const getCatalog = () => request('/api/catalog')

export const getAudit = () => request('/api/audit?limit=200')
export const getLlmStatus = () => request('/api/llm/status')

// 회사별 DB: 최근 질문, 용어사전, 동기화 로그
export const getHistory = (company, user) => request(`/api/history?${qs({ company, user })}`)
export const getHistoryItem = (company, id) => request(`/api/history/${id}?${qs({ company })}`)
export const deleteHistory = (company, id) => request(`/api/history/${id}?${qs({ company })}`, { method: 'DELETE' })
export const getGlossary = (company) => request(`/api/glossary?${qs({ company })}`)
export const saveGlossary = (entry) => request('/api/glossary', json('POST', entry))
export const deleteGlossary = (company, id) => request(`/api/glossary/${id}?${qs({ company })}`, { method: 'DELETE' })

// 근거 파일을 이 PC의 엑셀로 연다 (분석된 파일만 가능)
export const openFile = (path) => request('/api/open', json('POST', { path }))
