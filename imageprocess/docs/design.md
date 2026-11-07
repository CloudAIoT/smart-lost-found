# 설계 및 구현 범위

이 문서는 현재 시제품의 동작을 설명한다. 팀 백엔드 API·DB 스키마나 등록 성공 조건은 아직 확정하지 않았다.

## 처리 흐름

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

모든 필드는 필요하며 추가 필드는 거부한다. 아래 값의 의미는 현재 시제품 규칙이다. 의미 특징은 영어로 생성하도록 지시하고 브랜드와 `visible_text`는 사진 표기를 보존한다.

| 필드 | 타입 | 의미 및 확인 불가 표현 |
| --- | --- | --- |
| `category` | string | 모델이 판단한 물품 종류, 불확실하면 `unknown` |
| `color` | string[] | 관찰되는 색상, 확인 불가 시 빈 배열 |
| `material` | string | 사진에서 확인 가능한 재질, 불확실하면 `unknown` |
| `brand` | string 또는 null | 식별되는 브랜드, 불확실하면 null |
| `visible_text` | string[] | 실제 보이는 문구, 없으면 빈 배열 |
| `distinctive_features` | string[] | 부착물·무늬·형태 등 관찰 특징, 없으면 빈 배열 |
| `description` | string | 관찰 특징의 요약 |

프롬프트의 구체적인 완성 예시를 따라 생성하던 문제를 줄이기 위해 필드 타입과 스키마 중심으로 지시한다. 값의 의미 규칙은 프롬프트이며 파서는 타입·길이·필드 구성을 검증한다. 중복 키, NaN/Infinity, 누락·추가 필드와 잘못된 타입을 거부한다. JSON 객체 또는 전체가 하나의 JSON 코드 블록인 출력만 허용한다. 예외적으로 `material: ["unknown"]`처럼 문자열 하나를 담은 재질 배열만 문자열로 정규화한다. 빈 배열과 복수 재질 배열은 거부한다.

## 두 분석 경로와 비교 기록

- **Qwen/MLX:** 모델·processor를 로딩해 PIL 이미지를 입력한다. temperature 0과 기본 최대 512토큰을 사용하고, 프롬프트와 파서로 JSON 형식을 검증한다. Apple Silicon용 경로이다.
- **Gemini:** `google-genai` SDK에 전처리한 이미지와 공통 프롬프트를 전달한다. temperature 0과 기본 최대 4096토큰을 사용하고 API의 JSON Schema 옵션도 적용한다. 자동 재시도는 하지 않는다.
- **mock:** 의미 분석 없이 unknown 결과를 생성해 프로그램 실행 경로만 확인한다. 실제 backend 실패의 대체 수단으로 사용하지 않는다.

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

- 등록당 사진 2장의 입력, 두 사진 동시 분석 또는 특징 통합
- AWS 백엔드와의 이미지 전달, 등록 ID 연결, DB 저장
- 최종 카테고리 목록, 필수 특징, 확인 불가 값과 오류 계약
- 의미 정확도 평가 데이터, 합격 기준, 재촬영 및 재시도 정책
- 모델·운영 환경·비용 선택, 인증·배포·감시
- 분석 완료와 보관함 개방/소유 확인의 연결

현재 HTTP API에는 요청 인증, 작업 큐, Qwen 추론 강제 중단 기능이 없다. 공통 통신 규격은 팀 합의 후 별도 공통 문서로 확정한다. JSON 형식 검증이나 mock 결과만으로 등록 성공을 판단하지 않는다.
