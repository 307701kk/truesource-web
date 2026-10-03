import { useEffect, useMemo, useState } from 'react'
import Sidebar from './components/Sidebar'
import QuestionBar from './components/QuestionBar'
import ResultView from './components/ResultView'
import { askQuestion } from './api/askQuestion'
import SetupScreen from './components/SetupScreen'
import { getCatalog } from './api/backend'

// 프로필·폴더 경로는 브라우저에 기억한다 (DB는 백엔드 메모리라 재시작하면 다시 분석해야 함)
const STORE_KEY = 'truesource.setup'
const loadSaved = () => {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) ?? {} } catch { return {} }
}
const save = (v) => {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(v)) } catch { /* 저장 실패는 무시 */ }
}

export default function App() {
  const saved = useMemo(loadSaved, [])
  // setup: { profile, path, catalog } — 없으면 시작 화면
  const [setup, setSetup] = useState(null)
  const [booting, setBooting] = useState(Boolean(saved.profile))
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // 새로고침해도 백엔드가 이미 분석을 끝낸 상태면 시작 화면을 건너뛴다
  useEffect(() => {
    if (!saved.profile) return
    getCatalog()
      .then((c) => {
        if (c.state === 'done' && c.total > 0) setSetup({ profile: saved.profile, path: c.scanned_path, catalog: c })
      })
      .catch(() => {})
      .finally(() => setBooting(false))
  }, [saved])

  function handleSetupDone(profile, path, catalog) {
    save({ profile, path })
    setSetup({ profile, path, catalog })
    setResult(null)
  }

  async function handleAsk(question) {
    setLoading(true)
    setError(null)
    try {
      setResult(await askQuestion(question))
    } catch (e) {
      setError(e.message || '알 수 없는 오류')
    } finally {
      setLoading(false)
    }
  }

  // 이번 답변에서 쓰인 파일 이름들 (사이드바 상단 고정용)
  const usedFiles = useMemo(() => {
    const names = [
      ...(result?.sources ?? []).map((s) => s.file),
      ...(result?.search_results ?? []).map((s) => s.file),
    ]
    return new Set(names)
  }, [result])

  if (booting) return null
  if (!setup) {
    return <SetupScreen initialProfile={saved.profile} initialPath={saved.path ?? ''} onDone={handleSetupDone} />
  }

  return (
    <div className="app">
      <Sidebar
        catalog={setup.catalog.files}
        usedFiles={usedFiles}
        profile={setup.profile}
        onChangeFolder={() => setSetup(null)}
      />
      <main className="main">
        <QuestionBar onAsk={handleAsk} loading={loading} />
        <div className="content">
          {loading && <div className="state">답변을 찾는 중…</div>}
          {error && <div className="state error">오류: {error}</div>}
          {!loading && !error && !result && (
            <div className="state">질문을 입력하거나 아래 예시를 눌러보세요.</div>
          )}
          {!loading && !error && result && <ResultView data={result} />}
        </div>
      </main>
    </div>
  )
}
