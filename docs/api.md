# 웹 → 백엔드 API 계약

2026-11-21 현재 구현 기준. 화면별 흐름, 전체 21개 API의 요청·응답 필드, 상태 전이, 오류·중복 요청 처리와 FE 인수 기준은 [프론트 연동을 위한 백엔드 기능 명세](../backend/docs/functional-spec.md)를 따른다. 이 문서는 API 빠른 참조와 운영자 DB 확인 방법을 제공한다. Pi에 보내는 요청은 [장치 규약](device-protocol.md)을 따른다.

업무 API는 모두 `/api` 아래에 있다. 로컬에서는 `LOCAL_DEVELOPMENT=true`, EC2에서는 `BUSINESS_API_ENABLED=true`로 활성화한다. 둘 다 false면 503을 반환한다. 현재 로그인·키오스크 인증은 없으며, 활성화하면 인증 없이 호출할 수 있다. `/health`, `/ready`는 이 설정과 무관하다.

Pi가 없는 환경에서는 `DEV_FAKE_DEVICE=true`로 촬영·개방·문 조회를 개발용 가짜 장치가 대신한다. 촬영 응답은
더미 JPEG 2장이고 문은 항상 `closed`다. 나머지 상태 전이·S3 저장·미리보기 URL은 실제와 같으므로 FE는 계약을
그대로 따라갈 수 있다. 사진 분석은 이 더미 사진을 그대로 읽으므로 분석 결과도 더미 사진의 내용이 된다.
설정은 [EC2 배포](../deploy/README.md)에 있다.

