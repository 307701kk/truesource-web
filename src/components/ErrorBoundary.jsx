import { Component } from 'react'

// 화면을 그리다 예외가 나도 하얀 화면이 되지 않게 막는다. 가능하면 답변 글만이라도 보여 준다.
// 새 결과가 오면 호출하는 쪽에서 key 를 바꿔 다시 그리게 한다.
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const data = this.props.data
    return (
      <div className="card render-error" role="alert">
        <h3>결과 화면을 그리는 중 오류가 났습니다</h3>
        {data?.answer && <p className="fallback-answer">{data.answer}</p>}
        <small>{String(error.message || error)}</small>
        <button type="button" onClick={() => this.setState({ error: null })}>다시 그리기</button>
      </div>
    )
  }
}
