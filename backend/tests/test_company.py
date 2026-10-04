"""Per-company storage: history, glossary, sync log, auto-sync and isolation between companies."""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from app import main
from app.companydb import CompanyManager, diff_fingerprints
from app.service import ScanService

from .conftest import set_mtime, write_workbook

USER = {"company": "㈜가온산업", "name": "홍길동", "dept": "영업팀", "title": "대리"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    svc = ScanService()
    mgr = CompanyManager(tmp_path / "data")
    svc.on_complete = lambda c, p, fp, sh, rows, **kw: mgr.open(c).record_scan(
        p, fp, sh, rows, kw.get("columns")
    )
    monkeypatch.setattr(main, "service", svc)
    monkeypatch.setattr(main, "companies", mgr)
    return TestClient(main.app)


def test_diff_follows_the_design_table():
    prev = {
        "a.xlsx": (1, "t", "h1"),
        "b.xlsx": (1, "t", "h2"),
        "c.xlsx": (1, "t", "h3"),
        "d.xlsx": (1, "t", "h4"),
    }
    new = {
        "a.xlsx": (1, "t", "h1"),
        "b.xlsx": (2, "t", "h2x"),
        "moved/c.xlsx": (1, "t", "h3"),
        "e.xlsx": (1, "t", "h5"),
    }
    d = diff_fingerprints(prev, new)
    assert d["added"] == ["e.xlsx"] and d["modified"] == ["b.xlsx"] and d["deleted"] == ["d.xlsx"]
    assert d["moved"] == [
        {"from": "c.xlsx", "to": "moved/c.xlsx"}
    ]  # same hash = same file, memory kept


def test_login_creates_company_db_and_remembers_folder(client, share):
    res = client.post("/api/login", json=USER).json()
    assert res["company"] == "㈜가온산업" and res["last_folder"] is None and res["loaded"] is False
    client.post("/api/scan", json={"path": str(share), "company": USER["company"]})
    main.service.wait()
    again = client.post("/api/login", json=USER).json()
    assert again["last_folder"] == str(share.resolve()) and again["loaded"] is True
    assert client.get("/api/catalog").json()["company"] == "㈜가온산업"


def test_sync_log_records_add_modify_delete(client, share):
    body = {"path": str(share), "company": USER["company"]}
    client.post("/api/scan", json=body)
    main.service.wait()
    first = client.get("/api/scan/log", params={"company": USER["company"]}).json()["items"][0]
    assert first["added"] == first["files"] > 0 and first["modified"] == first["deleted"] == 0
    # one new file, one edited, one deleted
    write_workbook(share / "구매팀/발주.xlsx", {"시트": [["품목", "수량"], ["A", 1]]})
    write_workbook(share / "개인/메모.xlsx", {"시트1": [["a", "b"], [9, 9]]})
    set_mtime(share / "개인/메모.xlsx", "2026-10-02 09:00")
    (share / "회계팀/월마감/2026-07/매출장_202607.xlsx").unlink()
    client.post("/api/scan", json=body)
    main.service.wait()
    log = client.get("/api/scan/log", params={"company": USER["company"]}).json()["items"][0]
    assert (log["added"], log["modified"], log["deleted"]) == (1, 1, 1)
    assert main.service.status()["sync"]["added"] == 1


def test_auto_sync_rescans_only_when_files_changed(client, share):
    client.post("/api/scan", json={"path": str(share), "company": USER["company"]})
    main.service.wait()
    assert main.service.auto_sync_once() is False  # nothing changed
    shutil.copy(share / "개인/메모.xlsx", share / "개인/메모_복사.xlsx")
    assert main.service.auto_sync_once() is True
    main.service.wait()
    log = client.get("/api/scan/log", params={"company": USER["company"]}).json()["items"][0]
    assert log["added"] == 1 and log["moved"] == 0


def test_history_is_per_company_and_per_user(client):
    db = main.companies.open("㈜가온산업")
    qid = db.add_question(
        "홍길동",
        "s1",
        {"question": "3분기 3등?", "type": "질문형", "answer": "…", "confidence": "낮음"},
    )
    db.add_question(
        "김대리", "s2", {"question": "재고?", "type": "질문형", "answer": "…", "confidence": "높음"}
    )
    other = main.companies.open("다른회사")
    other.add_question(
        "홍길동",
        "s3",
        {"question": "남의 질문", "type": "질문형", "answer": "…", "confidence": "높음"},
    )

    mine = client.get("/api/history", params={"company": "㈜가온산업", "user": "홍길동"}).json()[
        "items"
    ]
    assert [m["question"] for m in mine] == ["3분기 3등?"]
    everyone = client.get("/api/history", params={"company": "㈜가온산업"}).json()["items"]
    assert {m["question"] for m in everyone} == {"3분기 3등?", "재고?"}  # no leakage from 다른회사
    assert (
        client.get(f"/api/history/{qid}", params={"company": "㈜가온산업"}).json()["response"][
            "answer"
        ]
        == "…"
    )
    # same id in another company is a different row (separate databases)
    theirs = client.get(f"/api/history/{qid}", params={"company": "다른회사"}).json()
    assert theirs["response"]["question"] == "남의 질문"
    assert client.delete(f"/api/history/{qid}", params={"company": "㈜가온산업"}).status_code == 200
    assert client.get(f"/api/history/{qid}", params={"company": "㈜가온산업"}).status_code == 404


def test_company_glossary_extends_the_seed(client):
    body = {
        "company": "㈜가온산업",
        "term": "수주액",
        "synonyms": ["오더금액"],
        "columns": ["계약금액"],
        "by": "홍길동",
    }
    saved = client.post("/api/glossary", json=body).json()
    listing = client.get("/api/glossary", params={"company": "㈜가온산업"}).json()
    assert [g["term"] for g in listing["custom"]] == ["수주액"] and listing["seed"]
    assert client.get("/api/glossary", params={"company": "다른회사"}).json()["custom"] == []
    # the agent's term lookup uses it (grade 동의어, not 미등록)
    from app.agent import glossary

    hit = glossary.match_term("오더금액", {"계약금액"}, listing["custom"])
    assert hit["grade"] == "동의어" and hit["columns"] == ["계약금액"]
    assert glossary.match_term("오더금액", {"계약금액"})["grade"] == "미등록"
    assert client.post("/api/glossary", json={**body, "columns": []}).status_code == 400
    assert (
        client.delete(f"/api/glossary/{saved['id']}", params={"company": "㈜가온산업"}).status_code
        == 200
    )


def test_data_survives_restart(tmp_path):
    first = CompanyManager(tmp_path)
    first.open("㈜가온산업").add_question(
        "홍길동", "s", {"question": "q", "type": "질문형", "answer": "a"}
    )
    first.open("㈜가온산업").glossary_save("수주액", [], ["계약금액"], "", "홍")
    again = CompanyManager(tmp_path)  # a new process would build a fresh manager
    assert again.open("㈜가온산업").recent_questions()[0]["question"] == "q"
    assert again.list_companies() == ["㈜가온산업"]
    assert again.open("㈜가온산업").glossary_list()[0]["term"] == "수주액"


def test_query_requires_company(client):
    res = client.post("/api/query", json={"question": "안녕", "user": {"name": "홍"}})
    assert res.status_code in (400, 409)


def test_company_db_avoids_the_file_creating_rollback_journal(tmp_path):
    """On some drives (exFAT disks) SQLite's default rollback journal fails from the second write
    with 'attempt to write a readonly database'. WAL / memory journals do not."""
    db = CompanyManager(tmp_path).open("㈜가온산업")
    assert db.journal_mode in ("wal", "memory")
    for i in range(50):  # many small writes, like questions being saved one after another
        db.add_question("홍", "s", {"question": f"q{i}", "type": "질문형", "answer": "a"})
    assert len(db.recent_questions(limit=100)) == 50
    # the database written in WAL mode is still found and complete after a "restart"
    again = CompanyManager(tmp_path).open("㈜가온산업")
    assert len(again.recent_questions(limit=100)) == 50
