// 백엔드(FastAPI, backend/app) 호출 모음. 개발 중 /api 는 vite 프록시가 8000번 포트로 넘긴다.

async function request(path, options) {
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

export const startScan = (path) =>
  request('/api/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })

export const getScanStatus = () => request('/api/scan/status')
export const getCatalog = () => request('/api/catalog')
