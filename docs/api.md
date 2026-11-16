# 웹 → 백엔드 API: 개발용 계약

2026-10-12 사용자 확인을 반영한 최초 구현이다. FE·AI·Pi 전체 팀의 최종 합의로 간주하지 않는다. Pi에 보내는 요청은 [장치 규약](device-protocol.md)을 따른다.

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
  "secondary_description": "사용자가 입력한 특징"
}
```

`kiosk_id`는 DB에 있어야 한다. 나머지 두 항목은 생략 가능하다. 카테고리를 지정하면 DB에 있는 ID여야 한다. 자유 입력은 개발용 요청 크기 제한으로 최대 2,000자이며 최종 UI 계약은 별도 협의한다. 알 수 없는 필드는 422이다. 최초 등록 생성 POST 자체는 멱등 요청이 아니므로 FE는 받은 ID를 보관하고 후속 요청에 재사용한다.

생성·등록 조회·촬영·분석·개방·완료의 공통 응답:

```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "kiosk_id": "00000000-0000-0000-0000-000000000001",
  "primary_category_id": null,
  "secondary_description": "사용자가 입력한 특징",
  "status": "draft",
  "analysis_status": "pending",
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

공개 검색·질문/답변 API는 제공하지 않는다. 반환 코드·수령은 위 절에 있으나 코드 발급의 승인 근거가 되는 소유 확인 평가는 미구현이다. 현재 발급은 인증·소유 확인 없이 호출된다. 내부 `search_stored_items`만 보관 완료 물건의 카테고리·현재 키오스크 정확 필터와 페이지네이션을 시험할 수 있다. 이 반환 구조를 공개 필드 합의로 해석하지 않는다.
