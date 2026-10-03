import Card from './Card'

// 실행 과정. status: ok = 파랑 점, warn = 빨강 점
export default function TraceList({ trace = [], timing }) {
  return (
    <Card title="실행 과정">
      <ol className="trace">
        {trace.map((t, i) => (
          <li key={i}>
            <span className={`dot ${t.status}`} />
            <div><b>{t.step}</b><small>{t.detail}</small></div>
          </li>
        ))}
      </ol>
      {timing && (
        <small className="timing">
          총 {timing.total_seconds}초 · AI 호출 {timing.llm_calls}회
          {timing.waited_seconds > 0 && ` · 호출 한도로 ${timing.waited_seconds}초 대기`}
        </small>
      )}
    </Card>
  )
}
