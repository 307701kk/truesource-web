"""Several people ask at the same time (one server = one company)."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent.runner import Agent
from app.companydb import CompanyManager
from app.limiter import QuestionLimiter, QueueTimeout

from .test_agent import ScriptedGateway
from .test_rules import make, month_rows, perf, scan

USER = {"name": "홍", "company": "㈜가온산업"}


def test_limiter_never_runs_more_than_the_limit_and_queues_the_rest():
    lim = QuestionLimiter(2)
    state = {"now": 0, "peak": 0, "done": 0}
    guard = threading.Lock()

    def work():
        with lim.slot(5):
            with guard:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.15)
            with guard:
                state["now"] -= 1
                state["done"] += 1

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    seen = lim.status()
    assert seen == {"limit": 2, "running": 2, "waiting": 4}  # two at work, four in line
    for t in threads:
        t.join()
    assert state["peak"] == 2 and state["done"] == 6  # nobody lost, never more than two at once
    assert lim.status() == {"limit": 2, "running": 0, "waiting": 0}


def test_waiting_too_long_gives_up_without_blocking_others():
    lim = QuestionLimiter(1)
    release = threading.Event()

    def hold():
        with lim.slot(5):
            release.wait(2)

    holder = threading.Thread(target=hold)
    holder.start()
    time.sleep(0.05)
    with pytest.raises(QueueTimeout):
        with lim.slot(0.1):
            pass
    assert lim.status()["waiting"] == 0  # the one that gave up left the line
    release.set()
    holder.join()
    with lim.slot(1):  # the slot is free again
        pass


class SlowGateway(ScriptedGateway):
    def _generate(self, client, contents, cfg):
        time.sleep(0.2)
        return super()._generate(client, contents, cfg)


def serve(tmp_path, monkeypatch, limit):
    root = tmp_path / "share"
    make(root, "영업팀/집계.xlsx", {"팀별실적": perf(month_rows())})
    svc = scan(root)
    svc.scan_company = "㈜가온산업"

    def policy(step, r, contents):
        return [("final_answer", {"question_type": "질문형", "answer": "답"})]

    monkeypatch.setattr(main, "service", svc)
    monkeypatch.setattr(main, "agent", Agent(svc, SlowGateway(policy)))
    monkeypatch.setattr(main, "companies", CompanyManager(tmp_path / "data"))
    monkeypatch.setattr(main, "limiter", QuestionLimiter(limit))
    return TestClient(main.app)


def ask(client, i, out):
    res = client.post("/api/query", json={"question": f"집계 매출 {i}", "user": USER})
    out[i] = (res.status_code, res.json().get("session_id"))


def test_simultaneous_questions_all_get_their_own_answer(tmp_path, monkeypatch):
    client = serve(tmp_path, monkeypatch, limit=4)
    out: dict = {}
    threads = [threading.Thread(target=ask, args=(client, i, out)) for i in range(8)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [v[0] for v in out.values()] == [200] * 8
    assert len({v[1] for v in out.values()}) == 8  # separate conversations, nothing mixed up
    assert time.time() - t0 < 8 * 0.2 * 3  # clearly faster than one after another
    assert client.get("/api/queue").json() == {"limit": 4, "running": 0, "waiting": 0}


def test_queue_endpoint_shows_people_waiting(tmp_path, monkeypatch):
    client = serve(tmp_path, monkeypatch, limit=1)
    out: dict = {}
    threads = [threading.Thread(target=ask, args=(client, i, out)) for i in range(3)]
    for t in threads:
        t.start()
    time.sleep(0.15)
    mid = client.get("/api/queue").json()
    assert mid["limit"] == 1 and mid["running"] == 1 and mid["waiting"] == 2
    for t in threads:
        t.join()
    assert [v[0] for v in out.values()] == [200] * 3


def test_a_question_that_waits_too_long_gets_a_clear_message(tmp_path, monkeypatch):
    client = serve(tmp_path, monkeypatch, limit=1)
    monkeypatch.setattr(main.config, "QUEUE_WAIT_SECONDS", 0.05)
    out: dict = {}
    first = threading.Thread(target=ask, args=(client, 0, out))
    first.start()
    time.sleep(0.05)
    res = client.post("/api/query", json={"question": "집계 매출 늦게", "user": USER})
    assert res.status_code == 503 and "순서를 기다리다" in res.json()["detail"]
    first.join()


def test_another_companys_name_cannot_read_this_servers_data(tmp_path, monkeypatch):
    client = serve(tmp_path, monkeypatch, limit=2)
    res = client.post(
        "/api/query", json={"question": "집계 매출", "user": {"name": "김", "company": "다른회사"}}
    )
    assert (
        res.status_code == 409
        and "다른 회사" in res.json()["detail"]
        and "㈜가온산업" in res.json()["detail"]
    )
    # nothing was saved under the other company
    assert client.get("/api/history", params={"company": "다른회사"}).json()["items"] == []
    ok = client.post("/api/query", json={"question": "집계 매출", "user": USER})
    assert ok.status_code == 200


def test_fifty_people_asking_at_once_do_not_freeze_the_light_requests(tmp_path, monkeypatch):
    """50 questions wait in line (each holds a request thread). Health / queue checks and logins of
    everybody else must stay fast, and all 50 must get an answer."""
    client = serve(tmp_path, monkeypatch, limit=4)
    out: dict = {}
    threads = [threading.Thread(target=ask, args=(client, i, out)) for i in range(50)]
    for t in threads:
        t.start()
    time.sleep(0.4)  # everybody is now either working or waiting
    t0 = time.time()
    assert client.get("/api/health").status_code == 200
    q = client.get("/api/queue").json()
    login = client.post(
        "/api/login", json={"company": "㈜가온산업", "name": "새사람", "dept": "영업팀"}
    )
    assert time.time() - t0 < 2.0 and login.status_code == 200  # not stuck behind the 50 questions
    assert q["running"] == 4 and q["waiting"] >= 30
    for t in threads:
        t.join()
    assert (
        sorted(v[0] for v in out.values()) == [200] * 50 and len({v[1] for v in out.values()}) == 50
    )
