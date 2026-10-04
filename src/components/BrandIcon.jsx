// 트루소스 아이콘: 돋보기(찾기) 안에 체크(검증). 평평한 로고와, 여러 겹을 쌓아 두께를 준 입체 버전.
const lerp = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t))
const rgb = (c) => `rgb(${c.join(',')})`

// 같은 도형(돋보기 + 체크)을 색만 바꿔 그린다. viewBox 100x100.
function Shape({ lens, handle, check, glass }) {
  return (
    <>
      {glass && <circle cx="40" cy="40" r="21" fill={glass} />}
      <circle cx="40" cy="40" r="25" fill="none" stroke={lens} strokeWidth="9" />
      <line x1="58.5" y1="58.5" x2="86" y2="86" stroke={handle} strokeWidth="12" strokeLinecap="round" />
      <polyline points="29,41 37.5,50 52,31" fill="none" stroke={check} strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
    </>
  )
}

// 평평한 로고. tone: 'light' = 어두운 배경 위(사이드바), 'dark' = 밝은 배경 위(시작 화면)
export default function BrandIcon({ size = 28, tone = 'light' }) {
  const lens = tone === 'light' ? '#ffffff' : '#1B2A4A'
  return (
    <svg className="brand-icon" width={size} height={size} viewBox="0 0 100 100" aria-hidden="true">
      <Shape lens={lens} handle={lens} check="#4cc38a" />
    </svg>
  )
}

const BACK = [16, 32, 74] // 뒷면(어두운 남색)
const FRONT = [62, 114, 214] // 앞면(밝은 파랑)
const CHECK_BACK = [16, 94, 56]
const CHECK_FRONT = [92, 214, 150]

// 입체 아이콘: 얇은 판을 앞뒤로 쌓아(압출) 두께를 만든다. 회전은 CSS(.icon3d)가 맡는다.
export function Icon3D({ layers = 18, step = 1.7 }) {
  return (
    <div className="icon3d-wrap">
      <div className="icon3d">
        {Array.from({ length: layers }, (_, i) => {
          const t = i / (layers - 1)
          const front = i === layers - 1
          return (
            <svg key={i} viewBox="0 0 100 100" style={{ transform: `translateZ(${(t - 0.5) * layers * step}px)` }} aria-hidden="true">
              <Shape
                lens={rgb(lerp(BACK, FRONT, t))}
                handle={rgb(lerp(BACK, FRONT, t))}
                check={rgb(lerp(CHECK_BACK, CHECK_FRONT, t))}
                glass={front ? 'rgba(150,190,255,.28)' : null}
              />
            </svg>
          )
        })}
      </div>
    </div>
  )
}
