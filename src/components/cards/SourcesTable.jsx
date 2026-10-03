import Card from './Card'
import { FileLink } from '../OpenFile'

const FRESH = { stale: ['구버전', 'bad'], copy: ['사본', 'warn'] }

// 근거 표: 어느 파일의 어느 시트, 몇 행·어느 열을 썼는지. 파일 이름을 누르면 엑셀이 열린다.
export default function SourcesTable({ sources }) {
  return (
    <Card title="근거 (파일을 누르면 엑셀이 열립니다)">
      <div className="tbl-wrap">
        <table className="tbl sources">
          <thead>
            <tr><th>파일</th><th>시트</th><th>위치</th><th>쓰임</th><th>수정</th><th>색인</th></tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={`${s.path}-${s.sheet}-${s.row}-${s.role}`}>
                <td>
                  <FileLink path={s.path}>{s.file}</FileLink>
                  {FRESH[s.fresh] && <span className={`tag ${FRESH[s.fresh][1]}`}>{FRESH[s.fresh][0]}</span>}
                  {s.editing && (
                    <span className="tag warn" title="다른 사람이 열어 둔 파일입니다">
                      {s.editing.owner ? `${s.editing.owner}님 편집 중` : '편집 중'}
                    </span>
                  )}
                </td>
                <td>{s.sheet}</td>
                <td>
                  <b>{s.row}</b>
                  {s.cells && <small className="cells">{s.cells}</small>}
                </td>
                <td>{s.role}</td>
                <td>{s.modified}</td>
                <td>{s.indexed?.slice(5, 16).replace('T', ' ') ?? '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
