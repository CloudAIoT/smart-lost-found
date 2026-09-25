# smart-lost-found Backend

분실물 관리 서비스의 백엔드. 현재는 개발 환경과 상태 확인 API만 구성되어 있다.

## 개발 환경

- Python 3.12 · FastAPI · Uvicorn
- PostgreSQL 17 · SQLAlchemy 2.x · psycopg 3
- pydantic-settings · Alembic
- pytest · httpx
- Docker Compose

환경변수는 `backend/.env`에서 관리한다. Docker Compose로 백엔드와 DB를 실행하며, 코드 수정 시 자동 재시작한다.

## API

| Method | Path | 설명 |
| --- | --- | --- |
| GET | `/health` | 서버 상태 확인. DB 연결과 무관하게 200 반환 |
| GET | `/ready` | DB 연결 확인. 성공 시 200, 실패 시 503 반환 |

기본 주소: `http://127.0.0.1:8000`

- Swagger UI: `/docs`
- OpenAPI: `/openapi.json`
