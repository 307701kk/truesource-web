import { useEffect, useState } from 'react'
import { deleteHistory, getHistory, getHistoryItem } from '../api/backend'
import { CONFIDENCE_CLASS } from '../constants'

// 최근 질문: 이 회사 DB 에 저장된 질문 목록. 누르면 저장된 답변을 그대로 다시 연다.
export default function HistoryView({ profile, onOpen }) {
  const [items, setItems] = useState(null)
  const [mineOnly, setMineOnly] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getHistory(profile.company, mineOnly ? profile.name : null)
      .then((r) => setItems(r.items))
      .catch((e) => setError(e.message))
  }, [profile, mineOnly])

  async function open(id) {
    try {
      onOpen((await getHistoryItem(profile.company, id)).response)
    } catch (e) {
      setError(e.message)
    }
  }

  async function remove(id) {
    try {
      await deleteHistory(profile.company, id)
      setItems((list) => list.filter((i) => i.id !== id))
    } catch (e) {
      setError(e.message)
    }
  }

  if (error) return <div className="state error">오류: {error}</div>
  if (!items) return <div className="state">불러오는 중…</div>

  return (
    <div className="history">
      <h2>
        최근 질문
        <label className="check">
          <input type="checkbox" checked={mineOnly} onChange={(e) => setMineOnly(e.target.checked)} /> 내 질문만
        </label>
      </h2>
      {!items.length && <div className="state">저장된 질문이 없습니다.</div>}
      {items.map((it) => (
        <div key={it.id} className="history-item">
          <button className="open" onClick={() => open(it.id)}>
            <span className="q">{it.question}</span>
            <span className="meta">
              {it.created_at.replace('T', ' ')} · {it.user_name} · {it.type}
              {it.confidence && <span className={`pill c-${CONFIDENCE_CLASS[it.confidence]}`}>{it.confidence}</span>}
            </span>
            <span className="a">{it.answer}</span>
          </button>
          <button className="del" title="삭제" onClick={() => remove(it.id)}>✕</button>
        </div>
      ))}
    </div>
  )
}
