import { useEffect, useState } from 'react'
import { deleteGlossary, dismissCandidate, getGlossary, getGlossaryCandidates, saveGlossary } from '../api/backend'

const split = (text) => text.split(',').map((x) => x.trim()).filter(Boolean)

// 용어사전: 기본 용어(읽기 전용) + 우리 회사가 추가한 용어. 추가한 용어는 에이전트의 용어 조회에 바로 쓰인다.
export default function GlossaryView({ profile, version }) {
  const [data, setData] = useState(null)
  const [cands, setCands] = useState([]) // 동기화 중 새로 생긴 컬럼 (아직 용어사전에 없는 것)
  const [error, setError] = useState(null)
  const [form, setForm] = useState({ term: '', synonyms: '', columns: '', note: '' })
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  const load = () => {
    getGlossary(profile.company).then(setData).catch((e) => setError(e.message))
    getGlossaryCandidates(profile.company).then((r) => setCands(r.items)).catch(() => setCands([]))
  }
  useEffect(() => { load() }, [profile, version]) // eslint-disable-line

  // 후보를 눌러 등록: 입력칸에 미리 채워 두고 사용자가 표준용어·동의어를 고쳐서 추가한다
  const prefill = (c) => setForm({ term: c.column, synonyms: '', columns: c.column, note: '' })
  async function dismiss(c) {
    try { await dismissCandidate(profile.company, c.column); setCands((l) => l.filter((x) => x.column !== c.column)) } catch (err) { setError(err.message) }
  }

  async function submit(e) {
    e.preventDefault()
    setError(null)
    try {
      await saveGlossary({
        company: profile.company, term: form.term, synonyms: split(form.synonyms),
        columns: split(form.columns), note: form.note, by: profile.name,
      })
      setForm({ term: '', synonyms: '', columns: '', note: '' })
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function remove(id) {
    try { await deleteGlossary(profile.company, id); load() } catch (err) { setError(err.message) }
  }

  if (!data) return <div className="state">{error ? `오류: ${error}` : '불러오는 중…'}</div>
  const row = (g, id) => (
    <tr key={id ?? g.term}>
      <td><b>{g.term}</b></td><td>{g.synonyms.join(', ')}</td><td>{g.columns.join(', ')}</td><td>{g.note}</td>
      <td>{id != null && <button className="del" onClick={() => remove(id)}>삭제</button>}</td>
    </tr>
  )

  return (
    <div className="glossary">
      <h2>용어사전 <small>{profile.company}</small></h2>
      {cands.length > 0 && (
        <div className="gl-cands">
          <b>새로 생긴 컬럼 {cands.length}개</b>
          <small>폴더가 바뀐 뒤 처음 보인 항목입니다. 같은 뜻의 기존 용어가 있으면 그 용어의 컬럼 후보에 추가하세요.</small>
          <ul>
            {cands.map((c) => (
              <li key={c.column}>
                <span className="c-name">{c.column}</span>
                <span className="c-files">{c.files.map((f) => f.split('/').pop()).join(', ')}</span>
                <button type="button" onClick={() => prefill(c)}>용어로 등록</button>
                <button type="button" className="del" onClick={() => dismiss(c)}>무시</button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <form className="gl-form" onSubmit={submit}>
        <input placeholder="표준용어 (예: 수주액)" value={form.term} onChange={set('term')} />
        <input placeholder="동의어 (쉼표로 구분)" value={form.synonyms} onChange={set('synonyms')} />
        <input placeholder="컬럼 후보 (쉼표로 구분, 예: 계약금액)" value={form.columns} onChange={set('columns')} />
        <input placeholder="메모(사내 기준 등)" value={form.note} onChange={set('note')} />
        <button type="submit" disabled={!form.term.trim() || !form.columns.trim()}>추가</button>
      </form>
      {error && <div className="state error">{error}</div>}
      <table className="tbl">
        <thead><tr><th>표준용어</th><th>동의어</th><th>컬럼 후보</th><th>메모</th><th /></tr></thead>
        <tbody>
          {data.custom.map((g) => row(g, g.id))}
          {data.seed.map((g) => row(g, null))}
        </tbody>
      </table>
      <p className="gl-hint">위 {data.custom.length}개는 우리 회사가 추가한 용어, 아래는 기본 제공 용어(수정 불가)입니다.</p>
    </div>
  )
}
