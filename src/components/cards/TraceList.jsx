import Card from './Card'

// 실행 과정. status: ok = 파랑 점, warn = 빨강 점
export default function TraceList({ trace }) {
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
    </Card>
  )
}
