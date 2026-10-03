"""Agent tests. The LLM is replaced by a scripted policy that goes through the REAL gateway
(masking, audit, injection filter), so these checks cover everything except Gemini itself."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import types

from app import config
from app.agent import confidence, glossary, numbers
from app.agent.gateway import AuditLog, Gateway, GatewayError, Masker, assert_allowed_fields
from app.agent.runner import Agent
from app.agent.tools import QuestionCtx, ToolBox
from app.service import ScanService

DATASET = os.environ.get("TRUESOURCE_DATASET")
needs_data = pytest.mark.skipif(not DATASET, reason="TRUESOURCE_DATASET is not set")


# ---------------------------------------------------------------- pure rules
def test_period_parsing_defaults_to_latest_year():
    p = glossary.parse_period("3분기", 2026)
    assert (p["start"], p["end"], p["year_defaulted"]) == ("2026-07-01", "2026-09-30", True)
    assert glossary.parse_period("2025년 상반기", 2026)["end"] == "2025-06-30"
    assert glossary.parse_period("실적", 2026) is None


def test_term_grades():
    cols = {"매출실적", "공급가액", "팀명", "담당팀"}
    assert glossary.match_term("매출실적", cols)["grade"] == "정확"
    assert glossary.match_term("실적", cols)["grade"] == "동의어"
    assert glossary.match_term("매출총계", cols)["grade"] == "미등록"


def test_number_check_catches_invented_figures():
    allowed = numbers.allowed_numbers([{"a": "3.02억", "b": 302000000}], "3분기 3등?")
    assert numbers.check(["영업2팀은 3.02억입니다 (3위, 9/30 반품 S260930-R01)"], allowed) == []
    assert numbers.check(["영업2팀은 3.20억입니다"], allowed) == ["3.2"]
    assert numbers.check(["3분기 매출 12%"], allowed) == ["12"]


def _ctx(**kw):
    c = QuestionCtx(question="q")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_confidence_is_the_worst_signal():
    ok = _ctx(
        term_grades={"정확"}, queries={"q1": {}}, cross={"match": True, "rank_changed": False}
    )
    assert confidence.evaluate(ok)["level"] == "높음"
    changed = _ctx(
        term_grades={"정확"},
        queries={"q1": {}},
        cross={"match": False, "rank_changed": True},
        traced=True,
    )
    res = confidence.evaluate(changed)
    assert res["level"] == "낮음" and "원인 확인됨" in res["reason"]
    assert (
        confidence.evaluate(
            _ctx(
                term_grades={"미등록"},
                queries={"q1": {}},
                cross={"match": True, "rank_changed": False},
            )
        )["level"]
        == "낮음"
    )
    assert (
        confidence.evaluate(
            _ctx(
                term_grades={"동의어"},
                defaults_applied=["연도 기본값"],
                queries={"q1": {}},
                cross={"match": True, "rank_changed": False},
            )
        )["level"]
        == "보통"
    )
    assert (
        confidence.evaluate(
            _ctx(
                stale_used={"실적집계_최종.xlsx"},
                queries={"q1": {}},
                cross={"match": True, "rank_changed": False},
            )
        )["level"]
        == "낮음"
    )
    assert (
        confidence.evaluate(
            _ctx(
                number_check="failed",
                queries={"q1": {}},
                cross={"match": True, "rank_changed": False},
            )
        )["level"]
        == "낮음"
    )
    # no calculation -> cross-check impossible -> 보통, stated explicitly
    nocalc = confidence.evaluate(_ctx())
    assert nocalc["level"] == "보통" and "교차검증 불가" in nocalc["reason"]


def test_gateway_blocks_raw_rows_and_masks_ids():
    big = types.Content(
        role="user",
        parts=[
            types.Part.from_function_response(
                name="x", response={"result": [{"a": i} for i in range(100)]}
            )
        ],
    )
    with pytest.raises(GatewayError):
        assert_allowed_fields("plan_and_answer", [big])
    forbidden = types.Content(
        role="user", parts=[types.Part.from_function_response(name="x", response={"raw_rows": []})]
    )
    with pytest.raises(GatewayError):
        assert_allowed_fields("plan_and_answer", [forbidden])
    m = Masker()
    masked = m.mask("사업자번호 701-42-26877 연락 a@b.com")
    assert "701-42-26877" not in masked and "a@b.com" not in masked
    assert m.unmask(masked) == "사업자번호 701-42-26877 연락 a@b.com"


# ---------------------------------------------------------------- fake LLM through the real gateway
class ScriptedGateway(Gateway):
    """Real call_llm path (permission, masking, injection filter, audit); only the API call is faked."""

    def __init__(self, policy) -> None:
        super().__init__(AuditLog())
        self.policy = policy

    def configured(self) -> bool:
        return True

    def _get_client(self):
        return object()

    def _generate(self, client, contents, cfg):
        step = sum(
            1 for c in contents if c.role == "model" and any(p.function_call for p in c.parts or [])
        )
        responses: dict[str, list] = {}
        for c in contents:
            for p in c.parts or []:
                if p.function_response:
                    responses.setdefault(p.function_response.name, []).append(
                        dict(p.function_response.response)
                    )
        calls = self.policy(step, responses, contents)
        parts = [types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in calls]
        return SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=types.Content(role="model", parts=parts), finish_reason="STOP"
                )
            ]
        )


@pytest.fixture(scope="module")
def service():
    svc = ScanService()
    svc.start(str(Path(DATASET) / "공유폴더"), background=False)
    return svc


USER = {"name": "홍길동", "dept": "영업팀", "title": "대리", "company": "㈜가온산업"}


def pick(catalog: dict, file: str) -> str:
    return next(c["table"] for c in catalog["candidates"] if c["file"] == file)


def q3_policy(premature_final: bool):
    def policy(step, r, contents):
        if step == 0:
            return [("lookup_terms", {"terms": ["실적", "팀", "3분기"]})]
        if step == 1:
            return [
                ("search_catalog", {"columns": ["팀명", "매출실적"], "year": 2026}),
                ("search_catalog", {"columns": ["담당팀", "공급가액", "구분"], "year": 2026}),
            ]
        a = pick(r["search_catalog"][0], "실적집계_v2.xlsx")
        b = pick(r["search_catalog"][1], "매출원장_2026.xlsx")
        if step == 2:
            return [
                (
                    "run_query",
                    {
                        "table": a,
                        "metrics": [{"agg": "sum", "column": "매출실적"}],
                        "group_by": ["팀명"],
                        "filters": [{"column": "월", "op": "in", "value": "7월,8월,9월"}],
                    },
                ),
                (
                    "run_query",
                    {
                        "table": b,
                        "metrics": [{"agg": "sum", "column": "공급가액"}],
                        "group_by": ["담당팀"],
                        "filters": [
                            {"column": "일자", "op": "between", "value": "2026-07-01,2026-09-30"}
                        ],
                    },
                ),
            ]
        if step == 3 and premature_final:
            return [("final_answer", {"question_type": "질문형", "answer": "3위는 영업2팀입니다."})]
        if step == 3 or (step == 4 and premature_final):
            return [("cross_verify", {"query_a": "q1", "query_b": "q2"})]
        if "trace_difference" not in r:
            return [
                (
                    "trace_difference",
                    {
                        "table": b,
                        "date_column": "일자",
                        "amount_column": "공급가액",
                        "after_date": "2026-09-28",
                        "group_by": ["구분", "담당팀"],
                    },
                )
            ]
        items = {i["key"]: i for i in r["cross_verify"][0]["items"]}
        rows = [(k, i) for k, i in items.items()]
        third_a = next(k for k, i in rows if i["rank_a"] == 3)
        third_b = next(k for k, i in rows if i["rank_b"] == 3)
        return [
            (
                "final_answer",
                {
                    "question_type": "질문형",
                    "answer": f"3분기 3위는 기준에 따라 다릅니다. 집계 파일은 {third_a}, 원장은 {third_b}입니다.",
                    "comparison": [
                        {"basis": "집계 파일", "rank_team": third_a, "value": items[third_a]["a"]},
                        {
                            "basis": "매출원장",
                            "rank_team": third_b,
                            "value": items[third_b]["b"],
                            "flag": "warn",
                        },
                    ],
                    "cause": f"9/28 이후 입력 거래 {r['trace_difference'][0]['total_count']}건 합계 {r['trace_difference'][0]['total_display']}가 집계 파일에 없습니다.",
                },
            )
        ]

    return policy


@needs_data
def test_q3_scenario_end_to_end(service):
    agent = Agent(service, ScriptedGateway(q3_policy(premature_final=False)))
    out = agent.ask("3분기 실적 3등이 무슨 팀이야?", USER)
    assert out["type"] == "질문형"
    assert (
        out["confidence"] == "낮음"
        and "순위가 바뀜" in out["confidence_reason"]
        and "원인 확인됨" in out["confidence_reason"]
    )
    assert out["number_check"] == "ok"
    names = [r["rank_team"] for r in out["comparison_table"]]
    assert set(names) == {"해외영업팀", "영업2팀"}  # placeholders were restored locally
    assert [s["step"] for s in out["trace"]][:3] == ["용어 조회", "카탈로그 검색", "카탈로그 검색"]
    assert any(s["step"] == "차이 추적" for s in out["trace"])
    assert {s["file"] for s in out["sources"]} >= {"실적집계_v2.xlsx", "매출원장_2026.xlsx"}
    assert all(s["indexed"] for s in out["sources"])
    assert out["tool_calls"] <= config.MAX_TOOL_CALLS


@needs_data
def test_nothing_real_leaves_the_pc(service):
    gw = ScriptedGateway(q3_policy(premature_final=False))
    Agent(service, gw).ask("3분기 실적 3등이 무슨 팀이야?", USER)
    sent = " ".join(e["sent"] for e in gw.audit.recent(500))
    for secret in ("영업1팀", "영업2팀", "영업3팀", "해외영업팀", "홍길동"):
        assert secret not in sent
    assert "{팀" in sent and all(e["masked_count"] >= 0 for e in gw.audit.recent())


@needs_data
def test_final_answer_is_refused_before_cross_check(service):
    agent = Agent(service, ScriptedGateway(q3_policy(premature_final=True)))
    out = agent.ask("3분기 실적 3등이 무슨 팀이야?", USER)
    assert (
        out["confidence"] == "낮음" and out["comparison_table"]
    )  # it was forced to cross-check first
    assert next(s for s in out["trace"] if s["step"] == "교차검증")


@needs_data
def test_invented_number_is_rewritten_then_flagged(service):
    def policy(step, r, contents):
        base = q3_policy(False)(step, r, contents)
        if base[0][0] == "final_answer":
            base[0][1]["answer"] = "3위는 영업2팀이고 매출은 9.99억입니다."
        return base

    out = Agent(service, ScriptedGateway(policy)).ask("3분기 실적 3등이 무슨 팀이야?", USER)
    assert out["number_check"] == "failed" and out["confidence"] == "낮음"


@needs_data
def test_tool_budget_and_retry_limit(service):
    store = service.store
    ctx = QuestionCtx(question="q")
    tb = ToolBox(store, ctx)
    bad = {"table": "t_0150", "metrics": [{"agg": "sum", "column": "없는컬럼"}]}
    for _ in range(config.MAX_QUERY_RETRIES + 1):
        assert "error" in tb.run_query(**bad)
    assert ctx.anomalies.get("retry_failed")
    assert "재시도 한도" in tb.run_query(**bad)["error"]


@needs_data
def test_query_cannot_expose_row_level_data(service):
    tb = ToolBox(service.store, QuestionCtx(question="q"))
    res = tb.run_query("t_0009", [{"agg": "sum", "column": "공급가액"}], group_by=["전표번호"])
    assert "식별자" in res["error"]
    res = tb.run_query("t_0009", [{"agg": "sum", "column": "공급가액"}], group_by=["일자"])
    assert "너무 많" in res["error"]


@needs_data
def test_value_search_finds_locations_without_row_content(service):
    ctx = QuestionCtx(question="q")
    out = ToolBox(service.store, ctx).search_value("대성기계")
    assert out["locations"] and all("snippet" not in loc for loc in out["locations"])
    assert ctx.search_hits and ctx.search_hits[0]["snippet"]  # local-only, for the UI


@needs_data
def test_http_query_endpoint(service, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import main
    from app.companydb import CompanyManager

    monkeypatch.setattr(main, "companies", CompanyManager(tmp_path))
    monkeypatch.setattr(main, "service", service)
    monkeypatch.setattr(main, "agent", Agent(service, ScriptedGateway(q3_policy(False))))
    client = TestClient(main.app)
    res = client.post(
        "/api/query", json={"question": "3분기 실적 3등이 무슨 팀이야?", "user": USER}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["type"] == "질문형" and body["session_id"] and body["confidence"] == "낮음"
    assert {s["name"] for s in body["confidence_signals"]} >= {"교차검증", "매핑 확신도"}
    assert client.post("/api/query", json={"question": " ", "user": USER}).status_code == 400


def test_query_before_scan_is_refused(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    client = TestClient(main.app)
    monkeypatch.setattr(main, "service", ScanService())
    res = client.post("/api/query", json={"question": "안녕", "user": USER})
    assert res.status_code == 409 and "분석" in res.json()["detail"]
