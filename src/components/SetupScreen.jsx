import { useEffect, useRef, useState } from 'react'
import { getCatalog, getScanStatus, startScan } from '../api/backend'
import { DEPT_ORDER } from '../constants'

const POLL_MS = 500

// 시작 화면. 1단계: 회사·사용자 정보 → 2단계: 공유폴더 경로 → 스캔 진행률 → 완료되면 onDone
export default function SetupScreen({ initialProfile, initialPath, onDone }) {
  const [profile, setProfile] = useState(initialProfile)
  const [step, setStep] = useState(initialProfile ? 'folder' : 'profile')

  function saveProfile(p) {
    setProfile(p)
    setStep('folder')
  }

  return (
    <div className="setup">
      <div className="setup-card">
        <div className="brand">트루소스<small>TrueSource</small></div>
        {step === 'profile' ? (
          <ProfileForm initial={profile} onSubmit={saveProfile} />
        ) : (
          <FolderForm
            profile={profile}
            initialPath={initialPath}
            onBack={() => setStep('profile')}
            onDone={(path, catalog) => onDone(profile, path, catalog)}
          />
        )}
      </div>
    </div>
  )
}

function ProfileForm({ initial, onSubmit }) {
  const [form, setForm] = useState(
    initial ?? { company: '', name: '', dept: '', title: '' },
  )
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value })
  const ready = Object.values(form).every((v) => v.trim())

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (ready) onSubmit(Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.trim()])))
      }}
    >
      <h2>사용자 정보를 입력하세요</h2>
      <p>답변 출처와 감사 로그에 &quot;누가 물었는지&quot;를 남기는 데 쓰입니다.</p>
      <label>회사명<input value={form.company} onChange={set('company')} placeholder="㈜가온산업" autoFocus /></label>
      <label>이름<input value={form.name} onChange={set('name')} placeholder="홍길동" /></label>
      <label>소속
        <input value={form.dept} onChange={set('dept')} placeholder="영업팀" list="dept-options" />
        <datalist id="dept-options">
          {DEPT_ORDER.filter((d) => d !== '공용').map((d) => <option key={d} value={d} />)}
        </datalist>
      </label>
      <label>직책<input value={form.title} onChange={set('title')} placeholder="대리" /></label>
      <button type="submit" disabled={!ready}>다음</button>
    </form>
  )
}

function FolderForm({ profile, initialPath, onBack, onDone }) {
  const [path, setPath] = useState(initialPath)
  const [status, setStatus] = useState(null) // 백엔드 스캔 상태
  const [error, setError] = useState(null)
  const timer = useRef(null)
  const running = status?.state === 'running'

  useEffect(() => () => clearTimeout(timer.current), [])

  function poll() {
    timer.current = setTimeout(async () => {
      try {
        const s = await getScanStatus()
        setStatus(s)
        if (s.state === 'running') return poll()
        if (s.state === 'error') return setError(s.message || '스캔 중 오류가 발생했습니다.')
        onDone(s.path, await getCatalog())
      } catch (e) {
        setError(e.message)
        setStatus(null)
      }
    }, POLL_MS)
  }

  async function submit(e) {
    e.preventDefault()
    if (!path.trim() || running) return
    setError(null)
    try {
      setStatus(await startScan(path.trim()))
      poll()
    } catch (err) {
      if (err.status === 409) { // 이미 스캔 중이면 그 진행을 이어서 보여 준다
        setStatus(await getScanStatus())
        poll()
      } else {
        setError(err.message)
      }
    }
  }

  return (
    <form onSubmit={submit}>
      <h2>{profile.company} 공유폴더 주소를 입력하세요</h2>
      <p>
        {profile.name} {profile.title}님, 이 폴더의 엑셀을 읽기 전용으로 분석해 DB에 저장합니다.
        원본은 수정되거나 외부로 나가지 않습니다.
      </p>
      <input
        value={path}
        onChange={(e) => setPath(e.target.value)}
        placeholder="/Volumes/NAS/공유폴더   또는   Z:\공유폴더"
        disabled={running}
        autoFocus
      />
      {error && <div className="state error">{error}</div>}
      {status && <Progress status={status} />}
      <div className="row">
        <button type="button" className="ghost" onClick={onBack} disabled={running}>이전</button>
        <button type="submit" disabled={running || !path.trim()}>
          {running ? '분석 중…' : '분석 시작'}
        </button>
      </div>
    </form>
  )
}

function Progress({ status }) {
  return (
    <div className="progress">
      <div className="bar"><span style={{ width: `${status.percent}%` }} /></div>
      <small>
        {status.files_done} / {status.files_total}개 파일 · 시트 {status.sheets} · 행 {status.data_rows.toLocaleString()}
      </small>
      {status.current_file && <small className="cur">{status.current_file}</small>}
    </div>
  )
}
