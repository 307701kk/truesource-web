import Card from './cards/Card'
import { FileLink } from './OpenFile'

const SEV = { 낮음: 'low', 보통: 'mid', 정보: 'info' }

// 같은 규칙·같은 제목의 경고가 여러 파일에서 나오면 하나로 묶고, 해당 파일들을 나열한다.
function groupWarnings(warnings) {
  const map = new Map()
  for (const w of warnings) {
    const key = `${w.rule}|${w.title}`
    if (!map.has(key)) map.set(key, { ...w, wheres: [] })
    if (w.where?.path && !map.get(key).wheres.some((x) => x.path === w.where.path && x.rows === w.where.rows)) {
      map.get(key).wheres.push(w.where)
    }
  }
  return [...map.values()]
}

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
  const items = groupWarnings(warnings)
  return (
    <Card title={`확인할 항목 ${items.filter((w) => w.severity !== '정보').length}건`}>
      <ul className="warnings">
        {items.map((w) => (
          <li key={`${w.rule}-${w.title}`} className={`w-${SEV[w.severity]}`}>
            <div className="w-head">
              <span className="sev">{w.severity}</span>
              <span className="w-title">{w.title}</span>
            </div>
            <div className="w-action">→ {w.action}</div>
            {w.wheres.slice(0, 4).map((x) => (
              <div className="w-where" key={`${x.path}-${x.sheet}-${x.rows}`}>
                <FileLink path={x.path}>{x.file}</FileLink>
                {x.sheet && ` · ${x.sheet}`}
                {x.rows && ` · ${x.rows}행`}
                {x.cells && ` · ${x.cells}`}
              </div>
            ))}
            {w.wheres.length > 4 && <div className="w-where">… 외 {w.wheres.length - 4}곳</div>}
          </li>
        ))}
      </ul>
    </Card>
  )
}
