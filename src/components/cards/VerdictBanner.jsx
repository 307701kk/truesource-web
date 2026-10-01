// 검증형: 맨 위 판정 배너 ("영업2팀이 3위" → 부분적으로 틀림)
export default function VerdictBanner({ claim }) {
  return (
    <div className="verdict">
      <small>검증 대상 주장</small>
      <strong>“{claim.statement}”</strong>
      <span className="verdict-badge">{claim.verdict}</span>
    </div>
  )
}
