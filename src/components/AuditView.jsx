import { useEffect, useState } from 'react'
import { getAudit } from '../api/backend'

// 감사 로그: 외부 LLM 으로 실제로 나간 내용(마스킹 후)을 보여 준다.
export default function AuditView() {
  const [items, setItems] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getAudit().then((r) => setItems(r.items)).catch((e) => setError(e.message))
  }, [])

  if (error) return <div className="state error">오류: {error}</div>
  if (!items) return <div className="state">불러오는 중…</div>
  if (!items.length) return <div className="state">아직 외부로 나간 내용이 없습니다.</div>

  return (
    <div className="audit">
      <h2>감사 로그 <small>외부 LLM 으로 나간 내용 (이름·번호는 자리표시자로 바뀐 상태)</small></h2>
      {items.map((it) => (
        <details key={it.id} className="audit-item">
          <summary>
            <b>{it.time.replace('T', ' ')}</b> · {it.user} · {it.purpose} · {it.model}
            <span className="pill">마스킹 {it.masked_count}건</span>
            {it.blocked.length > 0 && <span className="pill warn">인젝션 차단 {it.blocked.length}건</span>}
          </summary>
          <pre>{it.sent}</pre>
        </details>
      ))}
    </div>
  )
}
