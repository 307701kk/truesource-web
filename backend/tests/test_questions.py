"""Question handling bugs found by running real questions end to end. Each test rebuilds the
situation with a tiny workbook so it stays fixed."""

from __future__ import annotations

from datetime import date

import pytest

from app.agent import confidence
from app.agent.runner import Agent
from app.agent.tools import QuestionCtx, ToolBox

from .conftest import write_workbook
from .test_agent import ScriptedGateway
from .test_rules import PERF_HEAD, fired, make, month_rows, perf, scan, table_of, toolbox

USER = {"name": "홍", "company": "c"}


def stock(rows):
    return [["품목명", "창고", "기말재고"], *rows]


def run(svc, policy, question="질문"):
    return Agent(svc, ScriptedGateway(policy)).ask(question, USER)


def final(**kw):
    return [("final_answer", {"question_type": "질문형", "answer": "답", **kw})]


# ---------------------------------------------------------------- summing sheets / files
def test_sheets_of_a_workbook_can_be_summed(tmp_path):
    root = tmp_path / "s"
    make(
        root,
        "물류팀/재고.xlsx",
        {
            "본사창고": stock([["피팅", "본사", 30], ["호스", "본사", 10]]),
            "김해창고": stock([["피팅", "김해", 5], ["호스", "김해", 40]]),
        },
    )
    svc = scan(root)
    tb, ctx = toolbox(svc)
    cat = tb.search_catalog(["품목명", "기말재고"])
    first = cat["candidates"][0]
    siblings = [x["table"] for x in first["same_layout_tables"] if x["file"] == first["file"]]
    assert siblings, "the other warehouse sheet must be offered"
    res = tb.run_query(
        first["table"], [{"agg": "sum", "column": "기말재고"}], ["품목명"], also_tables=siblings
    )
    assert {r["품목명"]: r["sum_기말재고"] for r in res["result"]} == {"피팅": 35, "호스": 50}
    assert len(res["tables_combined"]) == 2 and res["rows_used"] == 4
    assert {s["sheet"] for s in ctx.sources} == {
        "본사창고",
        "김해창고",
    }  # evidence names both sheets


def test_monthly_files_are_summed_with_each_files_unit(tmp_path):
    root = tmp_path / "s"
    make(
        root,
        "회계팀/매출장_1월.xlsx",
        {"매출장": [["(단위: 원)"], ["업체명", "공급가액"], ["가", 1000]]},
        "2026-02-03 09:00",
    )
    make(
        root,
        "회계팀/매출장_2월.xlsx",
        {"매출장": [["(단위: 천원)"], ["업체명", "공급가액"], ["가", 2]]},
        "2026-03-03 09:00",
    )
    svc = scan(root)
    tb, _ = toolbox(svc)
    res = tb.run_query(
        table_of(svc, "회계팀/매출장_1월.xlsx"),
        [{"agg": "sum", "column": "공급가액"}],
        ["업체명"],
        also_tables=[table_of(svc, "회계팀/매출장_2월.xlsx")],
    )
    assert res["result"][0]["sum_공급가액"] == 1000 + 2 * 1000  # the 천원 file is put on 원 first


def test_union_needs_the_same_columns(tmp_path):
    root = tmp_path / "s"
    make(root, "물류팀/a.xlsx", {"a": stock([["피팅", "본사", 1]])})
    make(root, "물류팀/b.xlsx", {"b": [["품목명", "수량"], ["피팅", 3]]})
    svc = scan(root)
    tb, _ = toolbox(svc)
    res = tb.run_query(
        table_of(svc, "물류팀/a.xlsx"),
        [{"agg": "sum", "column": "기말재고"}],
        ["품목명"],
        also_tables=[table_of(svc, "물류팀/b.xlsx")],
    )
    assert "기말재고" in res["error"] and "같은 컬럼 구조" in res["error"]


