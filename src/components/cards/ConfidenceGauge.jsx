import Card from './Card'
import { CONFIDENCE_CLASS, CONFIDENCE_DEGREE } from '../../constants'

// 신뢰도 원형 게이지 (conic-gradient). 색은 CSS 클래스가 정함
export default function ConfidenceGauge({ confidence, reason }) {
  const cls = CONFIDENCE_CLASS[confidence] ?? 'mid'
  const deg = CONFIDENCE_DEGREE[confidence] ?? 240
  return (
    <Card title="신뢰도">
      <div className="gauge-wrap">
        <div className={`gauge ${cls}`} style={{ '--deg': `${deg}deg` }}>
          <span>{confidence}</span>
        </div>
        <p className="gauge-reason">{reason}</p>
      </div>
    </Card>
  )
}