FE의 최초 키오스크 선택·저장·복원은 [FE-K01](web-structure.md#fe-k01-키오스크-최초-1회-설정)을 따른다. 검증·확정된 선택값을 등록 생성과 수령 개방 요청의 기존 `kiosk_id`에 사용하며 API 필드는 추가하지 않는다. 브라우저의 선택값은 호출자 인증 근거가 아니고, 서버→Pi 인증 키를 FE에 전달하지 않는다. 기기 페어링·인증 API는 후속 설계 범위다.

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
  "found_location": "도서관 입구"
}
```

`kiosk_id`는 DB에 있어야 한다. 나머지 필드는 생략할 수 있다. 카테고리를 지정하면 DB에 있는 ID여야 한다. 추가 설명은 최대 2,000자, 습득 위치는 최대 200자다. 알 수 없는 필드는 422다. 최초 등록 후 FE는 받은 ID를 후속 요청에 재사용한다.

생성·등록 조회·촬영·분석·개방·완료의 공통 응답:

```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "kiosk_id": "00000000-0000-0000-0000-000000000001",
  "primary_category_id": null,
  "secondary_description": "사용자가 입력한 특징",
  "found_location": "도서관 입구",
  "status": "draft",
  "analysis_status": "pending",
  "images": [],
  "locker_number": null
}
```

사진 응답 항목은 `{"id":"UUID","shot_order":1}`이다. S3 객체 키·AI 특징은 응답하지 않는다. 미리보기 URL은 별도로 발급하고 영구 저장하지 않는다. `locker_number`는 개방 성공이 저장된 `awaiting_deposit`/`stored` 상태에서 종료되지 않은 사물함 배정이 있을 때만 반환한다.

수령 완료 후에도 등록 조회는 200으로 응답한다. 등록 `status`는 `stored`로 유지되지만 배정이 종료되므로 `locker_number`는 `null`이다. 사진 목록과 분석 상태 등 등록 정보는 유지되며, 같은 칸에 다른 물품이 배정되어도 이전 등록의 `locker_number`는 `null`이다. 수령 완료 여부는 수령 API의 `item_status`/`assignment_status`가 `retrieved`인지로 확인한다.

## 순서와 중복 요청

- 기본 상태: `draft → capturing → draft → analyzing → ready → opening → awaiting_deposit → stored`. 실패한 분석은 `draft` 및 `analysis_status=failed`로 돌아간다. 분석 상태는 `pending/running/succeeded/failed`이다.
- 첫 사진 저장 전 둘째 사진을 요청하면 409이다. 같은 사진 번호의 저장 완료 요청을 반복하면 기존 결과를 반환한다. 재촬영은 아직 지원하지 않는다.
- 분석은 저장된 두 사진을 사용한다. 결과의 이미지 ID·순서가 입력과 일치해야 한다. AI 호출 실패·미구성·잘못된 결과는 개방을 허용하지 않는다. 성공한 분석의 반복 요청은 저장 결과를 사용한다.
- 개방 전 빈 칸을 예약한다. 현재는 작은 칸 번호부터 선택하는 내부 구현이며 사용자에게 특정 칸을 보장하지 않는다. 실제 선택·해제 정책은 협의 대상이다.
- 완료된 개방을 재요청하면 같은 결과를 반환한다. 진행 중인 촬영·분석·개방은 409이며 다른 명령을 자동 실행하지 않는다.
- 개방 성공은 투입 완료가 아니다. `confirmed=true`와 센서 `closed`를 함께 확인한다. 중간의 `open` 관측은 요구하지 않는다. 완료 재요청은 기존 완료 결과를 반환한다.
- 촬영·개방 실패는 `blocked`로 두고 자동 재시도하지 않는다. 통신 단절·잘못된 성공 응답은 명령 `unknown`, 명시적 장치 오류는 `failed`로 저장한다. 개방 배정은 유지한다. 실패 후 복구·예약 해제·재촬영 정책은 미정이다.
- 프로세스 종료나 DB 장애로 외부 호출 결과를 기록하지 못하면 `capturing/analyzing/opening` 및 `sent`가 남을 수 있다. 재요청으로 하드웨어를 다시 실행하지 않는다. 운영 복구 API는 아직 없다.

## 등록 ID로 사진 분석·DB 저장 확인

`POST /api/registrations/{id}/analysis`는 요청 본문 없이 호출한다. 서버가 `item_images`에서 해당 등록의 사진 2장을 `shot_order` 순서로 조회하고, 저장된 객체 키로 S3 JPEG를 읽는다. 두 사진 전처리 → Gemini 통합 요청 → 특징 JSON 검증 후 `registrations.analysis_features`에 아래 8개 필드를 직접 저장한다. `data`로 다시 감싸지 않는다.

```json
{
  "category": "laptop",
  "color": ["silver"],
  "shape": "얇은 직사각형",
  "surface_texture": "매끄러운 표면",
  "brand": "Samsung",
  "visible_text": ["SAMSUNG"],
  "structure": ["힌지"],
  "distinctive_features": null
}
```

이는 저장 형식 예시다. 관찰할 수 없는 특징은 `null`이며, `analysis_input_image_ids`에는 실제 DB 사진 ID 2개를 촬영 순서대로 기록한다. 분석 성공 응답은 기존 등록 응답과 같으며 `status=ready`, `analysis_status=succeeded`가 된다. 전체 특징은 비공개 소유 확인 근거가 될 수 있어 공개 API 응답에는 포함하지 않는다.

실패한 분석은 `draft/failed`로 돌아가 같은 요청으로 재시도할 수 있다. `ready/succeeded` 상태에서 재요청하면 저장된 성공 상태를 반환하며 S3·Gemini를 다시 호출하지 않는다. 보관이 시작된 등록을 재분석하는 API는 제공하지 않는다.

EC2에서 실제 저장 JSON을 확인하려면 등록 분석 요청 후 운영자 DB 조회 명령을 사용한다. 이 명령은 AI·장치를 호출하거나 DB를 변경하지 않는다. `verified=true`는 분석 성공 상태, 사진 ID·순서 일치, 8개 특징 필드의 형식 검증을 뜻하며 인식 정확도 판정은 아니다.

```bash
curl --fail-with-body --max-time 120 -X POST \
  http://127.0.0.1/api/registrations/REGISTRATION_ID/analysis

sudo docker exec smart-lost-found-prod-backend-1 \
  python -m app.inspect_analysis REGISTRATION_ID --require-succeeded