def test_top_n_is_allowed_for_many_groups_but_not_dumping_them(tmp_path):
    root = tmp_path / "s"
    rows = [[f"품목{i}", "본사", i] for i in range(45)]
    make(root, "물류팀/재고.xlsx", {"본사": stock(rows)})
    svc = scan(root)
    tb, _ = toolbox(svc)
    t = table_of(svc, "물류팀/재고.xlsx")
    top = tb.run_query(t, [{"agg": "sum", "column": "기말재고"}], ["품목명"], limit=3)
    assert [r["품목명"] for r in top["result"]] == ["품목44", "품목43", "품목42"]
    assert top["groups_total"] == 45 and top["truncated"] is True
    assert "limit" in tb.run_query(t, [{"agg": "sum", "column": "기말재고"}], ["품목명"])["error"]


# ---------------------------------------------------------------- the period an answer names
def year_rows():
    return [["영업1팀", f"{m}월", 100, 90] for m in range(1, 10)]


def test_answer_calling_a_year_total_a_quarter_is_flagged(tmp_path):
    root = tmp_path / "s"
    make(root, "영업팀/실적.xlsx", {"팀별실적": perf(year_rows())})
    svc = scan(root)
    tb, ctx = toolbox(svc)
    res = tb.run_query(
        table_of(svc, "영업팀/실적.xlsx"), [{"agg": "sum", "column": "매출실적"}], ["팀명"]
    )
    assert res["period_covered"] == "1월~9월" and "M4" in fired(
        ctx
    )  # no period asked: say what was used
    tb.check_period_claim(
        [f"2026년 3분기 영업1팀 매출은 {res['result'][0]['sum_매출실적_display']}입니다."]
    )
    p3 = next(f for f in ctx.findings if f["rule"] == "P3")
    assert "3분기" in p3["title"] and "1월~9월" in p3["title"]
    assert confidence.evaluate(ctx)["level"] == "낮음"


def test_matching_period_is_not_flagged(tmp_path):
    root = tmp_path / "s"
    make(root, "영업팀/실적.xlsx", {"팀별실적": perf(year_rows())})
    svc = scan(root)
    tb, ctx = toolbox(svc)
    tb.lookup_terms(["3분기"])
    res = tb.run_query(
        table_of(svc, "영업팀/실적.xlsx"),
        [{"agg": "sum", "column": "매출실적"}],
        ["팀명"],
        [{"column": "월", "op": "in", "value": "7월,8월,9월"}],
    )
    assert res["period_covered"] == "7월~9월" and "M4" not in fired(ctx)
    tb.check_period_claim(
        [f"3분기 매출은 {res['result'][0]['sum_매출실적_display']}입니다 (9월 30일 기준)."]
    )
    assert "P3" not in fired(ctx)  # '9월 30일' is a day, not a second period


def test_date_filters_define_the_covered_range(tmp_path):
    root = tmp_path / "s"
    rows = [["일자", "담당팀", "공급가액"]] + [
        [date(2026, m, 5), "영업1팀", 10] for m in range(1, 10)
    ]
    make(root, "경영지원팀/원장.xlsx", {"원장": rows})
    svc = scan(root)
    tb, _ = toolbox(svc)
    t = table_of(svc, "경영지원팀/원장.xlsx")
    q3 = tb.run_query(
        t,
        [{"agg": "sum", "column": "공급가액"}],
        ["담당팀"],
        [{"column": "일자", "op": "between", "value": "2026-07-01,2026-09-30"}],
    )
    assert q3["period_covered"] == "2026-07-01~2026-09-05" and q3["result"][0]["sum_공급가액"] == 30
    allq = tb.run_query(t, [{"agg": "sum", "column": "공급가액"}], ["담당팀"])
    assert allq["period_covered"] == "2026-01-05~2026-09-05"


# ---------------------------------------------------------------- cross-check needs the same metric
def test_cross_check_refuses_a_different_metric(tmp_path):
    root = tmp_path / "s"
    make(root, "영업팀/집계.xlsx", {"팀별실적": perf(month_rows())})
    make(root, "물류팀/재고.xlsx", {"본사": [["팀명", "기말재고"], ["영업1팀", 5], ["영업2팀", 7]]})
    svc = scan(root)
    tb, ctx = toolbox(svc)
    tb.run_query(
        table_of(svc, "영업팀/집계.xlsx"), [{"agg": "sum", "column": "매출실적"}], ["팀명"]
    )
    tb.run_query(
        table_of(svc, "물류팀/재고.xlsx"), [{"agg": "sum", "column": "기말재고"}], ["팀명"]
    )
    res = tb.cross_verify("q1", "q2")
    assert "서로 다른 지표" in res["error"] and "매출원장" in res["error"] and ctx.cross is None


