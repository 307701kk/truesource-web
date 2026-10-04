import { useEffect, useState } from 'react'
import { checkSources } from '../api/backend'

// 답변을 만들 때 쓴 파일이 그 뒤에 바뀌었는지 확인한다. 자료 버전이 바뀔 때마다 다시 확인.
export function useSourceChanges(data, version) {
  const stamps = data.source_stamps
  const key = JSON.stringify([stamps, version])
  const [found, setFound] = useState({ key: null, changes: {} })
  useEffect(() => {
    if (!stamps || !Object.keys(stamps).length) return undefined
    let alive = true
    checkSources(stamps)
      .then((r) => {
        const changes = Object.fromEntries(Object.entries(r.items).filter(([, v]) => v.status !== 'ok'))
        if (alive) setFound({ key, changes })
      })
      .catch(() => {})
    return () => { alive = false }
  }, [key]) // eslint-disable-line
  return found.key === key ? found.changes : {}
}
