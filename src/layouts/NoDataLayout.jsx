import Card from '../components/cards/Card'

// 내용 없음: 분석된 파일에 관련 내용이 없을 때. 무엇을 찾아봤는지, 못 읽은 파일이 있는지, 다음에 할 일을 알려 준다.
export default function NoDataLayout({ data }) {
  const nd = data.no_data
  return (
    <>
      <Card className="nodata">
        <div className="nodata-head">
          <span className="nodata-icon">🔍</span>
          <div>
            <h3>관련 내용을 찾지 못했습니다</h3>
            <p>{nd.reason}</p>
            <small>{nd.coverage}</small>
          </div>
        </div>
      </Card>
      {nd.similar_names?.length > 0 && (
        <Card title="비슷한 이름은 있습니다 (같은 대상이 아닐 수 있습니다)">
          <ul className="plain">{nd.similar_names.map((t) => <li key={t}>{t}</li>)}</ul>
        </Card>
      )}
      {nd.tried.length > 0 && (
        <Card title="찾아본 곳">
          <ul className="plain">{nd.tried.map((t) => <li key={t}>{t}</li>)}</ul>
        </Card>
      )}
      {nd.not_analyzed.length > 0 && (
        <Card title="분석하지 못한 파일 (그 안에 있을 수 있습니다)">
          <ul className="plain">{nd.not_analyzed.map((t) => <li key={t}>{t}</li>)}</ul>
        </Card>
      )}
      <Card title="이렇게 해보세요">
        <ul className="plain">{nd.tips.map((t) => <li key={t}>{t}</li>)}</ul>
      </Card>
    </>
  )
}
