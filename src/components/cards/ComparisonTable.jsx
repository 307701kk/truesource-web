import Card from './Card'

// 질문형: 기준 파일별 판정표. row.flag === 'warn' 이면 빨간 강조
export default function ComparisonTable({ rows }) {
  return (
    <Card title="기준별 판정">
      <table className="tbl">
        <thead>
          <tr><th>기준</th><th>순위 팀</th><th>값</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.basis} className={r.flag === 'warn' ? 'warn' : ''}>
              <td>{r.basis}</td><td>{r.rank_team}</td><td>{r.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}
