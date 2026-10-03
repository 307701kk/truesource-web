import Card from './cards/Card'
import { FileLink } from './OpenFile'

const SEV = { 낮음: 'low', 보통: 'mid', 정보: 'info' }

// 신뢰도가 높음이 아닐 때: 왜 그런지, 무엇을 확인해야 하는지를 맨 위에 눈에 띄게 보여 준다.
export function CheckBanner({ data }) {
  if (!data.check_message) return null
  const low = data.confidence === '낮음'
  return (
    <div className={`check-banner ${low ? 'low' : 'mid'}`} role="alert">
      <b>{low ? '⚠ 신뢰도 낮음' : '△ 확인 권장'}</b>
      <span>{data.check_message}</span>
    </div>
  )
}

// 규칙이 발견한 모든 항목: 무엇이 문제인지, 확인할 것, 어디(파일·시트·행)인지
export function WarningsCard({ warnings }) {
  if (!warnings?.length) return null
  return (
    <Card title={`확인할 항목 ${warnings.filter((w) => w.severity !== '정보').length}건`}>
      <ul className="warnings">
        {warnings.map((w, i) => (
          <li key={`${w.rule}-${i}`} className={`w-${SEV[w.severity]}`}>
            <div className="w-head">
              <span className="sev">{w.severity}</span>
              <span className="w-title">{w.title}</span>
            </div>
            <div className="w-action">→ {w.action}</div>
            {w.where?.path && (
              <div className="w-where">
                <FileLink path={w.where.path}>{w.where.file}</FileLink>
                {w.where.sheet && ` · ${w.where.sheet}`}
                {w.where.rows && ` · ${w.where.rows}행`}
                {w.where.cells && ` · ${w.where.cells}`}
              </div>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}
