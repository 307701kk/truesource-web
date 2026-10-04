import { useEffect, useState } from 'react'
import { getQueue } from '../api/backend'

// 동시에 처리할 수 있는 질문 수가 다 찼으면, 내 질문이 순서를 기다리는 중임을 알려 준다 (2초마다 확인).
export default function QueueNotice() {
  const [queue, setQueue] = useState(null)

  useEffect(() => {
    let alive = true
    const poll = () => getQueue().then((q) => alive && setQueue(q)).catch(() => {})
    poll()
    const timer = setInterval(poll, 2000)
    return () => { alive = false; clearInterval(timer) }
  }, [])

  if (!queue || queue.waiting <= 0 || queue.running < queue.limit) return null
  return (
    <div className="queue-wrap">
      <p className="queue-note" role="status">
        지금 질문 {queue.running}개가 처리 중이라 순서를 기다리고 있습니다 (대기 {queue.waiting}개)
      </p>
    </div>
  )
}
