# 웹 → 백엔드 API: 개발용 계약

2026-10-12 사용자 확인을 반영한 최초 구현이다. FE·AI·Pi 전체 팀의 최종 합의로 간주하지 않는다. Pi에 보내는 요청은 [장치 규약](device-protocol.md)을 따른다.

2026-11-16: 두 사진 Gemini 분석, analysis_warnings 응답, JSONB 인덱스 후보 검색과 development_v3 질문 경로를 구현했다. 실제 키를 사용하는 추론 검증은 남아 있다.

업무 API는 모두 `/api` 아래에 있다. 로컬에서는 `LOCAL_DEVELOPMENT=true`, EC2에서는 `BUSINESS_API_ENABLED=true`로 활성화한다. 둘 다 false면 503을 반환한다. 현재 로그인·키오스크 인증은 없으며, 활성화하면 인증 없이 호출할 수 있다. `/health`, `/ready`는 이 설정과 무관하다.

## 등록과 조회

| 메서드 | 경로 | 입력·출력 |
| --- | --- | --- |
| GET | `/api/kiosks?limit=20&offset=0` | 개발용 위치 조회. ID·이름·장소 설명·위도·경도, 좌표는 NULL 허용 |
| GET | `/api/categories` | 등록된 카테고리 ID·code·name. 기본 목록 없음 |
| POST | `/api/registrations` | 아래 본문으로 생성, 201 |
| GET | `/api/registrations/{id}` | 등록 상태·선택 입력·사진 ID/순서 조회 |
| POST | `/api/registrations/{id}/shots/1` | Pi에서 첫 JPEG 촬영·S3 저장 |
| POST | `/api/registrations/{id}/shots/2` | 사용자가 방향을 바꾼 후 둘째 촬영·저장 |
| GET | `/api/registrations/{id}/shots/{1 또는 2}/preview` | `{"url":"..."}` 비공개 S3 임시 서명 URL |
| POST | `/api/registrations/{id}/analysis` | 두 사진의 특징 추출 요청·결과 저장 |
| POST | `/api/registrations/{id}/deposit/open` | 분석 성공 확인·빈 칸 배정·개방 |
| POST | `/api/registrations/{id}/deposit/complete` | `{"confirmed":true}` 사용자 완료 확인 → 문 조회 → 보관 완료 |

등록 입력:

```json
{
  "kiosk_id": "00000000-0000-0000-0000-000000000001",
  "primary_category_id": null,
  "secondary_description": "사용자가 입력한 특징",
  "found_location": "도서관 2층"
}
```

`kiosk_id`는 DB에 있어야 한다. 나머지 세 항목은 생략 가능하다. 카테고리는 DB의 ID여야 하고 secondary_description은 최대 2,000자, found_location은 최대 200자다. 습득 위치는 앞뒤 공백을 제거하고 빈 값은 null로 저장한다. 알 수 없는 필드는 422이다. 최초 등록 생성 POST 자체는 멱등 요청이 아니므로 FE는 받은 ID를 보관하고 후속 요청에 재사용한다.

생성·등록 조회·촬영·분석·개방·완료의 공통 응답:

```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "kiosk_id": "00000000-0000-0000-0000-000000000001",
  "primary_category_id": null,
  "secondary_description": "사용자가 입력한 특징",
  "found_location": "도서관 2층",
  "status": "draft",
  "analysis_status": "pending",
  "analysis_warnings": [],
  "images": [],
  "locker_number": null
}
```

사진 응답 항목은 `{"id":"UUID","shot_order":1}`이다. S3 객체 키·AI 특징은 응답하지 않는다. 미리보기 URL은 별도로 발급하고 영구 저장하지 않는다. `locker_number`는 개방 성공이 저장된 `awaiting_deposit`/`stored`에서만 반환한다.

## 순서와 중복 요청

- 기본 상태: `draft → capturing → draft → analyzing → ready → opening → awaiting_deposit → stored`. 실패한 분석은 `draft` 및 `analysis_status=failed`로 돌아간다. 분석 상태는 `pending/running/succeeded/failed`이다.
- 첫 사진 저장 전 둘째 사진을 요청하면 409이다. 같은 사진 번호의 저장 완료 요청을 반복하면 기존 결과를 반환한다. 재촬영은 아직 지원하지 않는다.
- 분석은 저장된 두 사진을 사용한다. 결과의 이미지 ID·순서가 입력과 일치해야 한다. AI 호출 실패·미구성·잘못된 결과는 개방을 허용하지 않는다. 성공한 분석의 반복 요청은 저장 결과를 사용한다.
- 개방 전 빈 칸을 예약한다. 현재는 작은 칸 번호부터 선택하는 내부 구현이며 사용자에게 특정 칸을 보장하지 않는다. 실제 선택·해제 정책은 협의 대상이다.
- 완료된 개방을 재요청하면 같은 결과를 반환한다. 진행 중인 촬영·분석·개방은 409이며 다른 명령을 자동 실행하지 않는다.
- 개방 성공은 투입 완료가 아니다. `confirmed=true`와 센서 `closed`를 함께 확인한다. 중간의 `open` 관측은 요구하지 않는다. 완료 재요청은 기존 완료 결과를 반환한다.
- 촬영·개방 실패는 `blocked`로 두고 자동 재시도하지 않는다. 통신 단절·잘못된 성공 응답은 명령 `unknown`, 명시적 장치 오류는 `failed`로 저장한다. 개방 배정은 유지한다. 실패 후 복구·예약 해제·재촬영 정책은 미정이다.
- 프로세스 종료나 DB 장애로 외부 호출 결과를 기록하지 못하면 `capturing/analyzing/opening` 및 `sent`가 남을 수 있다. 재요청으로 하드웨어를 다시 실행하지 않는다. 운영 복구 API는 아직 없다.

