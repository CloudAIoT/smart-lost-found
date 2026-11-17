# 두 사진 분석·인덱스 검색 구현 계획과 인계

2026-11-16. 사용자 구현 요청에 따라 적용한 범위다. 기존 11/01 작업은 [과거 기록](implementation-plan-20261101.md)에 보존한다. 기능 계약은 [기능 명세](functional-spec.md), 저장 구조는 [ERD](erd.md)가 기준이다.

## 구현 순서와 결과

| 단계 | 반영 |
| --- | --- |
| I01 DB | Alembic 0004. registrations에 analysis_metadata, ownership_evidence, search_features 추가. GIN(jsonb_path_ops), 기존 성공 행의 공통 특징 backfill |
| I02 분석 | S3 순서 있는 두 JPEG → prepare_item → Gemini 한 번 호출 → parse_item_output → 특징·메타데이터·색인 원자 저장 |
| I03 검토 | app.review_evidence CLI. 현재 analysis_id, feature_id, 원문, 중복, 검토자·시각 검증. 모델이 verified를 생성하지 않음 |
| I04 검색 | 정규화 단서의 @> 조회. 최대 51행, 50개 초과 refine_search. 단순 단서는 의미 비교 생략, 상세 단서는 후보만 한 번 비교 |
| I05 질문 | development_v3, 새 형태·표면·구조 필드. 분석 버전·검토 목록 해시·보관 상태 재검사. 기존 완료 이력은 조회만 허용 |
| I06 실행 | BE에 imageprocess/SDK/OpenCV 포함, 개발·운영 Docker 경로 수정, AI 설정의 workflow→SSM→backend.env 전달 준비, Nginx 120초 |
| I07 오프라인 | 실제 전처리·파서 + 대역 모델, SQLite/PostgreSQL 회귀·마이그레이션·인덱스·패키징·메모리 검증 |
| I08 실검증 | 담당자가 키를 넣은 뒤 실제 두 사진의 추론 정확도·대기 시간·모델 접근 권한·질문 품질 확인. 아직 미실행 |

## AI 시험 설정

기존 DB·S3·Pi 설정은 재사용한다. 신규 AI 설정 중 공란은 키 하나다. 저장소의 backend/.env.example과 서버 설정 기본값에 반영했다.

```dotenv
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_TIMEOUT_SECONDS=30
GEMINI_MAX_OUTPUT_TOKENS=4096
IMAGE_ANALYSIS_MODEL=gemini-3.6-flash
IMAGE_ANALYSIS_TIMEOUT_SECONDS=90
IMAGE_ANALYSIS_MAX_OUTPUT_TOKENS=8192
IMAGE_ANALYSIS_MAX_BYTES=10485760
IMAGE_ANALYSIS_MAX_PIXELS=4000000
IMAGE_ANALYSIS_MAX_SIDE=1280
IMAGE_ANALYSIS_MAX_CONCURRENCY=1
```

모델명은 저장소 실험의 기본값이다. 해당 키의 모델 접근·할당량은 실호출로 확인하며 자동 대체 모델을 사용하지 않는다. 이미지 temperature=0, 추가 재시도 없음, blur/darkness는 경고로 저장한다. 키 공란은 상태 변경·S3 읽기 전 503 AI_NOT_CONFIGURED, 슬롯 점유 중은 409 ANALYSIS_BUSY다. 유효한 성공 캐시는 키 없이 읽는다.

상한은 사진당 400만 픽셀·10MiB·긴 변 1280, 동시 처리 1개·worker 1개다. 기존 MAX_IMAGE_BYTES가 더 작으면 그 값을 따른다. 현재 Pi 기본 2304×1296을 수용한다. 큰 기존 사진은 원본을 보존하고 IMAGE_TOO_LARGE로 실패한다. 320MiB 컨테이너에서 검증한다. FE는 현재 fetch에 별도 짧은 timeout이 없고 Nginx는 120초를 기다린다.

## DB와 후보 검색

원본 사진은 S3, 원본 추출값은 analysis_features, 실행 정보는 analysis_metadata, 검색 토큰은 search_features, 검토 근거는 ownership_evidence에 저장한다. found_items는 기존 보관함 개방 단계에서 생성되며 실제 보관 완료 뒤에 검색된다.

