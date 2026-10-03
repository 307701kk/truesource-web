import AnswerCard from '../components/AnswerCard'
import SearchResultList from '../components/cards/SearchResultList'

// 찾기형: 한 줄 요약 + 검색 결과 카드들
export default function FindLayout({ data }) {
  return (
    <>
      <AnswerCard answer={data.answer} />
      {data.search_results?.length > 0 && <SearchResultList results={data.search_results} />}
    </>
  )
}
