import { useState } from 'react'
import { DEPT_ORDER, SORT_MODES } from '../constants'
import { useOpenFile } from './openFileContext'

// 같은 이름의 사본·백업 파일을 구분하도록 바로 위 폴더 이름을 작게 보여 준다
const parentOf = (path) => (path?.includes('/') ? path.split('/').slice(-2, -1)[0] : '')

// 파일 확장자로 형식 구분 (형식별 정렬용)
const ext = (name) => (name.match(/\.(\w+)/)?.[1] ?? '기타').toLowerCase()

function group(files, mode) {
  if (mode === '최신순') {
    return [['전체', [...files].sort((a, b) => b.modified.localeCompare(a.modified))]]
  }
  const key = mode === '부서별' ? (f) => f.dept : (f) => ext(f.name)
  const map = new Map()
  files.forEach((f) => map.set(key(f), [...(map.get(key(f)) ?? []), f]))
  const order = mode === '부서별'
    ? [...DEPT_ORDER, ...[...map.keys()].filter((k) => !DEPT_ORDER.includes(k))]
    : [...map.keys()]
  return order.filter((k) => map.has(k)).map((k) => [k, map.get(k)])
}

export default function Sidebar({ catalog, sync, usedFiles, profile, onChangeFolder, view, onNav }) {
  const [mode, setMode] = useState('부서별')
  const open = useOpenFile()
  const pinned = catalog.filter((f) => usedFiles.has(f.path))
  const rest = catalog.filter((f) => !usedFiles.has(f.path))

  const row = (f, isPinned) => (
    <li key={f.path ?? f.name} title={`${f.path} (클릭하면 엑셀로 열립니다)`} className={isPinned ? 'file pinned' : 'file'} onClick={() => f.path && open(f.path)}>
      <span className={`dot ${f.fresh === 'ok' ? 'fresh' : 'stale'}`} />
      <span className="fname">{isPinned && '★ '}{f.name}</span>
      {parentOf(f.path) && <span className="fdir">{parentOf(f.path)}</span>}
      {f.editing && <span className="lock" title={f.editing.owner ? `${f.editing.owner}님이 열어 둠` : '다른 사람이 열어 둠'}>✎</span>}
    </li>
  )

  // 정렬 방식(mode)에 맞춰 묶어서 그린다. 출처 그룹과 일반 목록이 같은 규칙을 쓴다.
  const renderGroups = (files, isPinned) =>
    group(files, mode).map(([name, list]) => (
      <div key={name}>
        {mode !== '최신순' && <div className="group">{name}</div>}
        <ul>{list.map((f) => row(f, isPinned))}</ul>
      </div>
    ))

  return (
    <aside className="sidebar">
      <div className="brand">트루소스<small>TrueSource</small></div>
      <nav className="nav">
        <button className={view === 'ask' ? 'active' : ''} onClick={() => onNav('ask')}>질문하기</button>
        <button className={view === 'history' ? 'active' : ''} onClick={() => onNav('history')}>최근 질문</button>
        <button className={view === 'audit' ? 'active' : ''} onClick={() => onNav('audit')}>감사 로그</button>
        <button className={view === 'glossary' ? 'active' : ''} onClick={() => onNav('glossary')}>용어사전</button>
      </nav>

      <div className="catalog-head">카탈로그 · {catalog.length}개 파일</div>
      {sync && (
        <div className="sync" title="마지막 폴더 동기화 결과">
          동기화 · 추가 {sync.added} · 수정 {sync.modified} · 삭제 {sync.deleted}
          {sync.moved > 0 && ` · 이동 ${sync.moved}`}
        </div>
      )}
      <div className="tabs">
        {SORT_MODES.map((m) => (
          <button key={m} className={m === mode ? 'on' : ''} onClick={() => setMode(m)}>{m}</button>
        ))}
      </div>

      <div className="catalog">
        {pinned.length > 0 && (
          <div className="pinned-box">
            <div className="group pinned-title">이번 답변 출처</div>
            {renderGroups(pinned, true)}
          </div>
        )}
        {renderGroups(rest, false)}
      </div>

      <div className="profile">
        <b>{profile.name} {profile.title}</b>
        <small>{profile.company} · {profile.dept}</small>
        <button onClick={onChangeFolder}>폴더 다시 지정</button>
      </div>
    </aside>
  )
}
