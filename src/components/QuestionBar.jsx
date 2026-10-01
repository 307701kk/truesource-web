import { useState } from 'react'
import { EXAMPLE_QUESTIONS } from '../constants'

export default function QuestionBar({ onAsk, loading }) {
  const [text, setText] = useState('')

  function submit(q) {
    const question = q.trim()
    if (!question || loading) return
    onAsk(question)
    setText('')
  }

  return (
    <div className="qbar">
      <form onSubmit={(e) => { e.preventDefault(); submit(text) }}>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="사내 엑셀 데이터에 대해 질문하세요"
        />
        <button type="submit" disabled={loading || !text.trim()}>질문</button>
      </form>
      <div className="chips">
        {EXAMPLE_QUESTIONS.map((q) => (
          <button key={q} type="button" className="chip" onClick={() => submit(q)}>{q}</button>
        ))}
      </div>
    </div>
  )
}