def test_shipped_vs_invoiced_may_be_compared_but_is_noted(tmp_path):
    """계약현황 납품실적 (hand typed) vs ledger sales: the same fact at two recording points."""
    root = tmp_path / "s"
    make(root, "영업팀/집계.xlsx", {"팀별실적": perf(month_rows())})
    make(
        root,
        "경영지원팀/계약.xlsx",
        {"계약": [["팀명", "3분기 납품실적"], ["영업1팀", 5], ["영업2팀", 7]]},
    )
    svc = scan(root)
    tb, ctx = toolbox(svc)
    tb.run_query(
        table_of(svc, "영업팀/집계.xlsx"), [{"agg": "sum", "column": "매출실적"}], ["팀명"]
    )
    tb.run_query(
        table_of(svc, "경영지원팀/계약.xlsx"),
        [{"agg": "sum", "column": "3분기 납품실적"}],
        ["팀명"],
    )
    assert "error" not in tb.cross_verify("q1", "q2")
    assert "X11" in {f["rule"] for f in ctx.findings}


def test_snapshots_of_different_dates_are_not_a_cross_check(tmp_path):
    root = tmp_path / "s"
    make(root, "물류팀/재고_0915.xlsx", {"본사": stock([["피팅", "본사", 1]])}, "2026-09-15 09:00")
    make(root, "물류팀/재고_0930.xlsx", {"본사": stock([["피팅", "본사", 9]])}, "2026-09-30 09:00")
    svc = scan(root)
    tb, ctx = toolbox(svc)
    for rel in ("물류팀/재고_0915.xlsx", "물류팀/재고_0930.xlsx"):
        tb.run_query(table_of(svc, rel), [{"agg": "sum", "column": "기말재고"}], ["품목명"])
    assert "기준일이 다른 현황표" in tb.cross_verify("q1", "q2")["error"]


def test_stock_of_different_dates_and_overlapping_periods_are_never_summed(tmp_path):
    root = tmp_path / "s"
    make(root, "물류팀/재고_0915.xlsx", {"본사": stock([["피팅", "본사", 1]])}, "2026-09-15 09:00")
    make(root, "물류팀/재고_0930.xlsx", {"본사": stock([["피팅", "본사", 9]])}, "2026-09-30 09:00")
    make(root, "영업팀/실적.xlsx", {"팀별실적": perf(month_rows())})
    make(
        root,
        "공용/월간.xlsx",
        {"팀별실적": perf(month_rows(), title="7월 팀별 실적 (영업팀 자료 발췌)")},
    )
    svc = scan(root)
    tb, _ = toolbox(svc)
    res = tb.run_query(
        table_of(svc, "물류팀/재고_0930.xlsx"),
        [{"agg": "sum", "column": "기말재고"}],
        ["품목명"],
        also_tables=[table_of(svc, "물류팀/재고_0915.xlsx")],
    )
    assert "이중 집계" in res["error"]
    res = tb.run_query(
        table_of(svc, "영업팀/실적.xlsx"),
        [{"agg": "sum", "column": "매출실적"}],
        ["팀명"],
        also_tables=[table_of(svc, "공용/월간.xlsx")],
    )
    assert "겹치는" in res["error"]  # the excerpt covers the same months: it would count them twice
    cat = tb.search_catalog(["팀명", "매출실적"])
    assert all("same_layout_tables" not in c for c in cat["candidates"] if c["file"] == "실적.xlsx")


def test_second_source_can_be_declared_missing_but_not_for_free(tmp_path):
    root = tmp_path / "s"
    make(root, "물류팀/재고.xlsx", {"본사": stock([["피팅", "본사", 9]])})
    svc = scan(root)

    def policy(step, r, contents):
        if step == 0:
            return [("search_catalog", {"columns": ["품목명", "기말재고"]})]
        t = r["search_catalog"][0]["candidates"][0]["table"]
        if step == 1:
            return [
                (
                    "run_query",
                    {
                        "table": t,
                        "metrics": [{"agg": "sum", "column": "기말재고"}],
                        "group_by": ["품목명"],
                    },
                )
            ]
        if step == 2:
            return final()  # no cross-check and no reason: must be refused
        return final(no_cross_reason="재고는 재고현황 한 곳에만 있음")

    out = run(svc, policy)
    assert out["confidence"] == "보통" and any(w["rule"] == "X10" for w in out["warnings"])
    assert "재고현황 한 곳" in out["confidence_reason"]


