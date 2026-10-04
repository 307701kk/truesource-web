import { useEffect, useMemo, useRef, useState } from 'react'
import Sidebar from './components/Sidebar'
import QuestionBar from './components/QuestionBar'
import ResultView from './components/ResultView'
import { askQuestion } from './api/askQuestion'
import SetupScreen from './components/SetupScreen'
import AuditView from './components/AuditView'
import HistoryView from './components/HistoryView'
import { OpenFileProvider } from './components/OpenFile'
import Loading3D from './components/Loading3D'
import QueueNotice from './components/QueueNotice'
import ErrorBoundary from './components/ErrorBoundary'
import GlossaryView from './components/GlossaryView'
import SyncNotice from './components/SyncNotice'
import { getCatalog, getGlossaryCandidates, getLlmCheck, getScanStatus, login, syncNow } from './api/backend'

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
  const [syncing, setSyncing] = useState(false)
  const [syncNotice, setSyncNotice] = useState(null) // { sync, newColumns } — 폴더가 바뀌었을 때 알림
  const seenVersion = useRef(null) // 사용자가 이미 본 자료 버전 (처음 입장할 때 값으로 시작)

  // 새로고침해도 백엔드가 이미 분석을 끝낸 상태면 시작 화면을 건너뛴다
  useEffect(() => {
    if (!saved.profile) return
    login(saved.profile)
      .then(() => getCatalog())
      .then((c) => {
        // 이 회사의 폴더가 이미 분석돼 있을 때만 바로 입장 (다른 회사 데이터면 시작 화면)
        if (c.state === 'done' && c.total > 0 && c.company === saved.profile.company) {
          seenVersion.current = c.sync?.version ?? null
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
        if (c.company === setup.profile.company && c.total > 0) applyCatalog(c)
      }).catch(() => {})
    }, 30000)
    return () => clearInterval(timer)
  }, [setup?.profile.company]) // eslint-disable-line

  // 새 카탈로그를 화면에 반영하고, 자료 버전이 올라갔으면(자동·수동 동기화 모두) 변경 알림을 띄운다
  function applyCatalog(c) {
    setSetup((cur) => (cur ? { ...cur, catalog: c } : cur))
    const s = c.sync
    if (!s?.version) return
    if (seenVersion.current == null) { seenVersion.current = s.version; return }
    if (s.version > seenVersion.current) {
      seenVersion.current = s.version
      if (s.changed && !s.first_scan) {
        getGlossaryCandidates(c.company).then((r) => r.items.length).catch(() => 0)
          .then((newColumns) => setSyncNotice({ sync: s, newColumns }))
      }
    }
  }

  // "지금 동기화": 폴더를 바로 다시 읽고 끝나면 카탈로그를 갱신한다
  async function handleSync() {
    setSyncing(true)
    setError(null)
    try {
      await syncNow()
      for (let i = 0; i < 240; i += 1) {
        await new Promise((r) => setTimeout(r, 1000))
        const st = await getScanStatus()
        if (st.state !== 'running') break
      }
      const c = await getCatalog()
      applyCatalog(c)
      if (c.sync && !c.sync.changed) setSyncNotice({ sync: c.sync, newColumns: 0 })
    } catch (e) {
      setError(e.message)
    } finally {
      setSyncing(false)
    }
  }

  // 키가 실제로 쓸 수 있는지 점검한다. 키 오류면 화면 위에 눈에 띄게 알린다.
  function checkKey(force) {
    getLlmCheck(force)
      .then((r) => setKeyProblem(r.ok ? null : r.message))
      .catch(() => {})
  }

  function handleSetupDone(profile, path, catalog) {
    save({ profile, path })
    seenVersion.current = catalog?.sync?.version ?? null
    setSyncNotice(null)
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
    const paths = [
      ...(result?.sources ?? []).map((s) => s.path),
      ...(result?.search_results ?? []).map((s) => s.path),
    ]
    return new Set(paths.filter(Boolean))
  }, [result])

  // 방금 동기화로 바뀐 파일: { 경로 → added | modified | moved } (사이드바 강조용)
  const changedFiles = useMemo(() => {
    const m = new Map()
    const d = syncNotice?.sync?.detail
    if (!d) return m
    d.added?.forEach((p) => m.set(p, 'added'))
    d.modified?.forEach((p) => m.set(p, 'modified'))
    d.moved?.forEach((x) => m.set(x.to, 'moved'))
    return m
  }, [syncNotice])

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
        changed={changedFiles}
        syncing={syncing}
        onSync={handleSync}
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
        {syncNotice && <SyncNotice notice={syncNotice} onGlossary={() => setView('glossary')} onClose={() => setSyncNotice(null)} />}
        {view === 'audit' && <div className="content"><AuditView /></div>}
        {view === 'history' && (
          <div className="content">
            <HistoryView profile={setup.profile} onOpen={(res) => { setResult(res); setError(null); setView('ask') }} />
          </div>
        )}
        {view === 'glossary' && <div className="content"><GlossaryView profile={setup.profile} version={setup.catalog.sync?.version} /></div>}
        {view === 'ask' && (<>
        <QuestionBar onAsk={handleAsk} loading={loading} />
        <div className="content">
          {loading && (
            <>
              <Loading3D />
              <QueueNotice />
            </>
          )}
          {error && <div className="state error">오류: {error}</div>}
          {!loading && !error && !result && (
            <div className="state">질문을 입력하거나 아래 예시를 눌러보세요.</div>
          )}
          {!loading && !error && result && (
            <ErrorBoundary key={result.question_id} data={result}>
              <ResultView data={result} onAsk={handleAsk} dataVersion={setup.catalog.sync?.version} />
            </ErrorBoundary>
          )}
        </div>
        </>)}
      </main>
    </div>
    </OpenFileProvider>
  )
}
