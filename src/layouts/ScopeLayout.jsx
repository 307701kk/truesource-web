import Card from '../components/cards/Card'
import { EXAMPLE_QUESTIONS } from '../constants'

// 범위 밖: 올린 엑셀과 관련 없는 질문. AI 를 부르지 않고, 대신 이 폴더에서 물어볼 수 있는 것을 알려 준다.
export default function ScopeLayout({ data, onAsk }) {
  const s = data.scope
  return (
    <>
      <Card className="scope">
        <div className="nodata-head">
          <span className="nodata-icon">🚫</span>
          <div>
            <h3>이 질문은 올린 엑셀 자료와 관련이 없습니다</h3>
            <p>{s.reason}</p>
            <small>외부 AI 로는 아무것도 보내지 않았습니다.</small>
          </div>
        </div>
      </Card>
      <Card title="이 폴더에서 물어볼 수 있는 것">
        {s.departments?.length > 0 && <p className="scope-line"><b>부서·폴더</b> {s.departments.join(' · ')}</p>}
        {s.columns?.length > 0 && <p className="scope-line"><b>항목(열)</b> {s.columns.join(' · ')}</p>}
        <div className="chips">
          {EXAMPLE_QUESTIONS.map((q) => (
            <button key={q} type="button" className="chip" onClick={() => onAsk(q)}>{q}</button>
          ))}
        </div>
      </Card>
    </>
  )
}
