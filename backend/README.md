# TrueSource Backend

> 에이전트(질문 처리) 동작 방식은 [루트 README](../README.md#기술-구조) 참고. 아래는 데이터 계층 설명이며, `.env`(`GEMINI_API_KEY`)는 `.env.example`을 복사해 만든다.

공유폴더 경로를 받아 엑셀(xlsx/xlsm)을 스캔 → 시트별 표로 변환 → 인메모리 DuckDB에 저장.
원본 엑셀은 읽기 전용으로만 열고 수정하지 않음.

## 실행 (Windows)
```
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
테스트/린트: `pip install -r requirements-dev.txt` → `pytest -q`, `ruff check .`
가상 데이터셋 검증: `set TRUESOURCE_DATASET=<가온산업_가상데이터 경로>` 후 `pytest -q`

## API
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | /api/health | 상태 |
| POST | /api/scan `{"path": "..."}` | 스캔 시작 (202). 잘못된 경로 400, 스캔 중 409 |
| GET | /api/scan/status | state(idle/running/done/error), 진행률, 건너뛴 파일 |
| GET | /api/catalog | 파일 목록(id, name, path, dept, modified, sheets, rows, fresh[ok/stale/copy], copy_of, data_date, error) + departments |
| GET | /api/catalog/{id} | 파일 상세(시트/컬럼) |
| POST | /api/query | 질문 → 에이전트 응답 (`agent/`) |
| GET | /api/audit | 외부 LLM으로 나간 내용(마스킹 후) |
| GET | /api/llm/status | Gemini 키 설정 여부·모델 |

## 테이블 구조
- 시트마다 `t_0001..` 테이블. 컬럼: 원본 헤더 + `_row`(엑셀 원본 행 번호), `_row_kind`('data'|'subtotal'|'total'), `_label`
- **합계 계산은 반드시 `_row_kind='data'`만** 대상으로 할 것
- 카탈로그 테이블: files / sheets / columns / value_index

## 설정
- 부서 목록: `app/config.py` `KNOWN_DEPARTMENTS` (폴더명 → 파일명 키워드 → 미분류)
- 환경변수: `TRUESOURCE_ALLOWED_ROOTS`(스캔 허용 루트, `;` 또는 `:` 구분), `TRUESOURCE_CORS_ORIGINS`

## 최신/구버전 판정
같은 이름 계열(_v2/_최종/(1)/복사본/_old 접미사 제거) 중 기준일(시트 상단 "기준일" 등, 없으면 수정일)이 가장 늦은 것 = ok, 나머지 stale, 바이트 동일 파일 = copy.

## 알려진 한계
- 캐시값 없는 수식 셀은 빈 값으로 읽힘(경고 표시)
- value_index는 텍스트 열만
- 최신 판정은 이름 기반: 서식만 다른 사본, 이름이 전혀 다른 구버전은 못 잡음. 파일명 속 버전 번호는 미사용
- .xls는 건너뜀(개수 표시). 심볼릭 링크/정션 따라가지 않음
- 30만 행 시트는 느림(약 84초/620MB)
- 셀 안의 지시문(프롬프트 인젝션)은 단순 데이터이며 LLM에는 원본 셀이 가지 않음
- 서버는 127.0.0.1로 실행하고 ALLOWED_ROOTS 지정 권장(UNC 경로 사용 시 NTLM 노출 주의)