등록 시 원본과 색인을 함께 저장한다. 자연어에서 추출한 단서도 같은 정규화·별칭 규칙을 적용한다. 예를 들어 필통/pencil_case와 검정/black은 같은 토큰으로 조회된다. 상세 문구는 전체 문자열과 단어 토큰을 사용한다. 인덱스는 정확한 정규화 토큰 검색이며 미등록 동의어·번역·한국어 조사 처리는 제한적이다. 모델 의미 비교는 인덱스로 찾은 후보 안에서만 가능하다.

전체 물품 수와 무관하게 앱이 읽는 후보는 최대 51행이다. 초과 시 추가 설명을 받으며 상위 일부로 질문을 시작하지 않는다. 종류·색·브랜드 단순 조회는 의미 비교 호출 없이 처리한다. 단서 추출과 필요한 오답 생성에는 Gemini가 필요하므로 전체 API 대기 시간은 별도로 측정해야 한다.

PostgreSQL은 통계·선택도에 따라 실제 실행 계획을 고른다. 드문 특징은 GIN을 활용하지만 흔한 색처럼 많은 행이 맞는 조건은 DB 내부에서 순차 스캔을 선택할 수도 있다. 인덱스가 모든 요청의 일정한 응답 시간을 보장하지는 않는다.

## 키 없이 재현

저장소 루트에서 Python 3.12 환경에 설치한다. Windows는 UTF-8 모드로 실행한다.

```powershell
python -m pip install -r backend/requirements-dev.txt
python -X utf8 -m pytest backend/tests deploy/tests backend/app/ai_ownership_verification/tests -q
python -X utf8 -m pytest tests -q
Push-Location imageprocess
python -X utf8 -m pytest -q
Pop-Location
```

PostgreSQL 검증은 별도 테스트 DB의 TEST_DATABASE_URL을 설정한다. 테스트는 무작위 전용 스키마를 만들고 정리한다. test_search_postgres.py는 5,000개 합성 보관 물품을 넣고 실제 EXPLAIN ANALYZE에서 GIN 사용을 확인한다. SQLite는 해당 성능 검증을 대체하지 않는다.

## 담당자 실검증 순서

1. 기존 DB를 백업하고 `alembic upgrade head`를 적용한다. 0004는 새 컬럼·색인·backfill 작업이므로 운영 데이터량에 따라 실행 시간을 확인한다. downgrade는 새 세 컬럼을 잃는다.
2. 로컬은 backend/.env, 배포 경로는 production Secret GEMINI_API_KEY에 키를 넣는다. 이 작업에서는 실제 키·SSM·운영 배포를 변경하지 않았다.
3. 이미 두 사진이 저장된 등록에 분석 POST를 호출한다. ready/succeeded, warnings, DB의 8개 특징·메타데이터·색인, 반복 호출 비용 0을 확인한다.
4. 기존 보관 완료 흐름 뒤 개발용 소유 확인 API를 시험한다. 근거가 없으면 insufficient_evidence가 정상이다.
5. 사진과 원문을 검토한 목록을 `python -m app.review_evidence --registration-id UUID --file reviewed.json`으로 저장한다. 입력 JSON은 ERD 예시를 따른다. Compose 내부 `/app`에서 실행하거나, 로컬에서는 PYTHONPATH에 backend와 저장소 루트를 포함한다.
6. 새 검색을 시작해 질문·선택지·판정을 확인한다. 검토 변경 후에는 새 시도를 시작한다. 예전 v1/v2 완료 시도는 조회만 가능하며 승인 결과로 재사용하지 않는다.

반환 코드와 소유 확인 판정의 자동 연결, 공개 인증, 운영 배포는 이번 구현에 포함하지 않는다. 실제 AI 인식 정확도를 합성 응답 테스트 결과로 대체하지 않는다.


## 메모리 검사 재현

운영 이미지를 로컬에서 빌드한 뒤 아래 명령으로 네트워크 없는 컨테이너에서 실행한다. 실제 추론 품질·HTTP 송수신 메모리는 측정하지 않는다.

```powershell
docker build -f deploy/Dockerfile -t slf-analysis:local .
Get-Content -Raw backend/tests/check_image_memory.py | docker run --rm -i --memory 320m --memory-swap 320m --network none -e POSTGRES_DB=test -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test slf-analysis:local python -
```

최종 검증 수치와 제외 범위는 [구현 현황](implementation-status.md)의 2026-11-16 기록을 따른다.
