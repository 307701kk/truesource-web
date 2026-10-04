const LABEL = { changed: '내용이 바뀜', moved: '위치가 바뀜', deleted: '삭제됨' }

export default function SourceChange({ changes, question, onAsk }) {
  const paths = Object.keys(changes)
  if (!paths.length) return null
  return (
    <div className="check-banner mid source-change" role="alert">
      <b>△ 근거 파일이 답변 이후 바뀌었습니다</b>
      <span>
        {paths.map((p) => `${p.split('/').pop()} (${LABEL[changes[p].status]})`).join(', ')}
        {' — 이 답변은 이전 내용 기준입니다.'}
      </span>
      <button type="button" className="chip" onClick={() => onAsk(question)}>다시 질문</button>
    </div>
  )
}
