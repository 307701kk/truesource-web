"""Scope gate (out-of-scope questions are refused before any LLM call) and the sync additions:
manual sync, data version, new-column glossary candidates, changed-source check."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import scope
from app.companydb import CompanyManager
from app.service import ScanService

from .conftest import set_mtime, write_workbook

USER = {"company": "테스트상사", "name": "홍길동", "dept": "영업팀", "title": "대리"}


@pytest.fixture
def env(tmp_path, monkeypatch, share):
    svc = ScanService()
    mgr = CompanyManager(tmp_path / "data")
    svc.on_complete = lambda c, p, fp, sh, rows, **kw: mgr.open(c).record_scan(
        p, fp, sh, rows, kw.get("columns")
    )
    monkeypatch.setattr(main, "service", svc)
    monkeypatch.setattr(main, "companies", mgr)
    client = TestClient(main.app)
    client.post("/api/scan", json={"path": str(share), "company": USER["company"]})
    svc.wait(30)
    return client, svc, share


def _rescan(client, svc):
    assert client.post("/api/sync").status_code == 200
    svc.wait(30)
    return svc.sync


# ------------------------------------------------------------------ scope gate
IN_SCOPE = [
    "3분기 영업2팀 매출실적 알려줘",
    "한울산기 관련 자료 찾아줘",
    "실적집계 파일 최신본이 뭐야?",
    "회계팀 매출장 합계 얼마야",
    "영업1팀이 몇 위야?",  # intent word, no vocabulary
    "Bangkok Industrial Supply 거래 내역",
]
OUT_OF_SCOPE = [
    "오늘의 점심은 뭐야",
    "서울 날씨 어때?",
    "농담 하나 해줘",
    "너 누구야",
    "ㅋㅋㅋ",
    "?!",
    "파이썬 코딩 알려줘",
    "서울 날씨 최근 어때",  # intent word but an off-topic word wins without vocabulary
]


@pytest.mark.parametrize("q", IN_SCOPE)
def test_in_scope_questions_pass(env, q):
    _, svc, _ = env
    assert scope.check(q, svc.store) is None


@pytest.mark.parametrize("q", OUT_OF_SCOPE)
def test_out_of_scope_questions_are_rejected(env, q):
    _, svc, _ = env
    assert scope.check(q, svc.store) is not None


def test_followups_pass_only_inside_a_conversation(env):
    _, svc, _ = env
    assert scope.check("그럼 2위는?", svc.store) is None  # follow-up cue
    assert scope.check("2번째는?", svc.store, in_session=True) is None
    assert scope.check("2번째는?", svc.store) is not None  # no conversation, no vocabulary
    assert scope.check("오늘 점심 뭐야", svc.store, in_session=True) is not None


def test_company_glossary_counts_as_vocabulary(env):
    _, svc, _ = env
    # a business-looking question about a term the files lack goes to the agent, which explains
    assert scope.check("수주액 알려줘", svc.store) is None
    assert scope.check("꿀단지 있어", svc.store) is not None
    custom = [{"term": "꿀단지", "synonyms": [], "columns": ["목표"], "note": ""}]
    assert scope.check("꿀단지 있어", svc.store, custom) is None


def test_query_endpoint_refuses_without_calling_the_llm(env, monkeypatch):
    client, _, _ = env

    def boom(*a, **k):
        raise AssertionError("agent must not be called for an out-of-scope question")

    monkeypatch.setattr(main.agent, "ask", boom)
    res = client.post("/api/query", json={"question": "오늘의 점심은 뭐야", "user": USER})
    assert res.status_code == 200
    body = res.json()
    assert body["type"] == "범위밖" and body["confidence"] is None and body["sources"] is None
    assert "영업팀" in body["scope"]["departments"] and body["scope"]["columns"]
    saved = client.get("/api/history", params={"company": USER["company"]}).json()["items"]
    assert saved[0]["type"] == "범위밖"  # kept in the history like any other answer


# ------------------------------------------------------------------ sync additions
def test_manual_sync_needs_a_loaded_folder_and_bumps_version_only_on_change(env):
    client, svc, share = env
    v1 = svc.sync["version"]
    assert svc.sync["first_scan"] and v1 == 1 and svc.sync["trigger"] == "scan"
    same = _rescan(client, svc)  # nothing changed
    assert same["version"] == v1 and not same["changed"] and same["trigger"] == "manual"
    write_workbook(share / "구매팀/발주.xlsx", {"시트": [["품목", "수량"], ["A", 1]]})
    changed = _rescan(client, svc)
    assert changed["version"] == v1 + 1 and changed["changed"] and changed["added"] == 1
    assert changed["detail"]["added"] == ["구매팀/발주.xlsx"] and changed["at"]


def test_sync_before_any_scan_is_a_400(monkeypatch):
    monkeypatch.setattr(main, "service", ScanService())
    assert TestClient(main.app).post("/api/sync").status_code == 400


def test_sync_while_running_is_a_409(env, monkeypatch):
    client, svc, _ = env
    svc._status["state"] = "running"
    assert client.post("/api/sync").status_code == 409


def test_new_column_becomes_a_glossary_candidate_until_registered_or_dismissed(env):
    client, svc, share = env
    company = USER["company"]
    assert client.get("/api/glossary/candidates", params={"company": company}).json()["items"] == []
    write_workbook(
        share / "영업팀/수주현황.xlsx",
        {"수주": [["거래처명", "수주액", "비고사항", "3월"], ["한울산기", 5, "x", 1]]},
    )
    sync = _rescan(client, svc)
    assert sync["new_columns"] == ["거래처명", "비고사항", "수주액"]  # '3월' is never suggested
    items = client.get("/api/glossary/candidates", params={"company": company}).json()["items"]
    assert {i["column"] for i in items} == {"수주액", "비고사항"}  # 거래처명 is already a seed term
    assert items[0]["files"] == ["영업팀/수주현황.xlsx"]
    client.post("/api/glossary/candidates/dismiss", json={"company": company, "column": "비고사항"})
    client.post(
        "/api/glossary",
        json={"company": company, "term": "수주금액", "columns": ["수주액"], "by": "홍길동"},
    )
    left = client.get("/api/glossary/candidates", params={"company": company}).json()["items"]
    assert left == []  # one dismissed, one now covered by a glossary entry


def test_changed_and_deleted_sources_are_detected(env):
    client, svc, share = env
    rel = "영업팀/2026/실적/실적집계_v2.xlsx"
    gone = "회계팀/월마감/2026-07/매출장_202607.xlsx"
    stamps = svc.stamps_for([rel, gone, "없는/파일.xlsx"])
    assert set(stamps) == {rel, gone}  # unknown paths get no stamp
    ok = client.post("/api/sources/check", json={"stamps": stamps}).json()["items"]
    assert {v["status"] for v in ok.values()} == {"ok"}
    write_workbook(share / rel, {"팀별실적": [["팀명", "월", "매출실적"], ["영업9팀", "7월", 1]]})
    set_mtime(share / rel, "2026-10-03 10:00")
    (share / gone).rename(share / "회계팀/매출장_이동.xlsx")  # same content, new place
    _rescan(client, svc)
    res = client.post("/api/sources/check", json={"stamps": stamps}).json()["items"]
    assert res[rel]["status"] == "changed"
    assert res[gone] == {"status": "moved", "to": "회계팀/매출장_이동.xlsx"}
    (share / "회계팀/매출장_이동.xlsx").unlink()
    _rescan(client, svc)
    res = client.post("/api/sources/check", json={"stamps": stamps}).json()["items"]
    assert res[gone]["status"] == "deleted"
