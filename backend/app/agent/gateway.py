"""Security gateway: the ONLY place that talks to the external LLM.

Order (same as the design doc):  permission -> allowed fields -> masking -> injection block
-> audit log -> API call -> unmask the reply locally.

Masking replaces team / customer / person names (known from the value index) and ID-like
strings with placeholders such as {팀1}. Real names never leave the PC; the reply is unmasked here.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from datetime import datetime

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .. import config

PURPOSES = {"plan_and_answer", "glossary_draft"}

# value_index column name -> placeholder kind
ENTITY_COLUMNS = {
    "팀명": "팀",
    "담당팀": "팀",
    "거래처명": "거래처",
    "업체명": "거래처",
    "고객사": "거래처",
    "거래처": "거래처",
    "공급처": "거래처",
    "담당자": "담당자",
}
_PATTERNS = [
    ("사업자번호", re.compile(r"\b\d{3}-\d{2}-\d{5}\b")),
    ("전화", re.compile(r"\b0\d{1,2}-\d{3,4}-\d{4}\b")),
    ("이메일", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
]
_INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|prompts?)|disregard .{0,30}instructions"
    r"|이전\s*(지시|명령|지침)(사항)?\s*(을|를)?\s*(모두\s*)?(무시|잊)|시스템\s*프롬프트|system prompt"
    r"|api[\s_-]*key|apikey|비밀\s*키|위\s*지시를?\s*무시)",
    re.IGNORECASE,
)
FORBIDDEN_KEYS = {"raw_rows", "raw", "cells", "records"}
MAX_LIST_ITEMS = 60
MAX_PAYLOAD_CHARS = 120_000
MAX_ATTEMPTS = 4  # per LLM call (429 / 5xx are retried)


class GatewayError(RuntimeError):
    """Raised for blocked or failed LLM calls. The message is safe to show the user."""


class LLMNotConfigured(GatewayError):
    pass


class Masker:
    """Reversible placeholder mapping. Stable: built from the sorted entity values of the store."""

    def __init__(self, store=None) -> None:
        self.fwd: dict[str, str] = {}
        self.rev: dict[str, str] = {}
        self._lock = threading.Lock()
        self._regex: re.Pattern | None = None
        self.count = 0
        if store is not None:
            self._load(store)

    def _load(self, store) -> None:
        cols = list(ENTITY_COLUMNS)
        marks = ",".join("?" * len(cols))
        rows = store.query(
            f"SELECT DISTINCT value, column_name FROM value_index WHERE column_name IN ({marks})"
            " ORDER BY value",
            cols,
        )
        counters: dict[str, int] = {}
        for value, column in rows:
            if value in self.fwd or len(value) < 2:
                continue
            kind = ENTITY_COLUMNS[column]
            counters[kind] = counters.get(kind, 0) + 1
            ph = f"{{{kind}{counters[kind]}}}"
            self.fwd[value] = ph
            self.rev[ph] = value
        self._rebuild()

    def _rebuild(self) -> None:
        names = sorted(self.fwd, key=len, reverse=True)
        self._regex = re.compile("|".join(re.escape(n) for n in names)) if names else None

    def mask(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            self.count += 1
            return self.fwd[m.group(0)]

        if self._regex:
            text = self._regex.sub(sub, text)
        for kind, pat in _PATTERNS:

            def pat_sub(m: re.Match, kind=kind) -> str:
                self.count += 1
                with self._lock:
                    if m.group(0) not in self.fwd:
                        n = sum(1 for p in self.rev if p.startswith("{" + kind)) + 1
                        ph = f"{{{kind}{n}}}"
                        self.fwd[m.group(0)] = ph
                        self.rev[ph] = m.group(0)
                    return self.fwd[m.group(0)]

            text = pat.sub(pat_sub, text)
        return text

    def unmask(self, text: str) -> str:
        for ph, real in self.rev.items():
            if ph in text:
                text = text.replace(ph, real)
        return text


def map_strings(obj, fn):
    """Deep-apply fn to every string inside dict / list / str structures."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {k: map_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [map_strings(v, fn) for v in obj]
    return obj


def map_content(content: types.Content, fn) -> types.Content:
    """Copy a Content, applying fn to all text / function-call args / function-response data."""
    new = content.model_copy(deep=True)
    for part in new.parts or []:
        if part.text:
            part.text = fn(part.text)
        if part.function_call and part.function_call.args:
            part.function_call.args = map_strings(dict(part.function_call.args), fn)
        if part.function_response and part.function_response.response:
            part.function_response.response = map_strings(dict(part.function_response.response), fn)
    return new


def assert_allowed_fields(purpose: str, contents: list[types.Content]) -> None:
    """Only questions, schemas, catalog summaries and aggregate values may leave. Raw rows may not."""

    def walk(obj) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in FORBIDDEN_KEYS:
                    raise GatewayError(f"외부 전송 차단: 허용되지 않는 필드 '{k}'")
                walk(v)
        elif isinstance(obj, list):
            if len(obj) > MAX_LIST_ITEMS:
                raise GatewayError("외부 전송 차단: 목록이 너무 길어 원본 행으로 의심됨")
            for v in obj:
                walk(v)

    for c in contents:
        for p in c.parts or []:
            if p.function_response and p.function_response.response:
                walk(dict(p.function_response.response))
    size = sum(len(c.model_dump_json(exclude_none=True)) for c in contents)
    if size > MAX_PAYLOAD_CHARS:
        raise GatewayError("외부 전송 차단: 전송 크기가 한도를 넘음")


