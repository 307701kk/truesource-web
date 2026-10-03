"""FastAPI app.  Run:  uvicorn app.main:app --port 8000"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # backend/.env (GEMINI_API_KEY ...)

from . import config  # noqa: E402
from .agent.gateway import Gateway, GatewayError, LLMNotConfigured  # noqa: E402
from .agent.runner import Agent  # noqa: E402
from .companydb import CompanyManager  # noqa: E402
from .opener import OpenError, open_file  # noqa: E402
from .service import ScanBusyError, ScanPathError, ScanService  # noqa: E402


async def _auto_sync_loop() -> None:
    """Periodic scan (design: every ~10 min): re-reads the folder only when files changed."""
    interval = config.sync_interval_seconds()
    while interval > 0:
        await asyncio.sleep(interval)
        await asyncio.to_thread(service.auto_sync_once)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_auto_sync_loop())
    yield
    task.cancel()


app = FastAPI(title="TrueSource backend", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins(),
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
service = ScanService()
companies = CompanyManager(config.data_dir())
gateway = Gateway()
agent = Agent(service, gateway)


def _record_scan(company: str, path: str, fp: dict, sheets: int, rows: int) -> dict:
    db = companies.open(company)
    return db.record_scan(path, fp, sheets, rows)


service.on_complete = _record_scan


def _company(name: str | None):
    """The company DB for a request. 400 if the name is missing."""
    try:
        return companies.open(name or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="회사명이 필요합니다.") from exc


class ScanRequest(BaseModel):
    path: str
    company: str | None = None


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/scan", status_code=202)
def start_scan(req: ScanRequest) -> dict:
    """Start scanning a folder. Returns at once; poll GET /api/scan/status."""
    try:
        if req.company:
            _company(req.company)  # make sure the company DB exists before scanning
        return service.start(req.path, company=req.company or None)
    except ScanPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ScanBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/scan/status")
def scan_status() -> dict:
    return service.status()


@app.get("/api/catalog")
def get_catalog() -> dict:
    """File list of the last finished scan (empty before the first scan)."""
    return service.catalog()


@app.get("/api/catalog/{file_id}")
def get_catalog_file(file_id: int) -> dict:
    """One file with its sheets and columns (for debugging / later tools)."""
    detail = service.file_detail(file_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="해당 파일이 없습니다.")
    return detail


class QueryRequest(BaseModel):
    question: str
    session_id: str | None = None
    user: dict = {}


@app.get("/api/llm/status")
def llm_status() -> dict:
    return {"configured": gateway.configured(), "model": config.gemini_model()}


@app.post("/api/query")
def query(req: QueryRequest) -> dict:
    """Ask a question: the agent plans, calls local tools and answers (see README)."""
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비었습니다.")
    if len(question) > 1000:
        raise HTTPException(status_code=400, detail="질문은 1000자 이내로 입력하세요.")
    if service.store is None:
        raise HTTPException(status_code=409, detail="먼저 공유폴더를 분석하세요.")
    db = _company(req.user.get("company"))
    try:
        out = agent.ask(question, req.user, req.session_id, db.glossary_list())
        out["history_id"] = db.add_question(req.user.get("name", ""), out.get("session_id"), out)
        return out
    except LLMNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/audit")
def audit(limit: int = 100) -> dict:
    """What was sent to the external LLM (already masked), newest first."""
    return {"items": gateway.audit.recent(min(max(limit, 1), 500))}


class LoginRequest(BaseModel):
    company: str
    name: str
    dept: str = ""
    title: str = ""


@app.post("/api/login")
def login(req: LoginRequest) -> dict:
    """Open (or create) the company's database and register the user.
    Tells the client where the company's folder was last scanned and whether it is loaded now."""
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="이름이 필요합니다.")
    db = _company(req.company)
    user = db.upsert_user(req.name, req.dept, req.title)
    loaded = service.scan_company is not None and service.scan_company == db.name
    return {
        "company": db.name,
        "user": user,
        "last_folder": db.get_meta("last_folder"),
        "loaded": loaded and service.store is not None,
        "question_count": len(db.recent_questions(user["name"], 1000)),
    }


@app.get("/api/companies")
def list_companies() -> dict:
    return {"companies": companies.list_companies()}


@app.get("/api/history")
def history(company: str, user: str | None = None, limit: int = 50) -> dict:
    """Recent questions of a company (optionally only one user's), newest first."""
    return {"items": _company(company).recent_questions(user, min(max(limit, 1), 200))}


@app.get("/api/history/{qid}")
def history_item(qid: int, company: str) -> dict:
    item = _company(company).get_question(qid)
    if item is None:
        raise HTTPException(status_code=404, detail="해당 질문이 없습니다.")
    return item


@app.delete("/api/history/{qid}")
def history_delete(qid: int, company: str) -> dict:
    if not _company(company).delete_question(qid):
        raise HTTPException(status_code=404, detail="해당 질문이 없습니다.")
    return {"ok": True}


class GlossaryRequest(BaseModel):
    company: str
    term: str
    synonyms: list[str] = []
    columns: list[str] = []
    note: str = ""
    by: str = ""


@app.get("/api/glossary")
def glossary_list(company: str) -> dict:
    """Built-in seed entries (read-only) + this company's own entries."""
    from .agent.glossary import GLOSSARY

    return {"seed": GLOSSARY, "custom": _company(company).glossary_list()}


@app.post("/api/glossary")
def glossary_save(req: GlossaryRequest) -> dict:
    term = req.term.strip()
    clean = lambda xs: list(dict.fromkeys(x.strip() for x in xs if x.strip()))[:30]  # noqa: E731
    if not term or len(term) > 40:
        raise HTTPException(status_code=400, detail="표준용어는 1~40자로 입력하세요.")
    columns = clean(req.columns)
    if not columns:
        raise HTTPException(status_code=400, detail="컬럼 후보를 하나 이상 입력하세요.")
    return _company(req.company).glossary_save(
        term, clean(req.synonyms), columns, req.note.strip()[:200], req.by.strip()
    )


@app.delete("/api/glossary/{gid}")
def glossary_delete(gid: int, company: str) -> dict:
    if not _company(company).glossary_delete(gid):
        raise HTTPException(status_code=404, detail="해당 항목이 없습니다.")
    return {"ok": True}


@app.get("/api/scan/log")
def scan_log(company: str, limit: int = 20) -> dict:
    """Sync log of the company: one row per scan with 추가/수정/삭제/이동 counts."""
    return {"items": _company(company).scan_logs(min(max(limit, 1), 100))}


class OpenRequest(BaseModel):
    path: str


@app.post("/api/open")
def open_excel(req: OpenRequest, request: Request) -> dict:
    """Open a scanned workbook in the user's own Excel. Only this machine may ask, and only for
    files the scan has seen."""
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="이 PC에서만 파일을 열 수 있습니다.")
    store = service.store
    known = {r[0] for r in store.query("SELECT rel_path FROM files")} if store else set()
    try:
        opened = open_file(service.scanned_path, req.path, known)
    except OpenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "opened": opened.name}
