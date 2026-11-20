# 설계 및 구현 범위

이 문서는 현재 시제품의 동작을 설명한다. 팀 백엔드 API·DB 스키마나 등록 성공 조건은 아직 확정하지 않았다.

## 백엔드용 두 사진 처리

`ImageInput` 두 개(사진 ID + bytes)와 등록 ID·카테고리를 `prepare_item`에 전달한다. 순서·ID·크기·형식을 검증하고 두 PIL 이미지와 통합 프롬프트/스키마를 반환한다. 모델 호출은 백엔드가 수행하고 결과 JSON 텍스트는 `parse_item_output`으로 검증한다. 시각적 관찰이 전혀 없는 결과는 `INSUFFICIENT_VISUAL_FEATURES`로 거부한다. [입출력과 연결 예제](backend-handoff.md)를 참고한다.

두 사진에는 같은 부착물이 반복될 수 있으므로 개수를 합산하지 않고 한 물건의 특징으로 통합하도록 지시한다. 충돌하는 공통 값은 null로 남기고 다른 물건이면 결과를 섞지 않도록 한다. 실제 동일 물건 판별과 특징 사실성을 보장하는 별도 검증기는 아니다.

## 기존 단일 사진 실험 흐름

```text
사진 파일 또는 multipart image + 선택적 category
  → 크기·형식·픽셀 수 확인
  → EXIF 회전 / RGB 변환 / 비율 유지 축소
  → 흐림·어두움 확인
  → 공통 프롬프트 + 카테고리 힌트 + 사진
  → Qwen(MLX) 또는 Gemini
  → JSON 파싱 / 엄격한 특징 스키마 검증
  → CLI JSON 저장 또는 HTTP 응답
```

카테고리 입력은 JSON 데이터로 구분해 프롬프트에 넣는다. 알려진 한글 카테고리를 영어 라벨로 변환하되 원문도 결과에 유지한다. 모델이 사진과 명백한 모순을 발견하면 사진을 따르도록 지시한다. 이 지시는 의미 정확성이나 입력 오염 방지를 보장하는 별도 검증기는 아니다.

## 특징 스키마

현재 규격 `fixed_common_v2`는 공통 필드 7개와 특이 특징 필드 1개로 구성한다. 모든 키가 필요하며 추가 키는 거부한다. 각 값은 null을 허용한다.

| 필드 | 타입 | 의미 |
|---|---|---|
| `category` | string 또는 null | 모델이 판단한 종류. 영어 snake_case |
| `color` | string[] 또는 null | 주요 색상. 영어 기본 색상명, 비중이 큰 색부터 |
| `shape` | string 또는 null | 전체 형태. 한국어 |
| `surface_texture` | string 또는 null | 눈에 보이는 질감·광택. 한국어 |
| `brand` | string 또는 null | 글자·로고·특징적 디자인으로 식별한 브랜드 |
| `visible_text` | string[] 또는 null | 실제 읽히는 문구 원문 |
| `structure` | string[] 또는 null | 보이는 개폐 방식·손잡이·연결부 등 주요 구조. 한국어 |
| `distinctive_features` | string[] 또는 null | 물품별 특이 사항을 한 목록에 정리. 캐릭터 식별, 무늬·부착물·흔적과 위치 등. 한국어 |

null은 미확인·판단 불가를 뜻하며 실제로 해당 특징이 없다는 증거가 아니다. 모든 목록형 필드의 빈 배열은 null로 통일한다. 누락된 키를 임의로 채우지 않는다. 이전 규격의 `material`과 `description`은 새 추론 출력에서 제거했다. 정확한 원단 조성이나 천연/인조가죽 구분 대신 관찰 가능한 표면 질감을 우선 다룬다.

브랜드는 글자뿐 아니라 로고·특징적 디자인을 모델의 시각 지식으로 인식하도록 허용한다. 인형 등은 실루엣·귀·주둥이·자수·표정·라벨을 종합하여 캐릭터명과 알고 있는 작품명을 특이 특징에 관찰 근거와 함께 정리하도록 한다. 구분하기 어려우면 명칭을 단정하지 않고 외형만 기술한다. 캐릭터 정체성만으로 제조사를 추측하지 않는다. 인식한 이름을 실제로 읽힌 글자와 혼동하지 않는다. 정답 이름이나 후보 목록을 프롬프트에 심지 않는다.

공통 특징과 특이 특징의 단순 중복을 피하고, 케이블 감김·노출된 탈착식 내용물 같은 일시 상태를 지속적인 식별 특징으로 삼지 않도록 지시한다. 이는 프롬프트의 의미 지침이며 사실성을 보장하는 검증기는 아니다.

완성 예시를 따라 생성하던 문제를 줄이기 위해 필드 타입과 스키마 중심으로 지시한다. 파서는 타입·길이·필드 구성을 검증하며 중복 키, NaN/Infinity, 누락·추가 키와 잘못된 타입을 거부한다. JSON 객체 또는 전체가 하나의 JSON 코드 블록인 출력만 허용한다. 이전 결과는 오프라인 비교 도구에서 별도 `LegacyFeatures`로 검증하며, 예전 필드를 새 값으로 추측해서 변환하지 않는다.

## 두 분석 경로와 비교 기록

- **Qwen/MLX:** 모델·processor를 로딩해 PIL 이미지를 입력한다. temperature 0과 기본 최대 512토큰을 사용하고, 프롬프트와 파서로 JSON 형식을 검증한다. Apple Silicon용 경로이다.
- **Gemini:** `google-genai` SDK에 전처리한 이미지와 공통 프롬프트를 전달한다. temperature 0과 기본 최대 4096토큰을 사용하고 API의 JSON Schema 옵션도 적용한다. 자동 재시도는 하지 않는다.
- **mock:** 모든 특징 값이 null인 결과와 `MOCK_INFERENCE` 경고를 생성해 프로그램 실행 경로만 확인한다. 실제 backend 실패의 대체 수단으로 사용하지 않는다.

