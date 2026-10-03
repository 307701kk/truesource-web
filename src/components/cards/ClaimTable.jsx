import Card from './Card'

// 검증형: 주장 vs 확인 결과
export default function ClaimTable({ checks = [] }) {
  if (!checks.length) return null
  return (
    <Card title="주장 vs 확인">
      <table className="tbl">
        <thead>
          <tr><th>근거</th><th>결과</th><th>비고</th></tr>
        </thead>
        <tbody>
          {checks.map((c, i) => (
            <tr key={`${i}-${c.basis}`} className={c.flag === 'warn' ? 'warn' : ''}>
              <td>{c.basis}</td><td>{c.result}</td><td>{c.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}
