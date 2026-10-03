import { useEffect, useState } from 'react'

// 입체 로딩 애니메이션: 회전하는 유리 큐브 + 안쪽의 작은 큐브 + 궤도 링. CSS 3D 만 사용한다(라이브러리 없음).
// 질문하고 자료를 찾는 동안 화면 가운데에 크게 보여 주며, 질문 하나가 길면 2분까지 걸릴 수 있어 경과 시간을 함께 보여 준다.
const FACES = ['front', 'back', 'right', 'left', 'top', 'bottom']

export default function Loading3D({
  text = '자료를 찾는 중입니다…',
  detail = '근거 파일을 찾고 다른 파일로 교차검증하는 중입니다',
  compact = false,
}) {
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className={`loading3d${compact ? ' compact' : ''}`} role="status" aria-live="polite">
      <div className="scene-box">
      <div className="scene">
        <div className="ring ring-a" />
        <div className="ring ring-b" />
        <div className="cube outer">
          {FACES.map((f) => <span key={f} className={`face ${f}`} />)}
        </div>
        <div className="cube inner">
          {FACES.map((f) => <span key={f} className={`face ${f}`} />)}
        </div>
        <div className="shadow" />
      </div>
      </div>
      <p className="loading-text">{text}</p>
      <small>
        {detail} · {seconds}초
        {!compact && seconds >= 30 && ' (AI 응답이 느릴 수 있습니다. 최대 2분까지 기다립니다)'}
      </small>
    </div>
  )
}