CLI는 실행마다 모델/클라이언트를 준비하고 Gemini 클라이언트를 종료한다. HTTP 서버는 시작 시 준비하고 재사용한다. 실제 모델 호출과 JSON 파싱 실패는 오류로 반환한다. Gemini의 응답 차단·출력 잘림도 성공으로 처리하지 않는다.

새 CLI 결과에는 원본 이미지 SHA-256, 전처리 RGB 픽셀 SHA-256, 크기, 프롬프트 SHA-256, temperature·출력 한도·JSON 강제 방식을 기록한다. Gemini 응답에 사용량이 있으면 입력·출력·추론·전체 토큰 수를 보존한다. 비용 계산은 하지 않는다. 결과 파일에는 실제 물품 문구가 포함될 수 있으므로 기본적으로 Git에서 제외한다.

## 로컬 HTTP 인터페이스

| 경로 | 입력 및 동작 |
| --- | --- |
| `GET /health` | backend와 준비 상태를 반환. 준비 시 200, 미준비 시 503 |
| `POST /analyze` | multipart 필수 `image`, 선택 `category`. 성공 시 200과 검증된 특징 |

성공 응답은 `success`, `request_id`, `input_category`, `data`, `processing_time_ms`, `warnings`를 포함한다. 실패 응답은 `success: false`, `request_id`, `error.code`, `error.message`를 포함한다. CLI의 비교용 메타데이터·사용량은 현재 HTTP 응답에 포함하지 않는다.

`X-Request-ID`는 영문·숫자·`._:-`의 1–128자만 재사용하고 없거나 부적절하면 UUID를 생성한다. 응답 헤더에도 반환한다. 추론은 전용 worker 하나에서 순서대로 실행하며 다른 분석이 진행 중이면 `SERVER_BUSY`로 거부한다. 서버의 health 경로는 추론과 별도로 응답한다.

Gemini의 `model_loaded=true`는 클라이언트 초기화 여부이다. 키 유효성·모델 접근권한·원격 연결을 확인한 상태가 아니다. mock의 준비 상태도 실제 모델 준비와 구분해야 한다.

## 입력 제한과 오류

파일은 JPEG/PNG/WEBP 실제 형식과 무결성을 검사한다. 기본 10MiB·2,500만 픽셀 제한을 적용하고 긴 변 1280 이하로 축소한다. HTTP 전체 본문 한도는 파일 한도에 multipart 여유 64KiB를 더한다. 흐림·어두움 기준은 기본적으로 경고만 반환하며 `REJECT_LOW_QUALITY=true`일 때 거부한다. 촬영 환경에 맞춘 기준 검증은 남아 있다.

| 주요 오류 | HTTP | 의미 |
| --- | --- | --- |
| `IMAGE_TOO_LARGE` | 413 | 파일·픽셀 또는 요청 크기 초과 |
| `INVALID_IMAGE` / `INVALID_CATEGORY` | 422 | 이미지 또는 카테고리 입력 오류 |
| `IMAGE_LOW_QUALITY` | 422 | 거부 설정에서 흐림·어두움 감지 |
| `REQUEST_VALIDATION_ERROR` | 422 | 필수 multipart 필드 등 요청 형식 오류 |
| `INVALID_MODEL_OUTPUT` | 502 | 특징 JSON 검증 실패 |
| `AI_INFERENCE_FAILED` | 502 | 모델 실행 실패 |
| `MODEL_NOT_READY` / `SERVER_BUSY` | 503 | 준비 실패 또는 다른 분석 진행 중 |

Gemini 오류는 `GEMINI_API_KEY_MISSING`, `GEMINI_DEPENDENCY_MISSING`, `GEMINI_REQUEST_REJECTED`, `GEMINI_MODEL_NOT_FOUND`, `GEMINI_RATE_LIMITED`, `GEMINI_RESPONSE_BLOCKED`, `GEMINI_OUTPUT_TRUNCATED`, `GEMINI_API_FAILED` 등으로 구분한다. SDK 예외 원문을 응답과 로그에 노출하지 않는다. 로컬 파일 읽기·저장 오류는 CLI의 `IMAGE_READ_FAILED` / `OUTPUT_WRITE_FAILED`로 반환한다.

서버는 처리 단계·시간·오류 코드만 기록하고 원본 사진·파일명·특징 결과·키를 일반 로그에 기록하지 않는다. 원본 이미지를 앱에서 별도 저장하지 않지만 multipart 처리 중 임시 파일이 만들어질 수 있다.

## 미구현 및 팀 협의

- 실제 등록 사진 2장을 사용한 정확도·시간·중복 계수·충돌 처리 평가
- 백엔드 담당자의 모델/HTTP API와 S3·DB·EC2 연결. 처리 함수는 입력 등록·이미지 ID를 유지함
- 최종 카테고리 목록, 필수 특징, 확인 불가 값과 오류 계약
- 의미 정확도 평가 데이터, 합격 기준, 재촬영 및 재시도 정책
- 모델·운영 환경·비용 선택, 인증·배포·감시
- 분석 완료와 보관함 개방/소유 확인의 연결

현재 HTTP API에는 요청 인증, 작업 큐, Qwen 추론 강제 중단 기능이 없다. 공통 통신 규격은 팀 합의 후 별도 공통 문서로 확정한다. JSON 형식 검증이나 mock 결과만으로 등록 성공을 판단하지 않는다.

단일 사진 10종의 결과는 [실험 보고서](experiments/single-photo-10-cases.md)에 정리했다. 이 결과로 두 사진 처리 정확도를 입증하지 않는다.