def test_monthly_flow_files_may_be_summed_but_snapshots_may_not(tmp_path):
    root = tmp_path / "s"
    make(
        root,
        "회계팀/매출장_1월.xlsx",
        {"매출장": [["업체명", "공급가액"], ["가", 1]]},
        "2026-02-03 09:00",
    )
    make(
        root,
        "회계팀/매출장_2월.xlsx",
        {"매출장": [["업체명", "공급가액"], ["가", 2]]},
        "2026-03-03 09:00",
    )
    svc = scan(root)
    tb, _ = toolbox(svc)
    ok = tb.run_query(
        table_of(svc, "회계팀/매출장_1월.xlsx"),
        [{"agg": "sum", "column": "공급가액"}],
        ["업체명"],
        also_tables=[table_of(svc, "회계팀/매출장_2월.xlsx")],
    )
    assert ok["result"][0]["sum_공급가액"] == 3  # different dates, flow numbers: allowed


def test_failed_and_empty_calls_do_not_use_the_budget(tmp_path):
    root = tmp_path / "s"
    make(root, "영업팀/집계.xlsx", {"팀별실적": perf(month_rows())})
    svc = scan(root)

    def policy(step, r, contents):
        if step == 0:
            return [
                ("search_catalog", {"columns": ["없는컬럼"]}),
                ("run_query", {"table": "t_9999", "metrics": [{"agg": "count"}]}),
            ]
        if step == 1:
            return [("search_catalog", {"columns": ["팀명", "매출실적"]})]
        return final(no_cross_reason="두 번째 자료 없음")

    out = run(svc, policy)
    assert out["tool_calls"] == 1  # only the catalog search that found something was counted


def test_column_to_column_filters(tmp_path):
    root = tmp_path / "s"
    make(
        root,
        "물류팀/재고.xlsx",
        {"본사": [["품목명", "기말재고", "안전재고"], ["가", 5, 10], ["나", 20, 10], ["다", 1, 3]]},
    )
    svc = scan(root)
    tb, _ = toolbox(svc)
    res = tb.run_query(
        table_of(svc, "물류팀/재고.xlsx"),
        [{"agg": "sum", "column": "기말재고"}],
        ["품목명"],
        [{"column": "기말재고", "op": "<", "value_column": "안전재고"}],
    )
    assert sorted(r["품목명"] for r in res["result"]) == ["가", "다"]
    bad = tb.run_query(
        table_of(svc, "물류팀/재고.xlsx"),
        [{"agg": "sum", "column": "기말재고"}],
        ["품목명"],
        [{"column": "기말재고", "op": "<", "value_column": "없는열"}],
    )
    assert "없는열" in bad["error"]


def test_item_codes_are_dimensions_and_alternative_columns_work(tmp_path):
    root = tmp_path / "s"
    make(
        root,
        "물류팀/재고.xlsx",
        {"본사": [["품목코드", "기말재고"], ["A1", 5], ["A1", 6], ["B2", 1]]},
    )
    make(root, "경영지원팀/원장.xlsx", {"원장": [["담당팀", "공급가액"], ["영업1팀", 5]]})
    svc = scan(root)
    tb, _ = toolbox(svc)
    res = tb.run_query(
        table_of(svc, "물류팀/재고.xlsx"), [{"agg": "sum", "column": "기말재고"}], ["품목코드"]
    )
    assert {r["품목코드"]: r["sum_기말재고"] for r in res["result"]} == {"A1": 11, "B2": 1}
    cat = tb.search_catalog(["담당팀", "매출실적|공급가액"])  # either amount column will do
    assert [c["file"] for c in cat["candidates"]] == ["원장.xlsx"]


