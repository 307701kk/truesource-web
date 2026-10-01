import AnswerCard from '../components/AnswerCard'
import CauseCard from '../components/CauseCard'
import VerdictBanner from '../components/cards/VerdictBanner'
import ClaimTable from '../components/cards/ClaimTable'
import SourcesTable from '../components/cards/SourcesTable'

// 검증형: 판정 배너 + 답변 + 주장 vs 확인 표 + 원인 + 출처
export default function VerifyLayout({ data }) {
  return (
    <>
      <VerdictBanner claim={data.claim} />
      <AnswerCard answer={data.answer} />
      <ClaimTable checks={data.claim.checks} />
      {data.cause && <CauseCard cause={data.cause} />}
      {data.sources && <SourcesTable sources={data.sources} />}
    </>
  )
}