## 수령 (분실자)

소유 확인을 통과한 물건에 일회성 반환 코드를 발급하고, 키오스크에서 그 코드로 보관함을 연다.
흐름과 "개방 성공 뒤에 코드를 사용 처리한다"는 원칙은 [장치 규약 6.2](device-protocol.md)를 따른다.

| 메서드 | 경로 | 입력·출력 |
| --- | --- | --- |
| POST | `/api/found-items/{found_item_id}/return-code` | 보관 중인 물건에 201 `{"found_item_id","code","expires_at"}` |
| POST | `/api/pickup/open` | `{"code":"...","kiosk_id":"UUID"}` → 코드 확인·보관함 개방 |
| POST | `/api/pickup/{found_item_id}/complete` | `{"confirmed":true}` → 문 조회 → 수령 완료 |

개방·완료의 공통 응답:

```json
{
  "found_item_id": "22222222-2222-2222-2222-222222222222",
  "kiosk_id": "00000000-0000-0000-0000-000000000001",
  "locker_number": 1,
  "item_status": "stored",
  "assignment_status": "awaiting_pickup"
}
```

- 코드 평문은 발급 응답에서 한 번만 받는다. 서버는 SHA-256 해시만 저장하므로 재조회 경로가 없고, 잃어버리면 재발급해야 한다. 재발급은 이전 코드를 즉시 `revoked`로 만든다.
- 코드는 8자리 Base32(A~Z, 2~7)다. 입력의 공백·하이픈·소문자는 서버가 정규화하므로 FE가 맞출 필요는 없다.
- 현재 발급에는 소유 확인이 없다. 보관 중(`stored`)이기만 하면 누구나 코드를 받을 수 있다. 질문·답변 평가(A04)가 정해지면 이 호출의 선행 조건이 된다. 그 전까지 공개 배포하지 않는다.
- 물건 상태는 `stored → retrieved`, 배정 상태는 `stored → awaiting_pickup → retrieved`다. 등록(`registrations`) 상태는 수령으로 바뀌지 않는다.
- 개방 성공 시 코드를 `used`로 처리한다. 완료된 개방을 재요청하면 문을 다시 열지 않고 같은 결과를 반환하므로 FE의 중복 클릭·새로고침은 안전하다.
- 명확한 장치 실패는 코드를 그대로 두어 다시 시도할 수 있다. 통신 단절 등 결과 불명은 문이 이미 열렸을 수 있어 코드를 `locked`로 두고 같은 코드의 재개방을 막는다. 운영 복구 API는 아직 없다.
- 완료는 `confirmed=true`와 센서 `closed`를 함께 확인한다. 완료 시 배정이 종료되어 해당 칸이 즉시 다시 배정 가능해진다.
- 물건을 꺼냈는지는 확인하지 않는다. 문 센서는 개폐만 알리므로, 물건을 두고 문만 닫은 뒤 완료를 누르면 그 칸이 빈 칸으로 반납된다. `confirmed=true`가 사용자 선언이며 FE 완료 화면이 이를 분명히 물어야 한다. 투입 완료도 같은 한계를 갖지만, 잘못된 수령 완료는 물건이 남은 칸을 재배정한다는 점에서 영향이 더 크다. 보관함 내부 감지 수단은 하드웨어 협의 대상이다.
- 코드의 대상 키오스크가 요청의 `kiosk_id`와 다르면 거절한다. 이는 대상 검사이고 호출자 인증이 아니다. 키오스크 세션은 여전히 미구현이다.

## 오류

업무/연동 오류: `{"detail":{"code":"ERROR_CODE"}}`. 입력 검증 422는 FastAPI 표준 오류 배열이다. 외부 오류 원문·키·주소를 응답에 복사하지 않는다.

