// 찾기형: 검색 결과 카드 리스트
export default function SearchResultList({ results }) {
  return (
    <div className="results">
      {results.map((r) => (
        <article key={r.file} className="card result">
          <div className="result-head">
            <b>{r.file}</b>
            <span className={`rel ${r.relevance === '높음' ? 'high' : 'mid'}`}>관련도 {r.relevance}</span>
          </div>
          <small>{r.location} · {r.modified}</small>
          <p>{r.snippet}</p>
        </article>
      ))}
    </div>
  )
}
