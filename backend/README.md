# smart-lost-found Backend

Python 3.12, FastAPI, PostgreSQL 17, SQLAlchemy, Alembic 기반 로컬 개발 API.

등록의 카테고리와 자유 입력은 선택값이다. 업무 API는 LOCAL_DEVELOPMENT=true에서만 활성화한다.
인증이 미정이므로 localhost 바인딩을 유지한다.

저장소 루트에서 backend/.env.example을 backend/.env로 복사하고 LOCAL_DEVELOPMENT=true를 설정한다.
기존 .env는 덮어쓰지 않는다.

```powershell
docker compose --env-file backend/.env up -d --build
docker compose --env-file backend/.env exec backend alembic upgrade head
docker compose --env-file backend/.env exec backend python -m app.seed_local
docker compose --env-file backend/.env exec backend pytest -q
```

Swagger: http://127.0.0.1:8000/docs

GET /health는 서버 상태, GET /ready는 DB 연결을 확인한다.
GET /api/kiosks, GET /api/categories, POST /api/registrations,
GET /api/registrations/{id}를 제공한다.

[API 계약](../docs/api.md)을 참고한다.

두 번의 촬영·S3 미리보기·AI 분석 연결 API를 추가했다. 실제 장치/버킷 설정은 .env.example을 참고한다. AI 기본 구현은 AI_NOT_CONFIGURED를 반환한다.