| HTTP | 주요 code |
| --- | --- |
| 404 | `KIOSK_NOT_FOUND`, `CATEGORY_NOT_FOUND`, `REGISTRATION_NOT_FOUND`, `IMAGE_NOT_FOUND`, `ITEM_NOT_FOUND`, `RETURN_CODE_NOT_FOUND` |
| 409 | `INVALID_REGISTRATION_STATE`, `SHOT_OUT_OF_ORDER`, `TWO_IMAGES_REQUIRED`, `ANALYSIS_REQUIRED`, `NO_AVAILABLE_LOCKER`, `ALLOCATION_CONFLICT`, `DEPOSIT_NOT_OPENED`, `DOOR_NOT_CLOSED`, `ITEM_NOT_STORED`, `PICKUP_NOT_OPENED`, `RETURN_CODE_EXPIRED`, `RETURN_CODE_ALREADY_USED`, `RETURN_CODE_UNUSABLE`, `RETURN_CODE_IN_PROGRESS`, `RETURN_CODE_WRONG_KIOSK`, `RETURN_CODE_CONFLICT` |
| 502 | `DEVICE_UNREACHABLE`, `INVALID_DEVICE_RESPONSE`, `DEVICE_ROLE_MISMATCH`, `DEVICE_RESPONSE_TOO_LARGE`, `INVALID_IMAGE`, `IMAGE_STORAGE_FAILED`, `INVALID_ANALYSIS_RESULT`, 규약의 Pi 오류 code |
| 503 | `BUSINESS_API_DISABLED`, `AI_NOT_CONFIGURED`, `DEVICE_NOT_CONFIGURED`, `CAPTURE_NOT_CONFIGURED`, `IMAGE_STORAGE_NOT_CONFIGURED`, `RETURN_CODE_NOT_CONFIGURED` |

운영 공개 검색·질문 API는 제공하지 않는다. 로컬 개발용 소유 확인 API는 아래와 같이 이미 구현되어 있다. 현재 반환 코드 발급에는 이 결과가 연결되지 않았으며 인증·소유 확인 없이 호출된다. 내부 search_stored_items의 정확 필터와 아래 Gemini 유사 검색은 별도 경로다.

## 소유 확인 개발용 API: 현재 구현

LOCAL_DEVELOPMENT=true일 때만 활성화된다. BUSINESS_API_ENABLED만으로 활성화되지 않는다.

| 메서드·경로 | 본문·응답 |
| --- | --- |
| POST /api/ownership/attempts | {description} → question/completed/needs_clues/no_candidates/refine_search |
| GET /api/ownership/attempts/{id} | 현재 질문 또는 완료 상태 |
| POST /api/ownership/attempts/{id}/answers | {question_id, option_id} → 다음 질문 또는 completed |

현재 development_v3는 Gemini로 단서를 추출하고 DB 인덱스로 후보를 찾는다. 종류·색·브랜드만 있으면 유사 후보 모델 호출을 생략하고, 상세 단서가 있으면 50개 이하 후보만 의미 비교한다. 50개 초과면 추가 설명을 요청한다. 필요한 오답 선택지는 Gemini로 생성하고 서버가 5지선다 답을 채점한다. 판정은 supported/not_confirmed/insufficient_evidence다. 상세 현재 계약은 [소유 확인 워크플로](../backend/app/ai_ownership_verification/workflow.md)를 따른다.

## 두 사진 분석: 현재 구현

- POST /api/registrations/{id}/analysis 경로를 유지하고 본문 없이 호출한다. DB의 순서 있는 두 S3 사진으로 prepare_item → Gemini 1회 요청 → parse_item_output → DB 저장을 수행한다.
- 성공은 기존 RegistrationView에 status=ready, analysis_status=succeeded이며, 공통 응답에 analysis_warnings 배열을 반환한다. 분석 특징·검토 근거·S3 키는 반환하지 않는다. 분석으로 보관함을 열거나 질문을 만들지 않는다.
- 키 공란은 상태 변경 전 503 AI_NOT_CONFIGURED, 분석 슬롯 사용 중은 409 ANALYSIS_BUSY다. 같은 사진의 유효한 새 규격 성공 결과는 재호출 없이 반환한다. 알려진 실행 실패는 draft/failed로 복구하고 사진을 유지한다.
- 소유 확인의 경로·요청 본문은 유지한다. 내부 검색은 새 8개 특징+location의 정규화 인덱스를 사용하고 검토 근거 컬럼을 읽으며 정책 버전은 development_v3로 구분한다.
- DB 저장 형태·오류 코드·legacy 호환·오프라인/실키 검증의 원본은 [기능 명세](../backend/docs/functional-spec.md), [ERD](../backend/docs/erd.md), [구현 계획](../backend/docs/implementation-plan-20261116.md)이다. 구체적 시험 설정은 계획에 고정하고 GEMINI_API_KEY만 나중에 입력한다.
