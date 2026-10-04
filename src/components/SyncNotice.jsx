// 폴더가 바뀌었을 때(자동·수동 동기화) 위쪽에 띄우는 알림: 무엇이 몇 개 바뀌었는지, 새 컬럼이 있는지
export default function SyncNotice({ notice, onGlossary, onClose }) {
  const s = notice.sync
  const parts = [['추가', s.added], ['수정', s.modified], ['삭제', s.deleted], ['이동', s.moved]].filter(([, n]) => n > 0)
  return (
    <div className="notice sync-notice" role="status">
      <span>
        {s.changed
          ? <><b>폴더가 바뀌어 자료를 새로 읽었습니다</b> (v{s.version}) — {parts.map(([k, n]) => `${k} ${n}`).join(' · ')}. 바뀐 파일은 왼쪽 목록에 표시됩니다.</>
          : <><b>변경 사항이 없습니다</b> — 방금 다시 확인했고 모든 파일이 그대로입니다 (v{s.version}).</>}
        {notice.newColumns > 0 && (
          <> 새 컬럼 {notice.newColumns}개 발견 → <button type="button" className="link" onClick={onGlossary}>용어사전에서 확인</button></>
        )}
      </span>
      <button type="button" onClick={onClose}>닫기</button>
    </div>
  )
}
