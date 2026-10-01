import AnswerCard from '../components/AnswerCard'
import CauseCard from '../components/CauseCard'
import ComparisonTable from '../components/cards/ComparisonTable'
import SourcesTable from '../components/cards/SourcesTable'

// 질문형: 답변 + 기준별 판정표 + 원인 + 출처
export default function QuestionLayout({ data }) {
  return (
    <>
      <AnswerCard answer={data.answer} />
      {data.comparison_table && <ComparisonTable rows={data.comparison_table} />}
      {data.cause && <CauseCard cause={data.cause} />}
      {data.sources && <SourcesTable sources={data.sources} />}
    </>
  )
}
