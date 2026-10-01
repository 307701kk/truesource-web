"""FastAPI app.  Run:  uvicorn app.main:app --port 8000"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import config
from .service import ScanBusyError, ScanPathError, ScanService

app = FastAPI(title="TrueSource backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
service = ScanService()


class ScanRequest(BaseModel):
    path: str


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/scan", status_code=202)
def start_scan(req: ScanRequest) -> dict:
    """Start scanning a folder. Returns at once; poll GET /api/scan/status."""
    try:
        return service.start(req.path)
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
