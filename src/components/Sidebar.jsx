import BrandIcon from './BrandIcon'
import { useEffect, useMemo, useState } from 'react'
import { DEPT_ORDER, SORT_MODES } from '../constants'
import { useOpenFile } from './openFileContext'

// 같은 이름의 사본·백업 파일을 구분하도록 바로 위 폴더 이름을 작게 보여 준다
const parentOf = (path) => (path?.includes('/') ? path.split('/').slice(-2, -1)[0] : '')

// 파일 확장자로 형식 구분 (형식별 정렬용)
const ext = (name) => (name.match(/\.(\w+)/)?.[1] ?? '기타').toLowerCase()

// 부서 아래 "한 겹" 하위 폴더 (영업팀/2026/실적/x.xlsx → 2026). 폴더 바로 아래 파일이면 없음.
const subOf = (path) => (path?.split('/').length > 2 ? path.split('/')[1] : null)

const CHANGE_LABEL = { added: '신규', modified: '수정', moved: '이동' }
const TREE_KEY = 'truesource.tree'
const loadOpen = () => {
  try { return new Set(JSON.parse(localStorage.getItem(TREE_KEY)) ?? []) } catch { return new Set() }
}

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

// 부서 ▸ 하위 폴더 ▸ 파일 트리 구조로 바꾼다: [{ key, name, files, subs: [{ key, name, files }] }]
function toTree(files) {
  return group(files, '부서별').map(([dept, list]) => {
    const direct = list.filter((f) => !subOf(f.path))
    const subs = new Map()
    list.filter((f) => subOf(f.path)).forEach((f) => subs.set(subOf(f.path), [...(subs.get(subOf(f.path)) ?? []), f]))
    return {
      key: dept,
      name: dept,
      files: list,
      direct,
      subs: [...subs].sort(([a], [b]) => a.localeCompare(b)).map(([name, fs]) => ({ key: `${dept}/${name}`, name, files: fs })),
    }
  })
}

const staleCount = (files) => files.filter((f) => f.fresh !== 'ok').length

export default function Sidebar({ catalog, sync, changed, syncing, onSync, usedFiles, profile, onChangeFolder, view, onNav }) {
  const [mode, setMode] = useState('부서별')
  const [open, setOpen] = useState(loadOpen)
  const openFile = useOpenFile()
  const pinned = catalog.filter((f) => usedFiles.has(f.path))
  const rest = catalog.filter((f) => !usedFiles.has(f.path))

  const toggle = (key) => setOpen((cur) => {
    const next = new Set(cur)
    if (!next.delete(key)) next.add(key)
    return next
  })
  useEffect(() => {
    try { localStorage.setItem(TREE_KEY, JSON.stringify([...open])) } catch { /* 저장 실패는 무시 */ }
  }, [open])

  // 바뀐 파일이 들어 있는 폴더는 알림이 떠 있는 동안 자동으로 펼쳐 둔다
  const forced = useMemo(() => {
    const keys = new Set()
    catalog.filter((f) => changed?.has(f.path)).forEach((f) => {
      keys.add(f.dept)
      if (subOf(f.path)) keys.add(`${f.dept}/${subOf(f.path)}`)
    })
    return keys
  }, [catalog, changed])
  const isOpen = (key) => open.has(key) || forced.has(key)

  const row = (f, isPinned, indent = 0) => (
    <li
      key={f.path ?? f.name}
      title={`${f.path} (클릭하면 엑셀로 열립니다)`}
      className={`file${isPinned ? ' pinned' : ''}${changed?.has(f.path) ? ' changed' : ''}`}
      style={indent ? { paddingLeft: 6 + indent * 14 } : undefined}
      onClick={() => f.path && openFile(f.path)}
    >
      <span className={`dot ${f.fresh === 'ok' ? 'fresh' : 'stale'}`} />
      <span className="fname">{isPinned && '★ '}{f.name}</span>
      {changed?.has(f.path) && <span className="chg">{CHANGE_LABEL[changed.get(f.path)]}</span>}
      {!indent && parentOf(f.path) && <span className="fdir">{parentOf(f.path)}</span>}
      {f.editing && <span className="lock" title={f.editing.owner ? `${f.editing.owner}님이 열어 둠` : '다른 사람이 열어 둠'}>✎</span>}
    </li>
  )

  const folderHead = (key, name, files, level) => (
    <button
      type="button"
      className={`tw-head level${level}`}
      aria-expanded={isOpen(key)}
      onClick={() => toggle(key)}
    >
      <span className={`tw-arrow${isOpen(key) ? ' open' : ''}`}>▸</span>
      <span className="tw-name">{name}</span>
      <span className="tw-count">{files.length}</span>
      {staleCount(files) > 0 && <span className="tw-stale" title="구버전·사본 파일 수">{staleCount(files)}</span>}
    </button>
  )

  const renderTree = (files) =>
    toTree(files).map((d) => (
      <div key={d.key} className="tw-dept">
        {folderHead(d.key, d.name, d.files, 0)}
        {isOpen(d.key) && (
          <div className="tw-body">
            {d.subs.map((s) => (
              <div key={s.key}>
                {folderHead(s.key, s.name, s.files, 1)}
                {isOpen(s.key) && <ul>{s.files.map((f) => row(f, false, 2))}</ul>}
              </div>
            ))}
            {d.direct.length > 0 && <ul>{d.direct.map((f) => row(f, false, 1))}</ul>}
          </div>
        )}
      </div>
    ))

  // 최신순·형식별은 예전처럼 평평하게
  const renderGroups = (files, isPinned) =>
    group(files, mode).map(([name, list]) => (
      <div key={name}>
        {mode !== '최신순' && <div className="group">{name}</div>}
        <ul>{list.map((f) => row(f, isPinned))}</ul>
      </div>
    ))

  const time = sync?.at?.slice(11, 16)

  return (
    <aside className="sidebar">
      <div className="brand"><BrandIcon size={30} tone="light" /><span>트루소스<small>TrueSource</small></span></div>
      <nav className="nav">
        <button className={view === 'ask' ? 'active' : ''} onClick={() => onNav('ask')}>질문하기</button>
        <button className={view === 'history' ? 'active' : ''} onClick={() => onNav('history')}>최근 질문</button>
        <button className={view === 'audit' ? 'active' : ''} onClick={() => onNav('audit')}>감사 로그</button>
        <button className={view === 'glossary' ? 'active' : ''} onClick={() => onNav('glossary')}>용어사전</button>
      </nav>

      <div className="catalog-head">카탈로그 · {catalog.length}개 파일</div>
      {sync && (
        <div className="sync" title="마지막 폴더 동기화 결과">
          자료 v{sync.version ?? 1}{time && ` · ${time} 동기화`}<br />
          추가 {sync.added} · 수정 {sync.modified} · 삭제 {sync.deleted}
          {sync.moved > 0 && ` · 이동 ${sync.moved}`}
        </div>
      )}
      <button type="button" className="sync-btn" disabled={syncing} onClick={onSync}>
        {syncing ? '동기화 중…' : '↻ 지금 동기화'}
      </button>
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
        {mode === '부서별' ? renderTree(rest) : renderGroups(rest, false)}
      </div>

      <div className="profile">
        <b>{profile.name} {profile.title}</b>
        <small>{profile.company} · {profile.dept}</small>
        <button onClick={onChangeFolder}>폴더 다시 지정</button>
      </div>
    </aside>
  )
}
