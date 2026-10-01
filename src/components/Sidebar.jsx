import { useState } from 'react'
import { DEPT_ORDER } from '../data/mockCatalog'
import { SORT_MODES } from '../constants'

// 파일 확장자로 형식 구분 (형식별 정렬용)
const ext = (name) => (name.match(/\.(\w+)/)?.[1] ?? '기타').toLowerCase()

function group(files, mode) {
  if (mode === '최신순') {
    return [['전체', [...files].sort((a, b) => b.modified.localeCompare(a.modified))]]
  }
  const key = mode === '부서별' ? (f) => f.dept : (f) => ext(f.name)
  const map = new Map()
  files.forEach((f) => map.set(key(f), [...(map.get(key(f)) ?? []), f]))
  const order = mode === '부서별' ? DEPT_ORDER : [...map.keys()]
  return order.filter((k) => map.has(k)).map((k) => [k, map.get(k)])
}

export default function Sidebar({ catalog, usedFiles }) {
  const [mode, setMode] = useState('부서별')
  const pinned = catalog.filter((f) => usedFiles.has(f.name))
  const rest = catalog.filter((f) => !usedFiles.has(f.name))

  const row = (f, isPinned) => (
    <li key={f.name} className={isPinned ? 'file pinned' : 'file'}>
      <span className={`dot ${f.fresh === 'ok' ? 'fresh' : 'stale'}`} />
      <span className="fname">{isPinned && '★ '}{f.name}</span>
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
        <button className="active">질문하기</button>
        <button>최근 질문</button>
        <button>감사 로그</button>
        <button>용어사전</button>
      </nav>

      <div className="catalog-head">카탈로그 · {catalog.length}개 파일</div>
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
    </aside>
  )
}