```

배포 디렉터리는 root 전용이므로 위 명령은 디렉터리 이동 없이 실행한다. `REGISTRATION_ID`를 실제 UUID로 바꾼다. 사물함 배정과 수령 결과도 운영자 DB에서 조회할 수 있다. 아래 쿼리는 모든 키오스크의 현재 점유 상태를 읽으며 장치를 동작시키지 않는다.

```bash
sudo docker exec -i smart-lost-found-prod-db-1 \
  sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off' <<'SQL'
SELECT l.kiosk_id, l.locker_number,
       COALESCE(a.status, 'available') AS locker_status,
       f.registration_id, f.status AS item_status,
       a.deposited_at
FROM lockers l
LEFT JOIN storage_assignments a
  ON a.locker_id = l.id AND a.ended_at IS NULL
LEFT JOIN found_items f ON f.id = a.found_item_id
ORDER BY l.kiosk_id, l.locker_number;
SQL
```

수령 완료 후에는 이 현재 배정 조회에서 해당 칸이 `available`이 된다. 종료된 기록 자체는 DB에 남는다. 특정 등록의 이력을 확인하려면 물품·배정을 연결해 조회한다.

```sql
SELECT r.id AS registration_id, r.status AS registration_status,
       r.analysis_status, f.id AS found_item_id, f.status AS item_status,
       l.locker_number, a.status AS assignment_status,
       a.deposited_at, a.ended_at
