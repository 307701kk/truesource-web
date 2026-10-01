import Card from './cards/Card'

export default function AnswerCard({ answer }) {
  return (
    <Card title="답변" className="answer">
      <p>{answer}</p>
    </Card>
  )
}