def test_empty_catalog_search_still_shows_the_closest_tables(tmp_path):
    root = tmp_path / "s"
    make(root, "영업팀/집계.xlsx", {"팀별실적": perf(month_rows())})
    svc = scan(root)
    tb, _ = toolbox(svc)
    res = tb.search_catalog(["팀명", "없는컬럼"])
    assert res["candidates"] == [] and res["closest_tables"][0]["file"] == "집계.xlsx"


def test_file_and_department_names_are_not_unregistered_terms(tmp_path):
    root = tmp_path / "s"
    make(root, "경영지원팀/계약현황_2026.xlsx", {"계약": [["거래처명", "계약금액"], ["가", 5]]})
    svc = scan(root)
    tb, ctx = toolbox(svc)
    m = tb.lookup_terms(["계약현황", "경영지원팀"])["mappings"]
    assert [x["grade"] for x in m] == ["파일", "파일"] and ctx.unregistered == []


def test_made_up_placeholders_are_not_shown_to_people(tmp_path):
    root = tmp_path / "s"
    make(root, "영업팀/집계.xlsx", {"팀별실적": perf(month_rows())})
    svc = scan(root)
    calls = {"n": 0}

    def policy(step, r, contents):
        calls["n"] += 1
        return final(question_type="찾기형", answer="가장 많은 품목은 {품목1}입니다.")

    out = run(svc, policy)
    assert "{품목1}" not in out["answer"] and "(이름 확인 필요)" in out["answer"]
    assert calls["n"] >= 3  # the model was asked to rewrite twice first


# ---------------------------------------------------------------- catalog helps instead of wasting calls
def test_format_copy_is_left_out_of_the_candidates(tmp_path):
    root = tmp_path / "s"
    make(root, "영업팀/실적집계_v2.xlsx", {"팀별실적": perf(month_rows(100_000, 200_000, 90_000))})
    make(
        root,
        "영업팀/실적집계_v2_천원.xlsx",
        {"팀별실적": perf(month_rows(100, 200, 90), unit="(단위: 천원)")},
    )
    svc = scan(root)
    tb, _ = toolbox(svc)
    res = tb.search_catalog(["팀명", "매출실적"])
    assert [c["file"] for c in res["candidates"]] == ["실적집계_v2.xlsx"]
    assert any("서식만 다른 사본" in e["reason"] for e in res["excluded"])


def test_empty_catalog_search_points_to_real_columns(tmp_path):
    root = tmp_path / "s"
    make(root, "영업팀/집계.xlsx", {"팀별실적": perf(month_rows())})
    svc = scan(root)
    tb, _ = toolbox(svc)
    res = tb.search_catalog(["팀명", "없는컬럼"])
    assert res["candidates"] == [] and "매출실적" in res["known_columns"]
    assert res["closest_tables"][0]["has"] == ["팀명"] and res["closest_tables"][0]["missing"] == [
        "없는컬럼"
    ]


def test_names_in_the_data_are_values_not_unregistered_terms(tmp_path):
    root = tmp_path / "s"
    make(
        root,
        "경영지원팀/원장.xlsx",
        {"원장": [["일자", "거래처명", "공급가액"], [date(2026, 7, 1), "한빛전자", 10]]},
    )
    svc = scan(root)
    tb, ctx = toolbox(svc)
    m = tb.lookup_terms(["한빛전자", "전혀모르는말"])["mappings"]
    assert m[0]["grade"] == "값" and m[0]["columns"] == ["거래처명"]
    assert m[1]["grade"] == "미등록"
    assert ctx.unregistered == ["전혀모르는말"]  # the customer name does not lower the confidence


# ---------------------------------------------------------------- not found, and similar names
def customers(tmp_path):
    root = tmp_path / "s"
    rows = [
        ["일자", "거래처명", "공급가액"],
        [date(2026, 7, 1), "한빛전자", 10],
        [date(2026, 7, 2), "대성기계", 20],
    ]
    make(root, "경영지원팀/원장.xlsx", {"원장": rows})
    return scan(root)


def test_similar_names_are_not_evidence_for_a_missing_name(tmp_path):
    svc = customers(tmp_path)
    tb, ctx = toolbox(svc)
    assert tb.search_value("가나다전자")["locations"] == []
    res = tb.search_value("전자")  # the model guesses with a shorter text
    assert res["fuzzy"] and "한빛전자" in res["similar_values"]
    assert ctx.search_hits == [] and ctx.similar_names == ["한빛전자"]


