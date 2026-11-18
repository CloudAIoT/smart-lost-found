# smart-lost-found Backend

EC2 자동 배포와 GitHub 환경설정 관리는 [운영 배포 안내](../deploy/README.md)를 참고한다. 아래 Compose는 로컬 개발용이며 운영에서는 별도 이미지·Compose를 사용한다.

분실물 관리 서비스의 백엔드. 상태 확인과 로컬 개발용 등록·촬영·분석 연결·보관 완료·반환 코드·수령 API를 구현했다.

백엔드 [ERD·기능 명세와 검토 기록](docs/README.md)은 `docs/`에서 관리한다. 원문 요구, 사용자 결정, 설계 제안과 미정 사항을 구분한 개발 전 검토 문서이다.

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

## 로컬 실행

저장소 루트에서 실행한다. `.env`가 이미 있으면 덮어쓰지 않는다.

```powershell
Copy-Item backend/.env.example backend/.env
docker compose --env-file backend/.env up -d --build
docker compose --env-file backend/.env exec backend alembic upgrade head
```

업무 API는 기본적으로 비활성화되어 503을 반환한다. 로컬 개발 시 `backend/.env`에서 `LOCAL_DEVELOPMENT=true`로 바꾼 뒤 `docker compose --env-file backend/.env up -d --force-recreate backend`를 실행한다. 로컬 Compose는 `127.0.0.1` 바인딩을 유지한다. EC2에서는 로컬 모드를 켜지 않고 `BUSINESS_API_ENABLED`로 업무 API를 활성화한다. 아직 인증은 구현되지 않았다.

```powershell
docker compose --env-file backend/.env exec backend python -m app.seed_local
docker compose --env-file backend/.env exec backend pytest -q
```

시드는 개발용 키오스크 1개와 2칸을 만든다. 반복 실행해도 중복 생성하지 않으며 카테고리 목록은 임의로 확정하지 않는다. 실제 키오스크의 위치·카테고리 관리 API는 이번 범위 밖이다.

저장소 루트에서 Python 3.12 가상환경에 `pip install -r backend/requirements-dev.txt -r imageprocess/requirements-core.txt`로 준비하고 `backend`에서 `python -m pytest -q`를 실행한다. 기본 테스트는 SQLite와 테스트 전용 Pi·S3·AI를 사용한다.

## 업무 API와 연동 상태

### Swagger에서 직접 시험하기

위 실행·마이그레이션·시드 명령을 마친 뒤 [Swagger UI](http://127.0.0.1:8000/docs)를 연다. 각 API의 **Try it out → Execute**로 요청한다.

1. `GET /ready`가 `200`인지 확인한다.
2. `GET /api/kiosks`로 개발용 키오스크를 확인한다.
3. `POST /api/registrations`에 아래 본문을 보내고 응답의 `id`를 복사한다.
4. `GET /api/registrations/{registration_id}`에 복사한 ID를 넣어 저장된 입력과 `draft` 상태를 확인한다.

```json
{
  "kiosk_id": "00000000-0000-0000-0000-000000000001",
  "secondary_description": "테스트용 검은색 지갑"
}
```

카테고리는 생략할 수 있다. 실제 Pi·S3 설정과 AI 구현이 없으면 촬영·분석·개방까지 Swagger에서 성공시키지는 못한다. 전체 모의 흐름을 직접 확인하려면 다음 명령을 실행한다. 가짜 장치는 테스트 안에서만 사용한다.

```powershell
docker compose --env-file backend/.env exec backend pytest -v tests/test_registration.py::test_full_deposit_and_duplicates
```

서버 종료는 저장소 루트에서 `docker compose --env-file backend/.env stop`을 실행한다. DB 볼륨은 유지된다.

### 연결 상태

경로·본문·오류·중복 처리 계약은 [팀 API의 로컬 개발 계약](../docs/api.md), 구현 범위와 장애 복구 제한은 [구현 현황](docs/implementation-status.md)에 정리했다.

- 등록 입력의 카테고리·자유 특징은 현재 선택값이다. 최종 필수 여부와 공개 검색 계약은 미정이다.
- 두 번의 수동 촬영 요청 → 비공개 S3 저장 → AI 특징 추출 → 등록 개방 → 사용자 완료 확인 및 문 조회 순서이다.
- Pi HTTP와 S3 어댑터는 구현되어 있으나 실제 장치·버킷 연결 검증은 별도로 필요하다. `.env.example`의 크기·TTL·시간 제한 예시는 팀 합의값이 아니다. 실제 연결 전 명시적으로 설정한다.
- S3 버킷은 비공개 및 Public Access Block을 설정한다. boto3의 환경변수 또는 역할 기반 인증을 사용한다. Compose는 `backend/.env`의 환경변수를 컨테이너에 전달한다. 키를 저장소에 커밋하지 않는다.
- AI는 `app.api.get_analyzer`에서 `GeminiPhotoAnalyzer`를 연결한다. `GEMINI_API_KEY`와 `GEMINI_MODEL`이 있으면 등록 ID에 연결된 S3 JPEG 두 장을 전처리하고 Gemini 결과의 8개 특징을 검증해 `analysis_features`에 저장한다. 사진 ID는 `analysis_input_image_ids`에 순서대로 저장한다.
- 키·모델이 없으면 `AI_NOT_CONFIGURED`를 반환한다. 사진 분석만 `IMAGE_ANALYSIS_TIMEOUT_SECONDS`(기본 90초)를 사용하며 소유 확인 텍스트 요청은 기존 시간 제한을 유지한다. 실제 DB 저장 결과는 서버에서 `python -m app.inspect_analysis REGISTRATION_ID --require-succeeded`로 조회한다. [API와 EC2 확인 방법](../docs/api.md#등록-id로-사진-분석db-저장-확인)을 참고한다.
- 소유 확인 통과 후 반환 코드 발급과 수령 API가 연결되어 있다. 검토된 소유 확인 근거의 준비는 사진 특징 추출과 별도다.
