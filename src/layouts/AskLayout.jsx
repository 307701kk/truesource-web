import Card from '../components/cards/Card'

// 되묻기: 에이전트가 모호한 부분을 사용자에게 확인한다. 선택지를 누르면 그 답이 다음 질문으로 간다.
export default function AskLayout({ data, onAsk }) {
  return (
    <Card title="확인이 필요합니다">
      <p className="ask-q">{data.ask.question}</p>
      <div className="chips">
        {data.ask.options.map((o) => (
          <button key={o} type="button" className="chip" onClick={() => onAsk(o)}>{o}</button>
        ))}
      </div>
      <p className="ask-hint">직접 답하려면 위 입력창에 입력하세요.</p>
    </Card>
  )
}