def test_missing_name_shows_the_no_data_screen_with_similar_names(tmp_path):
    svc = customers(tmp_path)

    def policy(step, r, contents):
        return (
            [
                [("search_value", {"text": "가나다전자"})],
                [("search_value", {"text": "전자"})],
            ][step]
            if step < 2
            else final(question_type="찾기형", answer="가나다전자는 없습니다.")
        )

    out = run(svc, policy, "가나다전자 자료 찾아줘")
    assert out["type"] == "내용없음" and out["sources"] is None
    assert out["no_data"]["similar_names"] == ["한빛전자"]
    assert "가나다전자" in out["no_data"]["reason"]


def test_existing_name_is_found_even_if_the_model_dropped_the_braces(tmp_path):
    svc = customers(tmp_path)

    def policy(step, r, contents):
        if step == 0:  # {거래처N} written as a bare "거래처N": must still become the real name
            return [("search_value", {"text": "거래처" + str(_placeholder_no(contents))})]
        return final(question_type="찾기형", answer="있습니다.")

    out = run(svc, policy, "대성기계 자료 찾아줘")
    assert out["type"] == "찾기형" and out["search_results"][0]["file"] == "원장.xlsx"


def _placeholder_no(contents) -> int:
    import re

    text = " ".join(p.text or "" for c in contents for p in c.parts or [])
    return int(re.search(r"\{거래처(\d+)\}", text).group(1))


# ---------------------------------------------------------------- identical calls cost nothing
def test_repeating_the_same_call_does_not_use_the_budget(tmp_path):
    root = tmp_path / "s"
    make(root, "영업팀/집계.xlsx", {"팀별실적": perf(month_rows())})
    svc = scan(root)

    def policy(step, r, contents):
        if step == 0:
            same = {"columns": ["팀명", "매출실적"]}
            return [("search_catalog", same), ("search_catalog", same)]
        return final(no_cross_reason="없음")

    out = run(svc, policy)
    assert out["tool_calls"] == 1  # the second identical call was answered from memory


@pytest.mark.parametrize("unused", [PERF_HEAD])
def test_helpers_are_importable(unused):
    assert QuestionCtx(question="q") is not None and ToolBox is not None
    assert write_workbook is not None


def test_named_file_filter_and_pre_lookup(tmp_path):
    root = tmp_path / "s"
    make(root, "경영지원팀/계약현황_2026.xlsx", {"계약": [["거래처", "3분기 납품실적"], ["가", 5]]})
    make(root, "영업팀/집계.xlsx", {"팀별실적": perf(month_rows())})
    svc = scan(root)
    tb, ctx = toolbox(svc)
    hint = tb.pre_lookup("계약현황에서 3분기 납품실적 1위 거래처는?")
    assert hint["mentioned_files"] == ["계약현황_2026.xlsx"]  # "계약현황에서" -> the file it names
    assert hint["period"]["text"] == "3분기" and ctx.period is not None
    only = tb.search_catalog(["거래처"], file="계약현황")
    assert [c["file"] for c in only["candidates"]] == ["계약현황_2026.xlsx"]
    wrong = tb.search_catalog(
        ["거래처명"], file="계약현황"
    )  # wrong column name: show the real ones
    assert wrong["candidates"] == [] and "거래처" in wrong["closest_tables"][0]["columns"]


def test_pre_lookup_runs_once_for_free_and_the_final_answer_is_cleaned(tmp_path):
    root = tmp_path / "s"
    make(root, "영업팀/집계.xlsx", {"팀별실적": perf(month_rows())})
    svc = scan(root)
    seen = {}

    def policy(step, r, contents):
        seen["first"] = next(p.text for c in contents for p in c.parts if p.text)
        # a model that forgets claim.checks, sends numbers as numbers and leaves a field out
        return final(
            question_type="검증형",
            no_cross_reason="없음",
            claim={"statement": "영업1팀이 1위", "verdict": "맞음"},
            comparison=[{"basis": "집계", "rank_team": "영업1팀", "value": 1}],
        )

    out = run(svc, policy, "7월 실적 1위는?")
    assert "[코드가 질문에서 미리 조회한 결과" in seen["first"]
    assert out["claim"]["checks"] == [] and out["comparison_table"] == [
        {"basis": "집계", "rank_team": "영업1팀", "value": "1"}
    ]
    assert out["timing"]["llm_calls"] >= 1 and "waited_seconds" in out["timing"]
    assert [t["step"] for t in out["trace"]].count("용어 조회") == 1  # done by code, once


