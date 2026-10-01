import Card from './Card'

// 출처 표 (파일·시트·행·수정시각)
export default function SourcesTable({ sources }) {
  return (
    <Card title="출처">
      <table className="tbl">
        <thead>
          <tr><th>파일</th><th>시트</th><th>위치</th><th>수정</th></tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr key={s.file}>
              <td>{s.file}</td><td>{s.sheet}</td><td>{s.row}</td><td>{s.modified}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}
