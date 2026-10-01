// 흰 박스 + 제목. 모든 결과 카드의 공통 틀.
export default function Card({ title, children, className = '' }) {
  return (
    <section className={`card ${className}`}>
      {title && <h3 className="card-title">{title}</h3>}
      {children}
    </section>
  )
}
