import { useEffect, useMemo, useState } from 'react'
import Sidebar from './components/Sidebar'
import QuestionBar from './components/QuestionBar'
import ResultView from './components/ResultView'
import { askQuestion } from './api/askQuestion'
import SetupScreen from './components/SetupScreen'
import AuditView from './components/AuditView'
import HistoryView from './components/HistoryView'
import { OpenFileProvider } from './components/OpenFile'
import Loading3D from './components/Loading3D'
import GlossaryView from './components/GlossaryView'
import { getCatalog, getLlmCheck, login } from './api/backend'

// 프로필·폴더 경로는 브라우저에 기억한다 (DB는 백엔드 메모리라 재시작하면 다시 분석해야 함)
const STORE_KEY = 'truesource.setup'
const loadSaved = () => {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) ?? {} } catch { return {} }
}
const save = (v) => {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(v)) } catch { /* 저장 실패는 무시 */ }
}

export default function App() {
  const saved = useMemo(() => loadSaved(), [])
  // setup: { profile, path, catalog } — 없으면 시작 화면
  const [setup, setSetup] = useState(null)
  const [booting, setBooting] = useState(Boolean(saved.profile))
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [sessionId, setSessionId] = useState(null) // 대화 맥락("그럼 2위는?")을 잇는 세션
  const [view, setView] = useState('ask') // ask | audit
  const [keyProblem, setKeyProblem] = useState(null) // Gemini 키 점검 결과가 나쁠 때의 안내 문구

  // 새로고침해도 백엔드가 이미 분석을 끝낸 상태면 시작 화면을 건너뛴다
  useEffect(() => {
    if (!saved.profile) return
    login(saved.profile)
      .then(() => getCatalog())
      .then((c) => {
        // 이 회사의 폴더가 이미 분석돼 있을 때만 바로 입장 (다른 회사 데이터면 시작 화면)
        if (c.state === 'done' && c.total > 0 && c.company === saved.profile.company) {
          setSetup({ profile: saved.profile, path: c.scanned_path, catalog: c })
        }
      })
      .catch(() => {})
      .finally(() => setBooting(false))
  }, [saved])

  useEffect(() => {
    checkKey(false)
  }, [])

  // 백엔드가 주기적으로 폴더 변경을 반영하므로(추가·수정·삭제), 화면의 카탈로그도 주기적으로 새로고침한다
  useEffect(() => {
    if (!setup) return undefined
    const timer = setInterval(() => {
      getCatalog().then((c) => {
        if (c.company === setup.profile.company && c.total > 0) setSetup((cur) => (cur ? { ...cur, catalog: c } : cur))
      }).catch(() => {})
    }, 30000)
    return () => clearInterval(timer)
  }, [setup?.profile.company]) // eslint-disable-line

  // 키가 실제로 쓸 수 있는지 점검한다. 키 오류면 화면 위에 눈에 띄게 알린다.
  function checkKey(force) {
    getLlmCheck(force)
      .then((r) => setKeyProblem(r.ok ? null : r.message))
      .catch(() => {})
  }

  function handleSetupDone(profile, path, catalog) {
    save({ profile, path })
    setSetup({ profile, path, catalog })
    setResult(null)
    setSessionId(null)
  }

  async function handleAsk(question) {
    setLoading(true)
    setError(null)
    try {
      const res = await askQuestion(question, { user: setup.profile, sessionId })
      setSessionId(res.session_id)
      setResult(res)
    } catch (e) {
      setError(e.message || '알 수 없는 오류')
      if (e.message?.startsWith('API 키 오류')) checkKey(true)
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
    <OpenFileProvider>
    <div className="app">
      <Sidebar
        catalog={setup.catalog.files}
        sync={setup.catalog.sync}
        usedFiles={usedFiles}
        profile={setup.profile}
        onChangeFolder={() => setSetup(null)}
        view={view}
        onNav={setView}
      />
      <main className="main">
        {keyProblem && (
          <div className="notice key-error" role="alert">
            <b>⚠ {keyProblem.startsWith('API 키 오류') ? '' : 'AI 연결 문제: '}{keyProblem}</b>
            <button onClick={() => checkKey(true)}>다시 확인</button>
          </div>
        )}
        {view === 'audit' && <div className="content"><AuditView /></div>}
        {view === 'history' && (
          <div className="content">
            <HistoryView profile={setup.profile} onOpen={(res) => { setResult(res); setError(null); setView('ask') }} />
          </div>
        )}
        {view === 'glossary' && <div className="content"><GlossaryView profile={setup.profile} /></div>}
        {view === 'ask' && (<>
        <QuestionBar onAsk={handleAsk} loading={loading} />
        <div className="content">
          {loading && <Loading3D />}
          {error && <div className="state error">오류: {error}</div>}
          {!loading && !error && !result && (
            <div className="state">질문을 입력하거나 아래 예시를 눌러보세요.</div>
          )}
          {!loading && !error && result && <ResultView data={result} onAsk={handleAsk} />}
        </div>
        </>)}
      </main>
    </div>
    </OpenFileProvider>
  )
}
