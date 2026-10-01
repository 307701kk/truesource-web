import Card from './cards/Card'

export default function CauseCard({ cause }) {
  return (
    <Card title="불일치 원인">
      <p>{cause}</p>
    </Card>
  )
}
