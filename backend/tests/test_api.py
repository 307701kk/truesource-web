from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.service import ScanService


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "service", ScanService())
    return TestClient(main.app)


def scan_and_wait(client, path):
    resp = client.post("/api/scan", json={"path": str(path)})
    assert resp.status_code == 202, resp.text
    main.service.wait(30)
    return client.get("/api/scan/status").json()


def test_catalog_before_any_scan(client):
    body = client.get("/api/catalog").json()
    assert body["state"] == "idle" and body["files"] == [] and body["total"] == 0
    assert body["departments"][-1] == "미분류"


def test_scan_flow_and_catalog_contract(client, share):
    status = scan_and_wait(client, share)
    assert status["state"] == "done" and status["percent"] == 100
    assert status["files_total"] == status["files_done"] == 6
    assert status["skipped"] == {"unsupported_xls": 1, "lock_file": 1}
    assert [e["file"] for e in status["errors"]] == ["영업팀/깨진파일.xlsx"]

    cat = client.get("/api/catalog").json()
    by_path = {f["path"]: f for f in cat["files"]}
    assert cat["total"] == 6 and cat["scanned_path"]
    v2 = by_path["영업팀/2026/실적/실적집계_v2.xlsx"]
    assert set(v2) == {
        "id",
        "name",
        "path",
        "dept",
        "top_folder",
        "modified",
        "size",
        "sheets",
        "rows",
        "fresh",
        "copy_of",
        "data_date",
        "error",
    }
    assert (v2["dept"], v2["fresh"], v2["rows"], v2["modified"]) == (
        "영업팀",
        "ok",
        2,
        "2026-09-28 13:10",
    )
    assert by_path["영업팀/2026/실적/실적집계.xlsx"]["fresh"] == "stale"
    copy = by_path["공용/받은자료/실적집계_v2.xlsx"]
    assert (copy["fresh"], copy["copy_of"]) == ("copy", "영업팀/2026/실적/실적집계_v2.xlsx")
    assert by_path["개인/메모.xlsx"]["dept"] == "미분류"
    assert (
        by_path["영업팀/깨진파일.xlsx"]["error"]
        and by_path["영업팀/깨진파일.xlsx"]["fresh"] is None
    )
    assert by_path["회계팀/월마감/2026-07/매출장_202607.xlsx"]["rows"] == 2  # data rows only


def test_file_detail(client, share):
    scan_and_wait(client, share)
    cat = client.get("/api/catalog").json()
    fid = next(f["id"] for f in cat["files"] if f["name"] == "매출장_202607.xlsx")
    detail = client.get(f"/api/catalog/{fid}").json()
    sheet = detail["sheet_list"][0]
    assert sheet["header_row"] == 4 and sheet["data_rows"] == 2
    assert [c["name"] for c in sheet["columns"]] == ["일자", "업체명", "공급가액", "비고"]
    assert client.get("/api/catalog/99999").status_code == 404


def test_bad_paths(client, tmp_path):
    assert client.post("/api/scan", json={"path": ""}).status_code == 400
    assert client.post("/api/scan", json={"path": str(tmp_path / "missing")}).status_code == 400
    assert client.post("/api/scan", json={"path": "/etc"}).status_code == 400
    assert client.post("/api/scan", json={}).status_code == 422


def test_second_scan_while_running_is_rejected(client, share):
    main.service._status["state"] = "running"
    resp = client.post("/api/scan", json={"path": str(share)})
    assert resp.status_code == 409


def test_rescan_replaces_previous_result(client, share, tmp_path):
    scan_and_wait(client, share)
    other = tmp_path / "other"
    other.mkdir()
    from .conftest import write_workbook

    write_workbook(other / "영업팀/a.xlsx", {"s": [["x", "y"], [1, 2]]})
    scan_and_wait(client, other)
    cat = client.get("/api/catalog").json()
    assert [f["path"] for f in cat["files"]] == ["영업팀/a.xlsx"]


def test_empty_folder_scan(client, tmp_path):
    status = scan_and_wait(client, tmp_path)
    assert status["state"] == "done" and status["files_total"] == 0


def test_cors_allows_vite_dev_origin(client):
    resp = client.options(
        "/api/scan",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    bad = client.options(
        "/api/scan",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


# ---- regressions found in code review ------------------------------------------------------
def test_one_bad_sheet_does_not_abort_the_scan(client, tmp_path, monkeypatch):
    from app.store import Store

    from .conftest import write_workbook

    write_workbook(
        tmp_path / "영업팀/a.xlsx",
        {"good": [["x", "y"], [1, 2]], "bad": [["x", "y"], [3, 4]]},
    )
    write_workbook(tmp_path / "영업팀/b.xlsx", {"s": [["Name", "name", "_Row"], [1, 2, 3]]})
    real = Store.add_sheet

    def flaky(self, file_id, sheet):
        if sheet.sheet_name == "bad":
            raise RuntimeError("boom")
        return real(self, file_id, sheet)

    monkeypatch.setattr(Store, "add_sheet", flaky)
    status = scan_and_wait(client, tmp_path)
    assert status["state"] == "done"
    assert status["errors"] == [{"file": "영업팀/a.xlsx", "message": "[bad] RuntimeError"}]
    files = {f["name"]: f for f in client.get("/api/catalog").json()["files"]}
    assert files["a.xlsx"]["sheets"] == 1 and "bad" in files["a.xlsx"]["error"]
    assert files["b.xlsx"]["error"] is None and files["b.xlsx"]["rows"] == 1


def test_uncached_formula_warning(client, tmp_path):
    from .conftest import write_workbook

    write_workbook(tmp_path / "a.xlsx", {"s": [["a", "b"], [1, "=A2*2"]]})
    status = scan_and_wait(client, tmp_path)
    assert any("수식 1개" in w for w in status["warnings"])


def test_scan_path_with_nul_byte_is_a_400(client):
    assert client.post("/api/scan", json={"path": "a\x00b"}).status_code == 400