FROM registrations r
LEFT JOIN found_items f ON f.registration_id = r.id
LEFT JOIN storage_assignments a ON a.found_item_id = f.id
LEFT JOIN lockers l ON l.id = a.locker_id
WHERE r.id = 'REGISTRATION_ID'::uuid
ORDER BY a.created_at;
```

수령 완료는 `item_status=retrieved`, `assignment_status=retrieved`, `ended_at` 존재로 확인한다. 등록 상태 `stored`는 수령 후에도 유지된다.

## 수령 (분실자)

소유 확인을 통과한 물건에 일회성 반환 코드를 발급하고, 키오스크에서 그 코드로 보관함을 연다.
흐름과 "개방 성공 뒤에 코드를 사용 처리한다"는 원칙은 [장치 규약 6.2](device-protocol.md)를 따른다.

| 메서드 | 경로 | 입력·출력 |
| --- | --- | --- |
| POST | `/api/ownership/attempts` | `{"description":"도서관에서 남색 필통을 잃어버렸어요"}` → 질문 또는 검색 결과 |
| GET | `/api/ownership/attempts/{attempt_id}` | 저장된 현재 질문 또는 완료 판정. 만료·이전 질문 제출 후 상태 확인 |
| POST | `/api/ownership/attempts/{attempt_id}/answers` | `{"question_id":"UUID","option_id":"UUID"}` → 다음 질문, `photo_confirmation`, 또는 최종 판정 |
| GET | `/api/ownership/attempts/{attempt_id}/photo` | 확인용 등록 사진 `image/jpeg`. 응답 헤더 `X-Ownership-Photo-Token`을 다음 요청에 쓴다 |
| POST | `/api/ownership/attempts/{attempt_id}/photo-confirmation` | `{"confirmed":true,"photo_token":"UUID"}` → `{"attempt","return_code"}`. **반환 코드를 받는 정상 경로** |
| POST | `/api/found-items/{found_item_id}/return-code` | 보조 경로. `{"attempt_id":"UUID"}` → 201 `{"found_item_id","code","expires_at","kiosk_id","kiosk_name"}` |
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

- 코드 평문은 발급 응답에서 한 번만 받는다. 서버는 SHA-256 해시를 저장한다. 코드를 잃어버리면 새 소유 확인을 거쳐 발급받는다. 재발급은 이전 코드를 `revoked`로 만든다.
- 코드는 8자리 Base32(A~Z, 2~7)다. 입력의 공백·하이픈·소문자는 서버가 정규화하므로 FE가 맞출 필요는 없다.
- 문답을 통과하면 `photo_confirmation` 단계에서 등록 사진을 사용자에게 보여 주고 승인을 받아야 `decision=supported`와 `found_item_id`가 나온다. 사진 확인 응답이 코드까지 함께 주므로 FE는 보조 경로를 따로 호출하지 않는다. 백엔드는 요청의 확인 시도와 물품이 일치하는지, 사진 확인을 거쳤는지, 근거가 유효하고 물건이 보관 중인지 확인한다. 한 확인 시도에서 코드를 한 번 발급한다. [소유 확인 워크플로](../backend/app/ai_ownership_verification/workflow.md)
- 물건 상태는 `stored → retrieved`, 배정 상태는 `stored → awaiting_pickup → retrieved`다. 등록(`registrations`) 상태는 수령으로 바뀌지 않는다.
- 개방 성공 시 코드를 `used`로 처리한다. 완료된 개방을 재요청하면 문을 다시 열지 않고 같은 결과를 반환하므로 FE의 중복 클릭·새로고침은 안전하다.
- 명확한 장치 실패는 코드를 그대로 두어 다시 시도할 수 있다. 통신 단절 등 결과 불명은 문이 이미 열렸을 수 있어 코드를 `locked`로 두고 같은 코드의 재개방을 막는다. 운영 복구 API는 아직 없다.
- 완료는 `confirmed=true`와 센서 `closed`를 함께 확인한다. 완료 시 배정이 종료되어 해당 칸이 즉시 다시 배정 가능해진다.
- 백엔드는 물건을 꺼냈는지 확인하지 않는다. 문 센서는 개폐만 알리므로, 물건을 두고 문만 닫은 뒤 완료를 누르면 그 칸이 빈 칸으로 반납된다. Pi는 가상 무게 센서로 `ITEM_NOT_TAKEN`·`ITEM_NOT_DEPOSITED`를 MQTT로 발행하지만([MQTT 규약](mqtt-protocol.md)) 백엔드에 구독·조회 경로가 없어 이 업무 API의 판정에는 반영되지 않는다. `confirmed=true`가 사용자 선언이며 FE 완료 화면이 이를 분명히 물어야 한다. 투입 완료도 같은 한계를 갖지만, 잘못된 수령 완료는 물건이 남은 칸을 재배정한다는 점에서 영향이 더 크다. 보관함 내부 감지 수단은 하드웨어 협의 대상이다.
- 코드의 대상 키오스크가 요청의 `kiosk_id`와 다르면 거절한다. 이는 대상 검사이고 호출자 인증이 아니다. 키오스크 세션은 여전히 미구현이다.

## 장치 상태와 경고 (MQTT 수신분)

Pi가 [MQTT 규약](mqtt-protocol.md)으로 보낸 센서 상태와 이상 상황을 백엔드가 구독해 저장하고, 아래 두 경로로 읽혀 준다. 이 값은 화면 표시용이고 보관·수령 완료 판정에는 쓰지 않는다(D03·D06).

| 메서드 | 경로 | 입력·출력 |
| --- | --- | --- |
| GET | `/api/kiosks/{kiosk_id}/device-status` | 그 키오스크의 최신 장치·칸 상태 배열. 없는 키오스크는 404 |
| GET | `/api/alerts` | `kiosk_id`·`severity`(`alarm`/`warning`)·`since`·`limit`(1~200, 기본 50)·`offset` 쿼리. 최근 경고부터 |

```json
[
  {"device": "locker", "locker_number": null, "connection": "online", "door": null,
   "weight_g": null, "item": null, "reported_at": "2026-11-19T19:24:26+09:00",
   "updated_at": "2026-11-19T10:24:26Z"},
  {"device": "locker", "locker_number": 1, "connection": null, "door": "closed",
   "weight_g": 150.0, "item": "present", "reported_at": "2026-11-19T19:24:27+09:00",
   "updated_at": "2026-11-19T10:24:27Z"}
]
```

- `locker_number`가 `null`인 행은 칸이 아니라 장치 자체이고, `connection`이 그 컨트롤러의 online/offline이다. Pi가 갑자기 끊기면 브로커가 유언 메시지로 `offline`을 보낸다.
- 칸 행에는 `door`·`weight_g`·`item`이 있고 `connection`은 `null`이다. 촬영 상자는 `device=capture`, `locker_number=null` 한 행에 연결 상태와 무게가 함께 담긴다.
- 아직 아무 메시지도 받지 못한 키오스크는 빈 배열이다. 장치가 꺼져 있는 것과 구분되지 않으므로 "상태 없음"으로 표시한다.

```json
[
  {"id": "...", "kiosk_id": "...", "code": "ITEM_NOT_TAKEN", "severity": "warning",
   "device": "locker", "locker_number": 2, "message": "2번 보관함에 물건이 남아 있습니다.",
   "detail": {"before_g": 150.0, "after_g": 150.0}, "reported_at": "2026-11-19T19:24:29+09:00"}
]
```

- `code`는 규약 4절의 6종이지만 그 밖의 값도 올 수 있다. 모르는 `code`는 `message`를 그대로 보여 준다.
- 보관 기간(기본 14일)이 지난 경고는 지워진다. 경고를 해소 처리하는 API는 없다.
- `MQTT_HOST`를 설정하지 않은 서버에서는 두 경로가 정상 응답하지만 내용이 비어 있다.

## 오류

업무/연동 오류: `{"detail":{"code":"ERROR_CODE"}}`. 입력 검증 422는 FastAPI 표준 오류 배열이다. 외부 오류 원문·키·주소를 응답에 복사하지 않는다.

| HTTP | 주요 code |
| --- | --- |
| 403 | `OWNERSHIP_REQUIRED` |
| 404 | `KIOSK_NOT_FOUND`, `CATEGORY_NOT_FOUND`, `REGISTRATION_NOT_FOUND`, `IMAGE_NOT_FOUND`, `ITEM_NOT_FOUND`, `RETURN_CODE_NOT_FOUND`, `OWNERSHIP_PHOTO_UNAVAILABLE` |
| 409 | `OWNERSHIP_PHOTO_NOT_PENDING`, `OWNERSHIP_PHOTO_LOAD_REQUIRED`, `INVALID_REGISTRATION_STATE`, `SHOT_OUT_OF_ORDER`, `TWO_IMAGES_REQUIRED`, `ANALYSIS_REQUIRED`, `NO_AVAILABLE_LOCKER`, `ALLOCATION_CONFLICT`, `DEPOSIT_NOT_OPENED`, `DOOR_NOT_CLOSED`, `ITEM_NOT_STORED`, `PICKUP_NOT_OPENED`, `RETURN_CODE_EXPIRED`, `RETURN_CODE_ALREADY_USED`, `RETURN_CODE_UNUSABLE`, `RETURN_CODE_IN_PROGRESS`, `RETURN_CODE_WRONG_KIOSK`, `RETURN_CODE_CONFLICT` |
| 502 | `DEVICE_UNREACHABLE`, `INVALID_DEVICE_RESPONSE`, `DEVICE_ROLE_MISMATCH`, `DEVICE_RESPONSE_TOO_LARGE`, `INVALID_IMAGE`, `IMAGE_STORAGE_FAILED`, `INVALID_ANALYSIS_RESULT`, 규약의 Pi 오류 code |
| 503 | `BUSINESS_API_DISABLED`, `AI_NOT_CONFIGURED`, `DEVICE_NOT_CONFIGURED`, `CAPTURE_NOT_CONFIGURED`, `IMAGE_STORAGE_NOT_CONFIGURED`, `RETURN_CODE_NOT_CONFIGURED` |

소유 확인의 만료 410, 현재 질문 충돌 409, 검토 근거 변경, AI 오류 등 전체 오류와 FE 처리 규칙은 [기능 명세의 오류 계약](../backend/docs/functional-spec.md#9-f13-오류지연중복-요청-처리)에 정리한다. 사진 분석에서도 공통 호출기의 `OWNERSHIP_AI_*` 오류가 올 수 있다.

자연어 검색·질문·답변은 `/api/ownership/attempts` 흐름으로 제공한다. 반환 코드는 성공한 동일 물품의 소유 확인이 있어야 발급한다. 별도 물품 검색 목록 API나 `search_stored_items` 공개 API는 없다. 후보 검색은 전체 대상 보관 물품 500건 제한이며, 질문용 검토 근거 등록과 키오스크 기기 인증은 별도 후속 범위다.
