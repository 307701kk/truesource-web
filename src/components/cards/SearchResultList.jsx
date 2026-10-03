import { FileLink } from '../OpenFile'

// 찾기형: 검색 결과 카드. 파일 이름을 누르면 엑셀이 열리고, 몇 행·어느 칸에 있는지도 보여 준다.
export default function SearchResultList({ results }) {
  return (
    <div className="results">
      {results.map((r) => (
        <article key={`${r.path}-${r.location}-${r.cell}`} className="card result">
          <div className="result-head">
            <FileLink path={r.path}>{r.file}</FileLink>
            {r.fresh === 'stale' && <span className="tag bad">구버전</span>}
            {r.fresh === 'copy' && <span className="tag warn">사본</span>}
            {r.editing && <span className="tag warn">{r.editing.owner ? `${r.editing.owner}님 편집 중` : '편집 중'}</span>}
            <span className={`rel ${r.relevance === '높음' ? 'high' : 'mid'}`}>관련도 {r.relevance}</span>
          </div>
          <small>{r.location} · {r.cell} 칸 ({r.rows}) · {r.modified}</small>
          <p>{r.snippet}</p>
        </article>
      ))}
    </div>
  )
}
