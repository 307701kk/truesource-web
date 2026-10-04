import { TYPE_CLASS } from '../constants'
import QuestionLayout from '../layouts/QuestionLayout'
import VerifyLayout from '../layouts/VerifyLayout'
import FindLayout from '../layouts/FindLayout'
import AskLayout from '../layouts/AskLayout'
import NoDataLayout from '../layouts/NoDataLayout'
import ScopeLayout from '../layouts/ScopeLayout'
import SourceChange from './SourceChange'
import { useSourceChanges } from './useSourceChanges'
import { CheckBanner, WarningsCard } from './CheckPanel'
import ConfidenceGauge from './cards/ConfidenceGauge'
import TraceList from './cards/TraceList'

// type 값 하나로 왼쪽(메인) 레이아웃을 고른다. 오른쪽(신뢰도·과정)은 공통.
const LAYOUTS = { 질문형: QuestionLayout, 검증형: VerifyLayout, 찾기형: FindLayout, 되묻기: AskLayout, 내용없음: NoDataLayout, 범위밖: ScopeLayout }

export default function ResultView({ data, onAsk, dataVersion }) {
  const Layout = LAYOUTS[data.type] ?? QuestionLayout
  const changes = useSourceChanges(data, dataVersion)
  return (
    <div className="result">
      <header className="result-header">
        <span className={`type-badge ${TYPE_CLASS[data.type] ?? 'q'}`}>{data.type}</span>
        <h2>{data.question}</h2>
        {Object.keys(changes).length > 0 && <span className="pill warn">근거 파일 변경됨</span>}
      </header>
      <div className="result-grid">
        <div className="main-col">
          <SourceChange changes={changes} question={data.question} onAsk={onAsk} />
          <CheckBanner data={data} />
          <Layout data={data} onAsk={onAsk} />
        </div>
        <aside className="side-col">
          {data.confidence && (
            <ConfidenceGauge confidence={data.confidence} reason={data.confidence_reason} signals={data.confidence_signals} />
          )}
          <WarningsCard warnings={data.warnings} />
          <TraceList trace={data.trace} timing={data.timing} />
        </aside>
      </div>
    </div>
  )
}
