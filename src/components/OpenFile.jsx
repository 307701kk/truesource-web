import { useCallback, useEffect, useState } from 'react'
import { openFile } from '../api/backend'
import { OpenContext, useOpenFile } from './openFileContext'

// 파일 이름을 누르면 그 엑셀 파일이 이 PC에서 실제로 열린다. 결과는 위쪽 알림으로 보여 준다.
export function OpenFileProvider({ children }) {
  const [notice, setNotice] = useState(null)

  useEffect(() => {
    if (!notice) return undefined
    const timer = setTimeout(() => setNotice(null), 3500)
    return () => clearTimeout(timer)
  }, [notice])

  const open = useCallback(async (path) => {
    try {
      const res = await openFile(path)
      setNotice({ ok: true, text: `${res.opened} 을(를) 엑셀로 열었습니다.` })
    } catch (e) {
      setNotice({ ok: false, text: e.message })
    }
  }, [])

  return (
    <OpenContext.Provider value={open}>
      {children}
      {notice && <div className={`toast ${notice.ok ? 'ok' : 'err'}`}>{notice.text}</div>}
    </OpenContext.Provider>
  )
}

// 클릭하면 엑셀이 열리는 파일 이름
export function FileLink({ path, children, className = '' }) {
  const open = useOpenFile()
  return (
    <button type="button" className={`file-link ${className}`} title="클릭하면 엑셀로 열립니다" onClick={() => open(path)}>
      {children}
    </button>
  )
}
