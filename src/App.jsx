import { useMemo, useState } from 'react'
import Sidebar from './components/Sidebar'
import QuestionBar from './components/QuestionBar'
import ResultView from './components/ResultView'
import { askQuestion } from './api/askQuestion'
import { mockCatalog } from './data/mockCatalog'

export default function App() {
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleAsk(question) {
    setLoading(true)
    setError(null)
    try {
      setResult(await askQuestion(question))
    } catch (e) {
      setError(e.message || '알 수 없는 오류')
    } finally {
      setLoading(false)
    }
  }

  // 이번 답변에서 쓰인 파일 이름들 (사이드바 상단 고정용)
  const usedFiles = useMemo(() => {
    const names = [
      ...(result?.sources ?? []).map((s) => s.file),
      ...(result?.search_results ?? []).map((s) => s.file),
    ]
    return new Set(names)
  }, [result])

  return (
    <div className="app">
      <Sidebar catalog={mockCatalog} usedFiles={usedFiles} />
      <main className="main">
        <QuestionBar onAsk={handleAsk} loading={loading} />
        <div className="content">
          {loading && <div className="state">답변을 찾는 중…</div>}
          {error && <div className="state error">오류: {error}</div>}
          {!loading && !error && !result && (
            <div className="state">질문을 입력하거나 아래 예시를 눌러보세요.</div>
          )}
          {!loading && !error && result && <ResultView data={result} />}
        </div>
      </main>
    </div>
  )
}
