import { TYPE_CLASS } from '../constants'
import QuestionLayout from '../layouts/QuestionLayout'
import VerifyLayout from '../layouts/VerifyLayout'
import FindLayout from '../layouts/FindLayout'
import AskLayout from '../layouts/AskLayout'
import ConfidenceGauge from './cards/ConfidenceGauge'
import TraceList from './cards/TraceList'

// type 값 하나로 왼쪽(메인) 레이아웃을 고른다. 오른쪽(신뢰도·과정)은 공통.
const LAYOUTS = { 질문형: QuestionLayout, 검증형: VerifyLayout, 찾기형: FindLayout, 되묻기: AskLayout }

export default function ResultView({ data, onAsk }) {
  const Layout = LAYOUTS[data.type] ?? QuestionLayout
  return (
    <div className="result">
      <header className="result-header">
        <span className={`type-badge ${TYPE_CLASS[data.type] ?? 'q'}`}>{data.type}</span>
        <h2>{data.question}</h2>
      </header>
      <div className="result-grid">
        <div className="main-col"><Layout data={data} onAsk={onAsk} /></div>
        <aside className="side-col">
          {data.confidence && (
            <ConfidenceGauge confidence={data.confidence} reason={data.confidence_reason} signals={data.confidence_signals} />
          )}
          <TraceList trace={data.trace} />
        </aside>
      </div>
    </div>
  )
}