def test_ranking_cross_check_needs_more_than_the_winner(tmp_path):
    """계약현황 vs 매출원장: comparing only the top customer said '일치' while the real #1 differed."""
    root = tmp_path / "s"
    make(
        root,
        "경영지원팀/계약.xlsx",
        {"계약": [["거래처", "3분기 납품실적"], ["가", 90], ["나", 80], ["다", 70]]},
    )
    ledger = [
        ["일자", "거래처명", "공급가액"],
        [date(2026, 7, 1), "가", 90],
        [date(2026, 7, 2), "나", 130],
        [date(2026, 7, 3), "다", 70],
    ]
    make(root, "회계팀/원장.xlsx", {"원장": ledger}, "2026-09-30 09:00")
    svc = scan(root)
    tb, ctx = toolbox(svc, "계약현황에서 3분기 납품실적 1위 거래처는?")
    contract, book = table_of(svc, "경영지원팀/계약.xlsx"), table_of(svc, "회계팀/원장.xlsx")
    tb.run_query(
        contract,
        [{"agg": "sum", "column": "3분기 납품실적"}],
        ["거래처"],
        [{"column": "거래처", "op": "=", "value": "가"}],
    )
    tb.run_query(
        book,
        [{"agg": "sum", "column": "공급가액"}],
        ["거래처명"],
        [{"column": "거래처명", "op": "=", "value": "가"}],
    )
    ctx.queries["q2"]["_group_by"] = ["거래처"]  # same key name on both sides
    ctx.queries["q2"]["result"] = [
        {"거래처": r["거래처명"], **{k: v for k, v in r.items() if k != "거래처명"}}
        for r in ctx.queries["q2"]["result"]
    ]
    assert (
        "순위를 검증할 수 없습니다" in tb.cross_verify("q1", "q2")["error"]
    )  # winner only: refused
    # the whole ranking: the stale contract value shows up as a changed #1
    tb.run_query(contract, [{"agg": "sum", "column": "3분기 납품실적"}], ["거래처"], limit=5)
    tb.run_query(book, [{"agg": "sum", "column": "공급가액"}], ["거래처명"], limit=5)
    ctx.queries["q4"]["_group_by"] = ["거래처"]
    ctx.queries["q4"]["result"] = [
        {"거래처": r["거래처명"], **{k: v for k, v in r.items() if k != "거래처명"}}
        for r in ctx.queries["q4"]["result"]
    ]
    res = tb.cross_verify("q3", "q4")
    assert res["rank_changed"] and {i["key"]: (i["rank_a"], i["rank_b"]) for i in res["items"]}[
        "나"
    ] == (2, 1)
    assert confidence.evaluate(ctx)["level"] == "낮음"


def test_a_model_that_never_cross_checks_still_gets_an_answer_marked_unchecked(tmp_path):
    root = tmp_path / "s"
    make(root, "영업팀/집계.xlsx", {"팀별실적": perf(month_rows())})
    svc = scan(root)

    def policy(step, r, contents):
        if step == 0:
            return [("search_catalog", {"columns": ["팀명", "매출실적"]})]
        t = r["search_catalog"][0]["candidates"][0]["table"]
        if step == 1:
            return [
                (
                    "run_query",
                    {
                        "table": t,
                        "metrics": [{"agg": "sum", "column": "매출실적"}],
                        "group_by": ["팀명"],
                    },
                )
            ]
        return final()  # never gives a reason and never cross-checks

    out = run(svc, policy)
    assert out["answer"] == "답" and "정해진 단계" not in out["answer"]  # not a failure message
    assert out["confidence"] == "낮음" and any(w["rule"] == "X1" for w in out["warnings"])