class AuditLog:
    def __init__(self) -> None:
        self._items: deque[dict] = deque(maxlen=config.AUDIT_MAX_ENTRIES)
        self._lock = threading.Lock()
        self._n = 0

    def add(self, **entry) -> None:
        with self._lock:
            self._n += 1
            self._items.append(
                {"id": self._n, "time": datetime.now().isoformat(timespec="seconds"), **entry}
            )

    def recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._items)[-limit:][::-1]


class Gateway:
    def __init__(self, audit: AuditLog | None = None) -> None:
        self.audit = audit or AuditLog()
        self._client: genai.Client | None = None

    def configured(self) -> bool:
        return bool(config.gemini_api_key())

    def _get_client(self) -> genai.Client:
        key = config.gemini_api_key()
        if not key:
            raise LLMNotConfigured(
                "GEMINI_API_KEY 가 설정되지 않았습니다. backend/.env 에 키를 넣고 서버를 다시 시작하세요."
            )
        if self._client is None:
            self._client = genai.Client(api_key=key)
        return self._client

    # -------------------------------------------------------------- the single entry point
    def call_llm(
        self,
        *,
        purpose: str,
        contents: list[types.Content],
        gen_config: types.GenerateContentConfig,
        user: dict,
        question_id: str,
        masker: Masker,
    ) -> types.Content:
        self._check_permission(user, purpose)  # 1 permission
        assert_allowed_fields(purpose, contents)  # 2 allowed fields only
        blocked: list[str] = []
        before = masker.count

        def scrub(text: str) -> str:  # 3 masking, 4 injection block
            text = masker.mask(text)

            def redact(m: re.Match) -> str:
                blocked.append(m.group(0))
                return "[차단됨]"

            return _INJECTION.sub(redact, text)

        masked = [map_content(c, scrub) for c in contents]
        masked_cfg = gen_config.model_copy(deep=True)
        if isinstance(masked_cfg.system_instruction, str):
            masked_cfg.system_instruction = scrub(masked_cfg.system_instruction)
        client = self._get_client()  # raises before anything is logged as sent
        self.audit.add(  # 5 audit: exactly what leaves the PC
            user=user.get("name"),
            dept=user.get("dept"),
            purpose=purpose,
            question_id=question_id,
            model=config.gemini_model(),
            masked_count=masker.count - before,
            blocked=blocked,
            sent=json.dumps(
                [c.model_dump(mode="json", exclude_none=True) for c in masked], ensure_ascii=False
            )[:20000],
            system=str(masked_cfg.system_instruction)[:4000],
        )
        resp = self._generate(client, masked, masked_cfg)  # 6 API call
        if (
            not resp.candidates
            or not resp.candidates[0].content
            or not resp.candidates[0].content.parts
        ):
            reason = resp.candidates[0].finish_reason if resp.candidates else "no candidates"
            raise GatewayError(f"LLM 이 응답을 만들지 못했습니다 ({reason}).")
        return map_content(resp.candidates[0].content, masker.unmask)  # 7 unmask locally

    @staticmethod
    def _check_permission(user: dict, purpose: str) -> None:
        if purpose not in PURPOSES:
            raise GatewayError(f"허용되지 않는 호출 목적: {purpose}")
        if not (user or {}).get("name"):
            raise GatewayError("사용자 정보가 없어 외부 LLM 을 호출할 수 없습니다.")

    @staticmethod
    def _retry_wait(exc: Exception, attempt: int) -> float:
        """Seconds to wait before retrying: the server's own hint for 429, else a short backoff."""
        code = getattr(exc, "code", None)
        if code == 429:
            m = re.search(r"retry(?:Delay|[ _]in)\D{0,12}([\d.]+)\s*s", str(exc), re.IGNORECASE)
            return min(float(m.group(1)) + 1, 65) if m else 20.0
        return 2.0 * (attempt + 1)

    def _generate(self, client, contents, cfg):
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                return client.models.generate_content(
                    model=config.gemini_model(), contents=contents, config=cfg
                )
            except genai_errors.APIError as exc:
                last = exc
                code = getattr(exc, "code", None)
                detail = str(getattr(exc, "message", "") or "")[:160]
                if code in (429, 500, 503) and attempt < MAX_ATTEMPTS - 1:
                    time.sleep(self._retry_wait(exc, attempt))
                    continue
                if code in (400, 401, 403):
                    raise GatewayError(
                        f"Gemini API 오류({code}): {detail} (API 키·프로젝트·모델명을 확인하세요)"
                    ) from exc
                if code == 429:
                    raise GatewayError(
                        "Gemini 요청 한도(분당 호출 수)를 넘었습니다. 잠시 후 다시 질문하세요."
                    ) from exc
                raise GatewayError(f"Gemini API 오류({code}): {detail}") from exc
        raise GatewayError("Gemini API 호출에 실패했습니다.") from last
