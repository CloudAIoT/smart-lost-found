# 프론트 연동을 위한 백엔드 기능 명세

기준일: 2026-11-21. `main`의 FastAPI 라우터·요청/응답 모델·서비스·테스트를 기준으로 작성한다. 소유 확인은 사진 확인 단계와 정책 `development_v4`가 반영된 현재 구현이다. 이 문서는 **현재 구현된 연동 계약**이며, 명세에 적힌 개선 항목을 구현·배포 완료로 간주하지 않는다. 과거 F01~F13의 기능 식별자는 유지하고, 이전의 “검색·소유 확인·반환 미구현” 설명을 현재 동작으로 대체한다.

- 주 독자: 웹 포털·현장 키오스크 FE 담당자.
- API 인덱스·운영자 DB 확인: [팀 API 문서](../../docs/api.md).
- 키오스크 최초 설정: [FE-K01](../../docs/web-structure.md#fe-k01-키오스크-최초-1회-설정).
- 소유 확인 내부 정책: [소유 확인 워크플로](../app/ai_ownership_verification/workflow.md).
- 원문 요구·결정 이력: [검토 기록](spec-review.md). 이력 문서의 당시 “미정”과 이 문서의 현재 구현을 구분한다.

## 1. 접속과 공통 계약

| 항목 | 계약 |
| --- | --- |
| EC2 테스트 주소 | `http://52.78.12.168` |
| Swagger / OpenAPI | `/docs` / `/openapi.json` |
| FE 호출 경로 | 같은 출처의 `/api/...`. `/health`·`/ready`에는 `/api`를 붙이지 않는다 |
| 로컬 FE | Vite의 `/api` 프록시 사용. 다른 출처에서 직접 호출할 CORS 설정은 현재 없음 |
| 요청 본문 | 본문이 있는 요청만 `Content-Type: application/json`. “본문 없음” API는 파일·빈 입력 객체를 요구하지 않음 |
| 식별자 | UUID 문자열. 요청마다 새로 만들지 않고 API가 반환한 ID를 다음 단계에서 사용 |
| 시간 | ISO 8601, 시간대 포함. FE에서 사용자 시간대로 표시 |
| 성공 형식 | 공통 `success`/`data` 감싸기 없음. 응답 객체 또는 배열을 그대로 사용 |
| 입력 검증 | 아래 요청 DTO에 없는 JSON 필드는 거절. UUID·길이·선택지 등의 검증 실패는 422 |
| 업무 API 활성화 | `LOCAL_DEVELOPMENT=true` 또는 `BUSINESS_API_ENABLED=true`. 둘 다 false면 업무 API는 503 `BUSINESS_API_DISABLED` |
| 인증 현황 | 사용자 로그인·키오스크 기기 인증 없음. 소유 확인·반환 코드·대상 키오스크 검사는 구현되어 있으나 호출자의 기기 신원 검증과는 별개 |
| FE 비밀값 | Gemini·AWS·Pi API 키를 FE에 넣지 않는다. FE는 백엔드만 호출 |
| 실물 확인 | 시제품은 개방을 해당 칸 LED 점멸로, 닫힘을 해당 칸 버튼으로 표현. FE는 서버 성공 응답과 사용자 완료 확인을 기준으로 진행 |

예시의 UUID와 반환 코드는 설명용이다. 등록·사진·확인 시도·질문·물품 ID를 서로 바꿔 쓰지 않는다.

| 이름 | 용도·획득 위치 |
| --- | --- |
| `kiosk_id` | 단말 대상. 키오스크 목록과 FE-K01에서 선택 |
| `registration_id` | 습득물 등록·사진·분석·투입 흐름. 등록 응답의 `id` |
| 사진 `id` | 등록 응답 `images[]`의 UUID. 사진 순서는 별도 `shot_order` |
| `attempt_id` | 분실자 검색·질문 흐름. `AttemptView`에서 획득 |
| `question_id` / `option_id` | 현재 질문·선택지 UUID. 답변을 보낼 때 사용 |
| `found_item_id` | 소유 확인에 성공한 물품·반환 흐름. 등록 ID와 다름 |
| `locker_number` | 키오스크 안의 정수 칸 번호. 서버 배정 결과이며 사물함 DB UUID가 아님 |

## 2. 화면별 전체 흐름과 API 목록

### 2.1 습득자: 현장 등록·보관

1. 키오스크 최초 설정 또는 저장값 복원 → 키오스크·카테고리 조회.
2. 습득 위치 등 입력 → 등록 생성.
3. 같은 물건의 첫 번째 사진 촬영 → 다른 면으로 돌린 뒤 두 번째 촬영 → 미리보기.
4. 이미지 분석 요청 → `ready/succeeded` 확인.
5. 투입 개방 요청 → 서버가 빈 칸 배정 → 응답의 칸 번호 표시.
6. 물건 투입 → 해당 칸 버튼으로 닫힘 알림 → 사용자 보관 완료 요청.
7. `stored` 응답 확인 → 완료 화면 → 다음 사용자에게 업무 입력 초기화.

### 2.2 분실자: 웹 검색·소유 확인·현장 수령

1. 웹에서 자연어 설명 제출 → 질문 또는 검색 결과 상태 표시.
2. 서버가 준 질문·선택지를 표시 → 선택지 ID 제출 → 다음 질문 반복.
3. 문답을 통과하면 `photo_confirmation` → 등록 사진을 받아 보여 주고 본인 물건인지 사용자에게 확인받는다.
4. 확인 결과 제출 → 같은 응답의 `return_code`에서 코드·만료 시각·키오스크를 표시. `completed/supported`가 함께 온다.
5. 해당 현장 키오스크에서 코드 입력 → 수령 개방 요청 → 응답의 칸 번호 표시.
6. 물건 수령 → 해당 칸 버튼으로 닫힘 알림 → 사용자 수령 완료 요청.
7. `item_status=retrieved` 및 `assignment_status=retrieved` 확인. 칸은 다시 배정 가능.

### 2.3 공개 엔드포인트 전체

이 표의 21개가 업무 FE와 상태 확인에 제공되는 전체 라우트다. Swagger 자체·OpenAPI 문서 경로는 제외한다.

| 메서드 | 경로 | 기능 | 성공 |
| --- | --- | --- | --- |
| GET | `/health` | F01 서버 상태 | 200 |
| GET | `/ready` | F02 DB 준비 상태 | 200 |
| GET | `/api/kiosks` | F03 키오스크 목록 | 200 |
| GET | `/api/categories` | F04 등록용 카테고리 목록 | 200 |
| GET | `/api/kiosks/{kiosk_id}/device-status` | F14 장치·칸 최신 상태 | 200 |
| GET | `/api/alerts` | F14 장치 경고 목록 | 200 |
| POST | `/api/registrations` | F05 등록 생성 | 201 |
| GET | `/api/registrations/{registration_id}` | F05 등록 조회 | 200 |
| POST | `/api/registrations/{registration_id}/shots/{shot_order}` | F06 촬영·저장 | 200 |
| GET | `/api/registrations/{registration_id}/shots/{shot_order}/preview` | F06 미리보기 URL | 200 |
| POST | `/api/registrations/{registration_id}/analysis` | F08 두 사진 분석 | 200 |
| POST | `/api/registrations/{registration_id}/deposit/open` | F07 투입용 개방 | 200 |
| POST | `/api/registrations/{registration_id}/deposit/complete` | F12 보관 완료 | 200 |
| POST | `/api/ownership/attempts` | F09 자연어 검색·소유 확인 시작 | 200 |
| GET | `/api/ownership/attempts/{attempt_id}` | F09 현재 질문·판정 조회 | 200 |
| POST | `/api/ownership/attempts/{attempt_id}/answers` | F09 답변 제출 | 200 |
| GET | `/api/ownership/attempts/{attempt_id}/photo` | F09 확인용 등록 사진 | 200 (`image/jpeg`) |
| POST | `/api/ownership/attempts/{attempt_id}/photo-confirmation` | F09 사진 확인 + F10 코드 발급 | 200 |
| POST | `/api/found-items/{found_item_id}/return-code` | F10 반환 코드 발급 (보조 경로) | 201 |
| POST | `/api/pickup/open` | F11 수령용 개방 | 200 |
| POST | `/api/pickup/{found_item_id}/complete` | F12 수령 완료 | 200 |

## 3. F01~F04 상태 확인·기초 데이터·키오스크 설정

### F01 서버 상태 / F02 DB 준비 상태

`GET /health`는 본문 없이 호출하고 `{"status":"ok"}`를 반환한다. DB·S3·Gemini·Pi 정상 여부를 보장하지 않는다.

`GET /ready`는 DB에 간단한 조회를 실행하고 `{"status":"ready"}`를 반환한다. DB 오류일 때는 503 `{"detail":"Database unavailable"}`다. 이 응답만으로 촬영·개방 장치가 정상이라고 판단하지 않는다.

### F03 키오스크 목록

`GET /api/kiosks?limit=100&offset=0`

| 쿼리 | 타입 | 기본값 | 제약 |
| --- | --- | --- | --- |
| `limit` | integer | 20 | 1~100 |
| `offset` | integer | 0 | 0 이상 |

응답은 이름·ID 순으로 정렬한 배열이며 전체 건수 필드는 없다.

~~~json
[
  {
    "id": "00000000-0000-0000-0000-000000000001",
    "name": "도서관 키오스크",
    "location_description": "도서관 1층 안내데스크 옆",
    "latitude": null,
    "longitude": null
  }
]
~~~

이름은 문자열, 장소 설명·좌표는 null 가능하다. 좌표가 없으면 목록은 표시하되 지도 마커·거리 계산은 생략한다. 빈 칸 수·장치 온라인 상태는 이 응답에 없다. 응답 건수가 `limit`와 같으면 `offset`을 늘려 다음 페이지를 읽는다.

현장 화면은 [FE-K01](../../docs/web-structure.md#fe-k01-키오스크-최초-1회-설정)의 `smart-lost-found.kiosk_id.v1` 설정을 사용한다. 최초 명시적 선택, 목록으로 복원 검증, 조회 실패 시 저장값 유지, 업무 중 대상 고정, 변경·초기화 규칙을 따른다. 현재 FE의 첫 항목 자동 선택은 이 명세대로 바꿔야 하는 구현 과제다.

### F04 등록용 카테고리와 분실자 검색 입력

`GET /api/categories` → 아래 배열. 페이지네이션 없이 `code` 순으로 정렬한다.

~~~json
[
  {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "code": "pencil_case",
    "name": "필통"
  }
]
~~~

고정 기본 목록은 없다. 빈 배열도 정상이다. 등록 카테고리는 선택값이므로 목록이 없으면 선택을 생략하고 `primary_category_id`를 생략하거나 null로 보낸다. 프론트가 만든 임의 ID나 빈 문자열을 보내지 않는다.

분실자가 찾는 물건은 별도 카테고리 선택 API 대신 F09의 자연어 `description`으로 검색한다. 독립적인 `GET /found-items` 또는 `/search` 공개 API는 없다.

## 4. 공통 등록 응답 RegistrationView

등록 생성·조회·촬영·분석·투입 개방·보관 완료는 모두 같은 형태를 반환한다.

~~~json
{
  "id": "11111111-1111-4111-8111-111111111111",
  "kiosk_id": "00000000-0000-0000-0000-000000000001",
  "primary_category_id": null,
  "secondary_description": null,
  "found_location": "도서관 1층",
  "status": "ready",
  "analysis_status": "succeeded",
  "images": [
    {"id": "22222222-2222-4222-8222-222222222222", "shot_order": 1},
    {"id": "33333333-3333-4333-8333-333333333333", "shot_order": 2}
  ],
  "locker_number": null
}
~~~

| 필드 | 타입 | FE 의미 |
| --- | --- | --- |
| `id` / `kiosk_id` | UUID string | 현재 등록과 소속 키오스크 |
| `primary_category_id` | UUID string 또는 null | 사용자가 선택한 DB 카테고리. AI의 종류 문자열과 다름 |
| `secondary_description` | string 또는 null | 사용자가 입력한 추가 특징 |
| `found_location` | string 또는 null | 습득 위치 |
| `status` | string | 아래 등록 상태표 참조 |
| `analysis_status` | string | `pending` / `running` / `succeeded` / `failed` |
| `images` | array | 촬영 순서대로 0~2개의 `{id, shot_order}`. S3 URL·파일은 포함하지 않음 |
| `locker_number` | integer 또는 null | 투입 개방 성공 후 종료되지 않은 현재 배정이 있을 때만 번호 |

등록 상태는 다음 의미를 갖는다.

| `status` | 의미·화면 |
| --- | --- |
| `draft` | 등록 생성/촬영 완료 후 대기. `images` 개수로 다음 촬영을 판단. 분석 실패 시에도 이 상태 |
| `capturing` | 촬영 처리 중. 중복 촬영 금지 |
| `analyzing` | 분석 처리 중. `analysis_status=running` |
| `ready` | 분석 성공, 투입 개방 가능 |
| `opening` | 사물함 배정·개방 처리 중 |
| `awaiting_deposit` | 개방 성공, 사용자 투입 완료 대기 |
| `stored` | 등록의 보관 절차 완료. 물품이 나중에 수령되어도 이 값은 유지 |
| `blocked` | 촬영·투입 개방 실패 또는 결과 불명. 일반 재시도·복구 API 없음 |

`stored`만 보고 “현재 물건이 사물함 안에 있음”으로 표시하지 않는다. 수령 완료 후에도 등록 조회는 200이며 `status=stored`, `locker_number=null`로 응답하고 사진·분석 상태는 유지된다. 같은 칸이 다른 물품에 재배정되어도 이전 등록의 번호는 null이다. 실제 수령 완료는 `PickupView`의 `retrieved`로 판단한다.

## 5. F05~F08 등록·사진·분석·투입 개방

### F05 등록 생성과 조회

`POST /api/registrations`

~~~json
{
  "kiosk_id": "00000000-0000-0000-0000-000000000001",
  "primary_category_id": null,
  "secondary_description": "검정 지퍼가 있는 필통",
  "found_location": "도서관 1층"
}
~~~

| 요청 필드 | 필수 | 제약 |
| --- | --- | --- |
| `kiosk_id` | 예 | DB에 존재하는 UUID, 확정된 단말 설정 |
| `primary_category_id` | 아니오 | 존재하는 카테고리 UUID 또는 null |
| `secondary_description` | 아니오 | 최대 2,000자 또는 null |
| `found_location` | 아니오 | 최대 200자 또는 null. 서버가 앞뒤 공백 제거, 빈 값은 null |

성공: 201 `RegistrationView`, `draft/pending`, `images=[]`, `locker_number=null`. 오류: 404 `KIOSK_NOT_FOUND`/`CATEGORY_NOT_FOUND`, 입력 422.

FE는 응답 `id`를 업무 상태에 저장한다. 생성 POST는 반복할 때마다 새 등록을 만든다. 응답을 잃어 ID를 모르는 경우 자동 반복 생성하지 않는다. 등록 수정·삭제·취소·목록 조회 API는 없다.

`GET /api/registrations/{registration_id}`는 본문 없이 200 `RegistrationView`를 반환한다. 없는 ID는 404 `REGISTRATION_NOT_FOUND`. 복구 가능한 진행 상태 조회에 사용하며, 조회 자체는 AI·장치 명령을 실행하지 않는다.

### F06 두 번의 촬영과 미리보기

`POST /api/registrations/{registration_id}/shots/{shot_order}`, 본문 없음. `shot_order`는 정수 1 또는 2이며 순서대로 호출한다.

1. 물건 한 개를 촬영 공간에 놓고 1번 요청을 보낸다.
2. 200 응답에서 `images`에 1번 사진이 생긴 것을 확인한다.
3. 같은 물건을 돌려 다른 면·구조·표시를 보이게 한 뒤 2번을 요청한다.
4. 200 응답에 1·2번 사진이 모두 있으면 분석 단계로 이동한다.

PC 웹캠이나 업로드 파일을 보내는 API가 아니다. 백엔드가 등록의 키오스크에 연결된 촬영 Pi를 호출하고 JPEG를 S3에 저장한다. FE는 사용자 안내와 실행 버튼을 담당한다.

두 촬영 성공 후에도 `status=draft`, `analysis_status=pending`이다. 이미 성공한 같은 사진 번호를 재요청하면 기존 결과를 반환하며 재촬영하지 않는다. 아직 없는 사진은 `draft`에서만 촬영 가능하다.

주요 오류: 409 `SHOT_OUT_OF_ORDER`/`INVALID_REGISTRATION_STATE`, 경로 입력 422. 촬영·S3 연동 실패는 502/503이고 등록이 `blocked`가 될 수 있다. 새 사진으로 바꾸는 재촬영·삭제 API는 없다.

`GET /api/registrations/{registration_id}/shots/{shot_order}/preview`

~~~json
{
  "url": "https://example.invalid/temporary-preview"
}
~~~

응답 URL을 이미지 `src`에 사용한다. URL은 서버 설정에 따른 유효기간이 있으므로 만료 시 같은 GET으로 다시 받는다. DB나 localStorage에 영구 저장할 URL이 아니다. 사진이 없으면 404 `IMAGE_NOT_FOUND`. 사진 조회 실패와 분석 실패를 혼동하지 않는다.

### F08 두 사진 분석

`POST /api/registrations/{registration_id}/analysis`, 본문 없음.

백엔드가 DB의 사진 1·2를 S3에서 읽어 방향 보정·RGB 변환·긴 변 최대 1280px 축소 후 Gemini에 함께 보낸다. 결과 형식을 검사하고 입력 사진 UUID 순서와 특징을 DB에 저장한다.

| 결과 | 응답·다음 행동 |
| --- | --- |
| 성공 | 200 `RegistrationView`, `ready/succeeded` → 투입 개방 가능 |
| 사진 부족 | 409 `TWO_IMAGES_REQUIRED` → 누락된 사진부터 처리 |
| 분석 연동 실패 | 보통 502/503, `draft/failed`로 복귀 → 오류 원인 확인 후 분석 재요청 가능 |
| 같은 성공 요청 반복 | `ready/succeeded`인 경우 저장 결과 반환, Gemini 재호출 없음 |
| 보관 시작 후 분석 요청 | 409 `INVALID_REGISTRATION_STATE`. 재분석 API 아님 |

사진 분석의 서버 모델 호출 제한은 기본 90초이며 배포 설정으로 바뀔 수 있다. Nginx API 응답 대기는 120초다. FE는 짧은 공통 타임아웃으로 중간에 실패 처리하지 않고 진행 표시·중복 클릭 방지를 제공한다. 브라우저 요청 취소나 타임아웃은 서버 처리 취소를 뜻하지 않는다.

분석 특징 원문은 `RegistrationView`에 없다. 화면이 별도 특징 응답을 기다리거나 키 이름을 가정해 읽지 않는다. DB 내부 형태는 10절을 참고한다.

### F07 빈 사물함 배정·투입 개방

`POST /api/registrations/{registration_id}/deposit/open`, 본문 없음.

분석 성공 및 두 입력 사진의 일치가 선행 조건이다. 백엔드는 같은 키오스크의 종료되지 않은 배정을 제외하고 작은 번호부터 빈 칸을 배정한다. 예를 들어 1번이 사용 중이고 2번이 등록되어 비어 있으면 2번이 선택된다. FE는 `locker_number`를 요청에 보내지 않는다.

성공: 200 `RegistrationView`, `status=awaiting_deposit`, `locker_number`에 선택된 번호. 장치 성공 응답 전에는 “개방 완료”로 표시하지 않는다. 시제품에서는 해당 칸 LED가 깜빡인다.

주요 오류: 409 `ANALYSIS_REQUIRED`/`INVALID_REGISTRATION_STATE`/`NO_AVAILABLE_LOCKER`/`ALLOCATION_CONFLICT`. 개방 연동 실패는 `blocked`가 되고 배정은 유지될 수 있다. FE가 실패를 빈 칸 반환으로 간주해서는 안 된다.

`awaiting_deposit` 또는 `stored`의 반복 요청은 새 개방을 실행하지 않는다. 반납 완료 후의 과거 등록으로 사물함을 다시 열 수 없다.

## 6. F09 자연어 검색·질문·소유 확인

### 6.1 검색·확인 시작

`POST /api/ownership/attempts`

~~~json
{
  "description": "도서관 근처에서 빨간색 필통을 잃어버렸어요"
}
~~~

`description`은 앞뒤 공백 제거 후 1~1,000자다. 카테고리 ID·키오스크 ID·후보 ID는 입력 필드가 아니다.

서버는 단서를 추출하고 현재 보관 중인 물품을 비교한다. 전체 키오스크의 대상 보관 물품이 500건을 넘으면 `search_limit`이다. “검색 결과 상위 500건”이 아니며, 설명을 더 자세히 입력해도 전체 보관 건수 제한 자체가 해소되지는 않는다. 100건씩 비교하는 내부 배치를 FE 페이지네이션으로 해석하지 않는다.

성공 HTTP 상태는 검색 결과 유무와 관계없이 200이다. `AttemptView.status`를 보고 화면을 나눈다.

### 6.2 AttemptView 전체 형태

~~~json
{
  "status": "question",
  "attempt_id": "55555555-5555-4555-8555-555555555555",
  "initial_candidate_count": 1,
  "question": {
    "id": "66666666-6666-4666-8666-666666666666",
    "text": "물건에 적힌 문구 중 기억나는 것을 골라 주세요.",
    "options": [
      {"id": "77777777-7777-4777-8777-777777777771", "label": "RED"},
      {"id": "77777777-7777-4777-8777-777777777772", "label": "BLUE"},
      {"id": "77777777-7777-4777-8777-777777777773", "label": "GREEN"},
      {"id": "77777777-7777-4777-8777-777777777774", "label": "BLACK"},
      {"id": "77777777-7777-4777-8777-777777777775", "label": "WHITE"}
    ]
  },
  "decision": null,
  "found_item_id": null
}
~~~

이 선택지는 합성 예시이며 정답을 지정한 예시가 아니다. 서버는 질문 하나와 순서가 섞인 선택지를 최대 5개 준다. 후보의 실제 값이 5개보다 적으면 AI가 만든 오답으로 채우므로, FE는 개수를 상수로 가정하지 않고 받은 배열 그대로 표시한다.

| 필드 | 타입·의미 |
| --- | --- |
| `status` | 아래 상태표 |
| `attempt_id` | UUID 또는 null. 확인 시도가 생성되면 존재 |
| `initial_candidate_count` | integer 또는 null. 최초 후보 수이며 현재 남은 후보 수가 아님 |
| `question` | 현재 질문 객체 또는 null |
| `question.id` / `text` | UUID / 표시할 질문 문자열 |
| `question.options` | `{id: UUID, label: string}` 배열. ID로 제출하고 label로 표시 |
| `decision` | null 또는 `supported` / `not_confirmed`. `insufficient_evidence`는 더 이상 발급되지 않고 과거 기록도 `not_confirmed`로 읽힌다 |
| `found_item_id` | 소유 확인 성공 시에만 물품 UUID, 나머지는 null |

| `status` | FE 처리 |
| --- | --- |
| `needs_clues` | 검색 가능한 단서 부족. 설명에 종류·색상·위치 등 기억나는 정보 추가 |
| `no_candidates` | 현재 설명에 맞는 후보 없음. 문구 수정 또는 종료 |
| `search_limit` | 서버 전체 대상 보관 물품이 500건 초과. 서비스 제한 안내, 무한 재검색 금지 |
| `question` | 질문과 선택지 표시. 반드시 한 항목 선택 후 답변 제출 |
| `photo_confirmation` | 문답 통과. 6.5의 사진 확인 단계로 이동. 아직 성공 판정이 아님 |
| `completed` | `decision`에 따른 최종 화면 |
| `refine_search` | 응답 모델에 남아 있는 예약 상태. 현재 검색 구현은 생성하지 않음. 수신 시 설명 보완 화면으로 처리 |

앞의 세 검색 종료 상태는 확인 시도를 만들지 않으므로 다른 필드는 null이다. 예:

~~~json
{
  "status": "no_candidates",
  "attempt_id": null,
  "initial_candidate_count": null,
  "question": null,
  "decision": null,
  "found_item_id": null
}
~~~

시작 요청이 반드시 질문으로 끝난다고 가정하지 않는다. 쓸 수 있는 질문 필드가 없으면 시작 응답부터 `photo_confirmation` 또는 `completed/not_confirmed`가 나올 수 있다.

### 6.3 현재 상태 조회와 답변 제출

`GET /api/ownership/attempts/{attempt_id}`는 본문 없이 200 `AttemptView`를 반환한다. AI를 다시 실행하지 않고 저장된 현재 질문/판정을 읽는다. 404 `OWNERSHIP_ATTEMPT_NOT_FOUND`, 410 `OWNERSHIP_ATTEMPT_EXPIRED`, 409 `OWNERSHIP_ATTEMPT_RESTART_REQUIRED`를 처리한다.

확인 시도의 유효기간은 생성 후 20분이며 답변마다 연장되지 않는다. 응답에 `expires_at`은 없으므로 서버의 410을 기준으로 다시 시작한다.

`POST /api/ownership/attempts/{attempt_id}/answers`

~~~json
{
  "question_id": "66666666-6666-4666-8666-666666666666",
  "option_id": "77777777-7777-4777-8777-777777777771"
}
~~~

성공: 200 `AttemptView`의 다음 질문 또는 최종 판정. 이전 질문의 선택 상태를 지우고 새 질문 ID 기준으로 화면을 갱신한다. FE가 질문 필드·정답·점수를 추론하거나 임의 “모르겠어요” 선택지/건너뛰기를 추가하지 않는다. 현재 공개 입력은 자유 서술 답변이 아닌 두 UUID다.

- 이전 질문·동시 제출 충돌: 409 `OWNERSHIP_QUESTION_NOT_CURRENT` → GET으로 현재 상태 재조회. 이전 답을 다음 질문에 자동 적용하지 않는다.
- 현재 질문에 없는 선택지: 422 `OWNERSHIP_OPTION_NOT_FOUND` → 현재 질문 확인.
- 다음 질문 생성 중 AI 오류: 실패한 답변 처리는 커밋되지 않는다. GET으로 현재 질문 확인 후 같은 질문이 남아 있으면 사용자가 재시도할 수 있다.
- 통신 응답 유실: 먼저 GET으로 답변 반영 여부 확인. 반복 POST를 자동 발사하지 않는다.

### 6.4 최종 판정과 반환 승인

| `decision` | 의미·다음 화면 |
| --- | --- |
| `supported` | 남은 후보 1개의 검증을 통과하고 사용자가 사진까지 확인. `found_item_id`로 코드 발급 가능 |
| `not_confirmed` | 후보 전멸·오답 한도 초과·검증 미완·후보 다수·사진 없음, 또는 사용자가 사진을 자기 물건이 아니라고 답한 경우. 실패 안내, 코드 발급 금지 |

성공 예:

~~~json
{
  "status": "completed",
  "attempt_id": "55555555-5555-4555-8555-555555555555",
  "initial_candidate_count": 1,
  "question": null,
  "decision": "supported",
  "found_item_id": "44444444-4444-4444-8444-444444444444"
}
~~~

`completed` 자체가 성공은 아니다. `decision=supported`와 `found_item_id`를 함께 확인한다. 질문 횟수는 특징과 후보에 따라 바뀌므로 “3문항을 맞히면 성공” 같은 FE 판정 규칙을 만들지 않는다.

현재 정책은 `development_v4`다. 판정이 아직 없는 확인 시도의 정책 버전이 서버와 다르면 409 `OWNERSHIP_ATTEMPT_RESTART_REQUIRED`로 거절되므로, 배포 시점에 열려 있던 시도는 새로 시작해야 한다. 비공개 근거는 서버에서 검토된 `ownership_evidence`가 필요하며, 새 사진의 특징 추출만으로 자동 생성되지 않는다. 검토 데이터가 없는 새 물품이 분석·보관까지 성공해도 소유 확인은 보류될 수 있다. 이 검토 정보를 입력하는 공개 운영 API는 없다. FE가 자체적으로 검토 완료 표시를 만들거나 비공개 특징을 공개 화면에 보여주지 않는다.

`GET`의 과거 성공 판정이 반환 코드 발급을 영구 보장하지 않는다. 서버는 발급 시점에 현재 물품 가용성·근거 변경·시도 만료를 다시 확인한다.

### 6.5 사진 확인과 코드 수신

문답을 통과하면 서버는 남은 후보 1개의 등록 사진 하나를 확인 시도에 고정하고 `status=photo_confirmation`을 준다. 이 단계의 사용자 승인 없이는 `supported` 판정도 반환 코드도 나오지 않는다.

`GET /api/ownership/attempts/{attempt_id}/photo`

응답은 JSON이 아니라 `image/jpeg` 본문이며 헤더에 다음이 붙는다.

| 헤더 | 용도 |
| --- | --- |
| `X-Ownership-Photo-Token` | 다음 요청의 `photo_token` 값. 이 GET으로만 얻는다 |
| `Cache-Control: no-store` | 사진을 캐시·영구 저장하지 않는다 |
| `X-Content-Type-Options: nosniff` | 응답 타입 고정 |

FE는 본문을 `Blob`으로 읽어 화면에 띄우고 토큰은 메모리에만 보관한다. 다른 사람의 분실물을 특정할 수 있는 사진이므로 다운로드·공유·localStorage 저장 경로를 만들지 않는다.

`POST /api/ownership/attempts/{attempt_id}/photo-confirmation`

~~~json
{
  "confirmed": true,
  "photo_token": "88888888-8888-4888-8888-888888888888"
}
~~~

`confirmed`는 엄격한 boolean이라 `"true"` 같은 문자열은 422다. 승인(`true`)에는 `photo_token`이 필수이며 고정된 토큰과 다르면 409 `OWNERSHIP_PHOTO_LOAD_REQUIRED`다. 사진을 실제로 받아 본 세션만 승인할 수 있다는 뜻이다. 거부(`false`)는 토큰 없이 보낼 수 있다.

성공: 200. 본문은 `{"attempt": AttemptView, "return_code": ReturnCodeView 또는 null}`이고 `Cache-Control: no-store`가 붙는다.

~~~json
{
  "attempt": {
    "status": "completed",
    "attempt_id": "55555555-5555-4555-8555-555555555555",
    "initial_candidate_count": 1,
    "question": null,
    "decision": "supported",
    "found_item_id": "44444444-4444-4444-8444-444444444444"
  },
  "return_code": {
    "found_item_id": "44444444-4444-4444-8444-444444444444",
    "code": "ABCD2345",
    "expires_at": "2026-11-21T08:30:00Z",
    "kiosk_id": "00000000-0000-0000-0000-000000000001",
    "kiosk_name": "도서관 키오스크"
  }
}
~~~

**이 응답이 반환 코드를 받는 정상 경로다.** 승인이 성공하면 판정 저장과 코드 발급이 한 트랜잭션에서 함께 끝나므로 FE는 7절의 발급 API를 따로 호출하지 않는다. 거부하면 `attempt.decision=not_confirmed`, `return_code=null`이다.

- 사진 확인 단계가 아닌 시도(문답 진행 중, 이미 판정됨, 후보가 1개가 아님): 409 `OWNERSHIP_PHOTO_NOT_PENDING` → GET으로 현재 상태 재조회.
- 이미 코드가 발급된 시도: 409 `OWNERSHIP_RETURN_CODE_ALREADY_ISSUED`. 코드 평문은 다시 받을 수 없다.
- 고정된 사진을 읽을 수 없음: 404 `OWNERSHIP_PHOTO_UNAVAILABLE`.
- 응답 유실: 코드가 이미 발급되었을 수 있으므로 자동 재전송하지 않는다. GET으로 판정을 확인하고 코드를 받지 못했으면 새 소유 확인부터 다시 한다.

## 7. F10 반환 코드 발급 (보조 경로)

`POST /api/found-items/{found_item_id}/return-code`

6.5의 사진 확인 응답이 코드를 함께 주므로 FE의 정상 흐름에는 이 호출이 필요하지 않다. 같은 발급 로직을 직접 부르는 보조 경로이며, 사진 확인을 거치지 않은 시도는 409 `OWNERSHIP_ATTEMPT_RESTART_REQUIRED`, 이미 발급된 시도는 409 `OWNERSHIP_RETURN_CODE_ALREADY_ISSUED`가 된다. 사진 확인을 건너뛰고 코드를 받는 수단이 아니다.

경로에는 성공한 소유 확인 응답의 `found_item_id`, 본문에는 같은 응답의 `attempt_id`를 사용한다.

~~~json
{
  "attempt_id": "55555555-5555-4555-8555-555555555555"
}
~~~

성공: 201. `Cache-Control: no-store`.

~~~json
{
  "found_item_id": "44444444-4444-4444-8444-444444444444",
  "code": "ABCD2345",
  "expires_at": "2026-11-21T08:30:00Z",
  "kiosk_id": "00000000-0000-0000-0000-000000000001",
  "kiosk_name": "도서관 키오스크"
}
~~~

코드는 현재 8자리 Base32 영문 A~Z·숫자 2~7이다. FE에서 “수령 코드” 또는 기존 화면의 “OTP”로 표시할 수 있다. 응답 시각으로 만료를 표시하며 유효기간을 상수로 가정하지 않는다. `kiosk_id`와 `kiosk_name`은 이 코드가 통하는 현장을 알려 준다. 좌표·칸 번호는 이 응답에 없으므로 위치 안내가 필요하면 같은 `kiosk_id`를 F03 목록에서 찾아 쓴다. 분실자 화면이 이 값으로 현장 단말의 FE-K01 설정을 바꾸지는 않는다.

발급은 다음을 검사한다: 확인 시도 존재·유효기간·정책 버전, 성공 판정, 물품 일치, 사진 확인 완료, 현재 보관·배정 상태, 검토 근거 유효성, 같은 시도의 발급 여부.

| 오류 | FE 처리 |
| --- | --- |
| 403 `OWNERSHIP_REQUIRED` | 성공한 동일 물품의 확인 시도 필요 |
| 409 `OWNERSHIP_RETURN_CODE_ALREADY_ISSUED` | 같은 시도에서는 다시 발급 불가 |
| 409 `OWNERSHIP_ITEM_NO_LONGER_AVAILABLE` | 이미 수령 중/완료 등 가용성 변경 안내 |
| 409 `OWNERSHIP_EVIDENCE_CHANGED` | 근거가 달라졌으므로 새 검색·소유 확인부터 진행 |
| 409 `OWNERSHIP_ATTEMPT_RESTART_REQUIRED` | 사진 확인을 거치지 않았거나 정책이 바뀜. 새 소유 확인부터 진행 |
| 404/410 확인 시도 오류 | 새 검색 시작 |
| 503 `RETURN_CODE_NOT_CONFIGURED` | 서버 설정 확인 필요 |

코드 평문은 이 응답에서 한 번만 제공하며 DB에는 해시로 저장한다. 코드 조회 API는 없다. 응답 유실·코드 분실 시 같은 시도로 다시 발급하지 못하므로 새 소유 확인이 필요하다. 새로 발급하면 해당 물품의 기존 유효 코드는 폐기된다. FE는 사용자에게 코드를 전달할 화면을 유지하고, localStorage·URL·분석 로그에 저장하지 않는다.

## 8. F11 수령 개방 / F12 보관·수령 완료

### F11 반환 코드로 수령 개방

`POST /api/pickup/open`

~~~json
{
  "code": "ABCD2345",
  "kiosk_id": "00000000-0000-0000-0000-000000000001"
}
~~~

`code`는 요청 스키마상 4~64자, `kiosk_id`는 필수 UUID다. 현재 발급 형식은 8자리다. 서버는 코드의 공백·하이픈을 제거하고 대문자로 정규화한다. 키오스크는 FE-K01의 확정된 설정을 사용한다.

성공: 200 `PickupView`.

~~~json
{
  "found_item_id": "44444444-4444-4444-8444-444444444444",
  "kiosk_id": "00000000-0000-0000-0000-000000000001",
  "locker_number": 2,
  "item_status": "stored",
  "assignment_status": "awaiting_pickup"
}
~~~

개방 응답의 물품 ID와 칸 번호를 업무 상태에 보관한다. 성공 시 코드가 `used`가 되고 `used_at`이 기록된다. 아직 사용자가 물건을 꺼냈다는 뜻은 아니다.

- 코드 대상과 단말의 키오스크가 다르면 409 `RETURN_CODE_WRONG_KIOSK`. 설정 자동 변경 금지.
- 이미 개방 성공했고 `awaiting_pickup`이면 같은 요청은 저장된 결과를 반환하고 다시 개방하지 않는다.
- 수령 완료 후 사용한 코드로 다시 열려고 하면 409 `RETURN_CODE_ALREADY_USED`.
- 명확한 장치 실패는 코드가 다시 시도 가능한 상태로 남을 수 있다. 통신 단절 등 결과 불명이면 `locked`로 잠겨 재개방을 막는다. FE가 모든 502를 동일하게 자동 재시도하면 안 된다.

### F12-A 보관 완료

`POST /api/registrations/{registration_id}/deposit/complete`

~~~json
{
  "confirmed": true
}
~~~

사용자가 물건을 넣었음을 확인한 뒤 보낸다. 서버가 장치의 `closed`를 확인해야 200 `RegistrationView`, `status=stored`가 된다. 시제품은 해당 칸 버튼을 눌러야 닫힘 상태가 된다.

409 `DOOR_NOT_CLOSED`이면 닫힘 조작 후 사용자 재시도. 개방이 선행되지 않으면 409 `DEPOSIT_NOT_OPENED`. 이미 보관 완료된 요청은 기존 결과를 반환한다.

문 닫힘 확인 뒤 그 칸의 최신 무게 상태를 한 번 본다. 이번 개방 이후 받은 값이 `item=empty`면 409 `ITEM_NOT_DETECTED`로 거절하고 아무것도 기록하지 않는다(등록은 `awaiting_deposit` 유지, 칸도 그대로 배정된 상태). 문은 이미 닫혀 있으므로 물건을 넣으려면 `POST /api/registrations/{registration_id}/deposit/reopen`(본문 없음)으로 같은 칸을 다시 연 뒤 완료를 다시 요청한다. FE 문구: **"물건이 감지되지 않았어요. 다시 넣어 주세요"**와 다시 열기 버튼.

### F12-B 수령 완료

`POST /api/pickup/{found_item_id}/complete`. 위와 같은 `{"confirmed":true}` 본문을 사용한다.

경로 ID는 F11 개방 응답의 물품 ID다. 등록 UUID를 넣지 않는다. 사용자가 물건을 꺼낸 것을 확인하고 해당 칸 버튼을 누른 뒤 요청한다.

~~~json
{
  "found_item_id": "44444444-4444-4444-8444-444444444444",
  "kiosk_id": "00000000-0000-0000-0000-000000000001",
  "locker_number": 2,
  "item_status": "retrieved",
  "assignment_status": "retrieved"
}
~~~

성공 200이면 배정 `ended_at`이 기록되고 칸이 재사용 가능해진다. 응답의 `locker_number`는 방금 수령한 칸 번호이지 현재 점유를 뜻하지 않는다. 이미 수령 완료된 같은 요청은 기존 결과를 반환한다.

409 `PICKUP_NOT_OPENED`/`DOOR_NOT_CLOSED`, 404 `ITEM_NOT_FOUND`, 장치 조회 실패 502/503을 처리한다. 문 상태만으로 투입·수령 완료를 자동 요청하지 않으며, 사용자 확인 없이 `confirmed=true`를 자동 전송하지 않는다.

수령 완료도 문 닫힘 확인 뒤 그 칸의 최신 무게 상태를 한 번 본다. 이번 개방 이후 받은 값이 `item=present`면 409 `ITEM_STILL_INSIDE`로 거절하고 아무것도 기록하지 않는다(배정 `awaiting_pickup` 유지, 칸 해제 없음, 반환 코드는 개방 때 쓰인 `used` 그대로). 문이 닫힌 채 물건이 남아 있으므로 `POST /api/pickup/{found_item_id}/reopen`(본문 없음)으로 같은 칸을 다시 연 뒤 완료를 다시 요청한다. 이 경로는 반환 코드를 건드리지 않으므로 "한 번 쓴 코드는 다시 쓰지 않는다"가 유지되고, `awaiting_pickup`인 배정에만 열리므로 쓴 코드를 되살리는 통로가 되지 않는다. FE 문구: **"n번 보관함에 물건이 남아 있어요. 다시 열어 꺼내 주세요"**와 다시 열기 버튼.

두 완료 모두 믿을 수 있는 값일 때만 본다. 그 칸 값이 이번 개방(재개방 포함) 이후 수신됐고, 같은 키오스크의 locker 장치 행(`locker_number` 0)이 `online`이어야 한다. 행이 없거나, 개방보다 오래됐거나, `offline`이거나, MQTT를 쓰지 않는 환경이면 판단 불가로 보고 예전처럼 사용자 확인과 문 닫힘만으로 완료한다.

### 8-1. F14 장치 상태와 경고

Pi가 가상 센서로 본 상태와 이상 상황은 HTTP가 아니라 MQTT로 백엔드에 온다([MQTT 규약](../../docs/mqtt-protocol.md)). FE는 그 결과만 아래 두 경로로 읽는다. 브로커에 직접 붙지 않는다.

**이 값으로 업무를 진행시키지 않는다.** 완료는 그대로 F12의 사용자 확인과 서버의 문 조회로 끝낸다(D03·D06). 센서는 **완료를 막을 수만 있다**: 사용자가 선언한 완료와 칸 상태가 어긋날 때 서버가 409로 거절하는 것이 전부이고, 센서가 완료를 대신 진행하거나 자동으로 요청을 만들지 않는다(F12-A·F12-B). FE도 이 값만 보고 완료 요청을 자동 전송하지 않는다. 문을 닫기 전 이 경로를 조회해 "물건이 인식되었습니다 / 아직 비어 있습니다"를 안내하면 거절 자체를 줄일 수 있다.

`GET /api/kiosks/{kiosk_id}/device-status`

~~~json
[
  {"device": "locker", "locker_number": null, "connection": "online", "door": null,
   "weight_g": null, "item": null, "reported_at": "2026-11-19T19:24:26+09:00",
   "updated_at": "2026-11-19T10:24:26Z"},
  {"device": "locker", "locker_number": 2, "connection": null, "door": "closed",
   "weight_g": 150.0, "item": "present", "reported_at": "2026-11-19T19:24:27+09:00",
   "updated_at": "2026-11-19T10:24:27Z"}
]
~~~

| 필드 | 의미 |
| --- | --- |
| `device` | `locker` 또는 `capture` |
| `locker_number` | 정수면 그 칸, `null`이면 장치 자체 |
| `connection` | 장치 행에만 있는 `online`/`offline`. 칸 행은 `null` |
| `door` | 칸의 `open`/`closed`. 서버가 Pi에 직접 묻는 F12의 문 조회와 다른 값이며 더 오래된 값일 수 있다 |
| `weight_g` / `item` | 가상 무게와 `present`/`empty`. 10g 초과를 `present`로 본다 |
| `reported_at` | Pi가 보낸 시각 |
| `updated_at` | 백엔드가 저장한 시각. 둘의 차이가 크면 장치나 구독이 끊겼을 수 있다 |

없는 키오스크는 404 `KIOSK_NOT_FOUND`. 아직 받은 메시지가 없으면 빈 배열이며, 이것은 "장치 정상"이 아니라 "상태 모름"이다. 브로커 미설정 서버에서도 빈 배열이 정상 응답이다.

`GET /api/alerts`

| 쿼리 | 타입 | 기본값 | 제약 |
| --- | --- | --- | --- |
| `kiosk_id` | UUID | 없음 | 생략하면 전체 키오스크 |
| `severity` | string | 없음 | `alarm` 또는 `warning`. 그 외는 422 |
| `since` | ISO 8601 | 없음 | 이 시각 이후만 |
| `limit` | integer | 50 | 1~200 |
| `offset` | integer | 0 | 0 이상 |

~~~json
[
  {"id": "99999999-9999-4999-8999-999999999999",
   "kiosk_id": "00000000-0000-0000-0000-000000000001",
   "code": "ITEM_NOT_TAKEN", "severity": "warning", "device": "locker", "locker_number": 2,
   "message": "2번 보관함에 물건이 남아 있습니다.",
   "detail": {"before_g": 150.0, "after_g": 150.0},
   "reported_at": "2026-11-19T19:24:29+09:00"}
]
~~~

규약 4절의 `code` 6종과 화면 처리는 다음과 같다.

| `code` | `severity` | FE 처리 |
| --- | --- | --- |
| `FORCED_OPEN` | `alarm` | 관리 화면 경보. 개방 명령 없이 문이 열렸다 |
| `ITEM_MISSING` | `alarm` | 관리 화면 경보. 강제 개방된 칸에서 물건이 사라졌다 |
| `TAMPER` | `warning` | 관리 화면 표시. 닫힌 칸에 강한 충격 |
| `ITEM_NOT_DEPOSITED` | `warning` | 보관 완료 뒤 "물건을 넣고 문을 닫아 주세요" 안내 |
| `ITEM_NOT_TAKEN` | `warning` | 수령 완료 뒤 "보관함에 물건이 남아 있어요" 안내 |
| `CAPTURE_ITEM_LEFT` | `warning` | 등록 중 "촬영 상자에 물건이 남아 있어요" 안내 |

`code`를 이 6종으로 가정하지 않는다. Pi에 새 상황이 생기면 모르는 `code`가 오고, 그때는 `message`를 그대로 보여 준다. `locker_number`는 촬영 경고에서 `null`이다.

경고는 서버가 밀어 주지 않으므로 FE가 주기적으로 조회한다. 키오스크 완료 화면에서는 완료 응답을 받은 뒤 해당 키오스크의 최근 경고를 한 번 더 조회해 띄우는 식이면 충분하다. 짧은 주기로 반복 조회하지 않는다. 보관 기간(기본 14일)이 지난 경고는 사라지며, 경고를 해소 처리하는 API는 없다.

## 9. F13 오류·지연·중복 요청 처리

### 9.1 오류 응답 형식

업무·외부 연동 오류:

~~~json
{
  "detail": {
    "code": "DOOR_NOT_CLOSED"
  }
}
~~~

입력 검증 오류는 FastAPI 표준 `detail` 배열이며 `loc`/`msg`/`type` 등을 포함한다. `GET /ready`의 503은 `detail` 문자열이다. 프록시 502/504나 처리되지 않은 500은 JSON이 아닐 수 있다. FE는 JSON 파싱 실패가 원래 HTTP 오류를 덮지 않도록 처리하고, 알 수 없는 코드는 일반 오류 안내와 재조회 경로를 제공한다.

HTTP 200의 `no_candidates`/`completed/not_confirmed`는 네트워크 오류가 아니다. 반대로 AI 요청 실패를 소유 확인 불합격으로 표시하지 않는다.

### 9.2 업무 오류 표

| HTTP | 코드 | 처리 방향 |
| --- | --- | --- |
| 403 | `OWNERSHIP_REQUIRED` | 성공한 소유 확인 필요 |
| 404 | `KIOSK_NOT_FOUND`, `CATEGORY_NOT_FOUND` | 목록 재조회·선택 확인 |
| 404 | `REGISTRATION_NOT_FOUND`, `IMAGE_NOT_FOUND`, `ITEM_NOT_FOUND` | ID 종류·해당 작업 존재 확인 |
| 404 | `OWNERSHIP_ATTEMPT_NOT_FOUND` | 새 검색 시작 |
| 404 | `OWNERSHIP_PHOTO_UNAVAILABLE` | 고정된 확인용 사진을 읽을 수 없음. 새 소유 확인 필요 |
| 404 | `RETURN_CODE_NOT_FOUND` | 코드 확인·다시 입력 |
| 409 | `INVALID_REGISTRATION_STATE` | 등록 GET으로 현재 단계 확인 |
| 409 | `SHOT_OUT_OF_ORDER`, `TWO_IMAGES_REQUIRED`, `ANALYSIS_REQUIRED` | 선행 단계 확인. 분석 전 개방은 상태 오류가 먼저 나올 수도 있음 |
| 409 | `NO_AVAILABLE_LOCKER` | 빈 칸 없음. 대기/운영 안내, 임의 번호 요청 금지 |
| 409 | `ALLOCATION_CONFLICT` | 등록 상태 재조회 후 재시도 여부 판단 |
| 409 | `DEPOSIT_NOT_OPENED`, `PICKUP_NOT_OPENED` | 대응 개방 성공 확인 |
| 409 | `DOOR_NOT_CLOSED` | 해당 칸 버튼/닫힘 확인 후 완료 재요청 |
| 409 | `ITEM_NOT_DETECTED` | 보관 완료인데 칸이 비어 있다고 보고됨. "물건이 감지되지 않았어요. 다시 넣어 주세요" + `deposit/reopen` |
| 409 | `ITEM_STILL_INSIDE` | 수령 완료인데 물건이 남아 있다고 보고됨. "n번 보관함에 물건이 남아 있어요. 다시 열어 꺼내 주세요" + `pickup/{found_item_id}/reopen` |
| 409 | `ITEM_NOT_STORED` | 물품이 현재 보관 상태가 아님 |
| 409 | `OWNERSHIP_QUESTION_NOT_CURRENT` | 확인 시도 GET으로 현재 질문 재조회 |
| 409 | `OWNERSHIP_PHOTO_NOT_PENDING` | 사진 확인 단계가 아님. GET으로 현재 상태 재조회 |
| 409 | `OWNERSHIP_PHOTO_LOAD_REQUIRED` | 사진 GET의 토큰 없이 승인함. 사진을 다시 받아 표시한 뒤 승인 |
| 409 | `OWNERSHIP_ATTEMPT_RESTART_REQUIRED` | 정책 변경 또는 사진 확인 누락. 새 확인 시도 필요 |
| 409 | `OWNERSHIP_EVIDENCE_CHANGED` | 새 검색·문답 필요 |
| 409 | `OWNERSHIP_ITEM_NO_LONGER_AVAILABLE` | 더 이상 수령 가능한 물품이 아님 |
| 409 | `OWNERSHIP_RETURN_CODE_ALREADY_ISSUED` | 같은 확인 시도로 재발급 불가 |
| 409 | `RETURN_CODE_EXPIRED` | 새 소유 확인·코드 발급 필요 |
| 409 | `RETURN_CODE_ALREADY_USED` | 수령 완료한 코드 재사용 불가 |
| 409 | `RETURN_CODE_UNUSABLE` | 잠김·폐기된 코드. 상태 확인/운영 안내 |
| 409 | `RETURN_CODE_IN_PROGRESS` | 같은 코드 개방 처리 중. 연속 제출 금지 |
| 409 | `RETURN_CODE_WRONG_KIOSK` | 올바른 현장으로 이동 안내, 설정 자동 변경 금지 |
| 409 | `RETURN_CODE_CONFLICT` | 발급/코드 처리 충돌. 기존 요청 결과 확인 |
| 410 | `OWNERSHIP_ATTEMPT_EXPIRED` | 확인 시도 20분 만료. 새 검색 |
| 422 | `OWNERSHIP_OPTION_NOT_FOUND` | 현재 질문의 선택지인지 확인 |
| 422 | 입력 검증 오류 / `INVALID_SHOT_ORDER` | UUID·본문·길이·촬영 번호 확인. 공개 경로의 잘못된 번호는 보통 표준 검증 배열 |

### 9.3 외부 연동 오류

`IntegrationError`의 코드는 `NOT_CONFIGURED`로 끝나면 503, 나머지는 502로 반환한다. 내부 Gemini나 Pi의 HTTP 상태를 브라우저 응답 상태로 그대로 전달하지 않는다.

| HTTP | 코드 | 의미 |
| --- | --- | --- |
| 503 | `BUSINESS_API_DISABLED` | 업무 API 비활성화 |
| 503 | `AI_NOT_CONFIGURED`, `OWNERSHIP_AI_NOT_CONFIGURED` | AI 키/모델 설정 누락 |
| 503 | `DEVICE_NOT_CONFIGURED`, `CAPTURE_NOT_CONFIGURED` | 장치 주소·시간 제한·촬영 설정 누락 |
| 503 | `IMAGE_STORAGE_NOT_CONFIGURED`, `RETURN_CODE_NOT_CONFIGURED` | 저장소·반환 코드 설정 누락 |
| 502 | `DEVICE_UNREACHABLE`, `INVALID_DEVICE_RESPONSE`, `DEVICE_ROLE_MISMATCH`, `DEVICE_RESPONSE_TOO_LARGE` | 장치 통신·응답 문제 |
| 502 | `COMMAND_EXPIRED`, `INVALID_COMMAND`, `UNAUTHORIZED`, `LOCKER_NOT_FOUND`, `DEVICE_BUSY`, `CAMERA_ERROR`, `LOCK_ERROR` | Pi가 반환한 명령·인증·장치 오류. `UNAUTHORIZED`는 사용자 로그인 오류가 아님 |
| 502 | `IMAGE_STORAGE_FAILED`, `INVALID_IMAGE`, `INVALID_ANALYSIS_RESULT` | 이미지 저장·읽기·검증 실패 |
| 502 | `INVALID_MODEL_OUTPUT`, `INSUFFICIENT_VISUAL_FEATURES`, `IMAGE_TOO_LARGE`, `IMAGE_LOW_QUALITY`, `INVALID_CATEGORY`, `INVALID_ITEM_ID`, `INVALID_IMAGE_COUNT`, `DUPLICATE_IMAGE_ID` | 이미지 준비·모델 결과 검사 오류. 내부 모듈의 413/422를 이 업무 API의 HTTP 상태로 가정하지 않음 |
| 502 | `OWNERSHIP_AI_TIMEOUT`, `OWNERSHIP_AI_UNREACHABLE`, `OWNERSHIP_AI_REQUEST_FAILED` | 모델 호출 실패 |
| 502 | `OWNERSHIP_AI_RATE_LIMITED`, `OWNERSHIP_AI_AUTHENTICATION_FAILED`, `OWNERSHIP_AI_MODEL_UNAVAILABLE` | 모델 공급자 요청 제한·인증·모델 설정 오류 |
| 502 | `OWNERSHIP_AI_RESPONSE_TOO_LARGE`, `OWNERSHIP_AI_INVALID_RESPONSE`, `OWNERSHIP_AI_INVALID_OPTIONS`, `OWNERSHIP_AI_INVALID_MATCHES`, `OWNERSHIP_UNGROUNDED_CLUE` | AI 응답 형식·선택지·검색 결과·단서 검증 실패 |

사진 분석도 공통 Gemini 호출기를 쓰므로 `OWNERSHIP_AI_*` 오류가 발생할 수 있다. 접두사만 보고 등록 화면에서 오류를 무시하지 않는다.

### 9.4 중복·유실 대응 표

| 요청 | 정상 중복 처리 / FE 주의 |
| --- | --- |
| 등록 생성 | 새 등록 생성. 자동 재요청 금지 |
| 성공한 같은 사진 번호 | 기존 사진 반환. 재촬영 안 함 |
| 분석 | `ready/succeeded`일 때만 캐시 응답. 실패는 상태 조회 후 사용자 재시도 |
| 투입 개방 | 이미 개방/보관 완료면 재개방 안 함. `blocked`는 운영 확인 |
| 보관·수령 완료 | 완료된 같은 건은 기존 결과 반환 |
| 소유 확인 시작 | 새 확인 시도 생성. 중복 제출 방지 |
| 답변 제출 | 중복 답변은 현재 질문 오류가 될 수 있음. GET 후 현재 질문 기준으로 처리 |
| 사진 확인 제출 | 승인 응답에 코드가 포함됨. 중복 제출은 발급 완료 오류. 자동 재전송 금지 |
| 반환 코드 발급 | 동일 시도 재발급 불가. 유실된 평문 조회 API 없음 |
| 수령 개방 | `awaiting_pickup` 중 동일 결과 반환, 수령 완료 후 같은 코드 거절 |

진행 중 버튼 중복 클릭을 막고 요청 중임을 표시한다. 데이터 변경 API를 일반 네트워크 재시도 래퍼로 무조건 반복하지 않는다. AI 호출이 여러 번 필요한 검색은 단일 호출 시간보다 오래 걸릴 수 있으며 120초 프록시 제한에 걸릴 수도 있다. 현재 비동기 작업·진행률·작업 ID API는 없다.

## 10. DB 저장과 공개 범위

FE는 DB에 직접 접속하지 않는다. 이 표는 API 응답이 어떤 기록을 남기는지 설명하기 위한 것이다.

| 테이블/저장소 | 기록 시점·내용 |
| --- | --- |
| `kiosks` / `categories` / `lockers` | 설치 정보·카테고리·사물함 번호. 목록 API는 조회만 제공 |
| `registrations` | 등록 입력, 등록/분석 상태, `analysis_input_image_ids`, `analysis_features` |
| `item_images` | 사진당 한 행. 등록 ID, 촬영 순서, S3 객체 키, JPEG 타입·바이트 수·수신 시각 |
| 비공개 S3 | 촬영 원본. `registrations/{registration_id}/{capture_command_id}.jpg` |
| `found_items` | 투입 배정 시 생성되는 실제 물품. `awaiting_deposit → stored → retrieved` |
| `storage_assignments` | 물품↔사물함 연결. `reserved → awaiting_deposit → stored → awaiting_pickup → retrieved`. 수령 완료 시 `ended_at` 기록 |
| `device_commands` | 촬영·투입 개방·수령 개방 의도를 외부 호출 전에 저장. `sent/succeeded/failed/unknown` |
| `ownership_attempts` | 후보·검토 근거·현재 질문·답변·판정·20분 만료·버전·코드 발급 여부. 내부 원문을 FE에 공개하지 않음 |
| `return_codes` | 코드 해시·물품·만료·사용 시각·상태. 평문 저장 안 함 |

원본 사진은 S3에, 사진 메타데이터는 DB에 저장한다. 분석용 전처리 이미지는 호출에 사용하고 별도로 공개하지 않는다. 분석 성공 시 `analysis_input_image_ids`는 실제 사진 UUID 두 개를 1→2 순서로 기록하며, S3 파일명의 촬영명령 ID와 구분한다.

`analysis_features` JSONB에는 다음 8개 키를 직접 저장한다. 사진마다 따로 생성하는 것이 아니라 두 사진을 종합한 한 물건의 특징이다.

~~~json
{
  "category": "pencil_case",
  "color": ["red", "black"],
  "shape": "직사각형 파우치",
  "surface_texture": "직조 무늬가 보이는 표면",
  "brand": null,
  "visible_text": ["RED"],
  "structure": ["상단 지퍼"],
  "distinctive_features": null
}
~~~

`category`/`shape`/`surface_texture`/`brand`는 문자열 또는 null, 나머지는 문자열 배열 또는 null이다. 모든 키는 필수이며 미확인은 null이다. `primary_category_id`와 모델의 `category`는 별도 값으로 자동 치환하지 않는다. API 응답에는 이 특징·S3 객체 키·정답·검토 원문을 넣지 않는다.

보관 완료는 등록·물품·배정의 `stored`를 기록한다. 수령 완료는 물품·배정만 `retrieved`로 바꾸고 배정을 종료한다. **사물함의 빈 상태는 문이 닫혔는지가 아니라 종료되지 않은 배정이 없는지로 판단한다.**

운영자 분석 JSON·사물함 확인 명령은 [팀 API 문서](../../docs/api.md)의 DB 확인 절을 사용한다. FE 전용 조회 API로 오인하지 않는다.

## 11. 현재 제공하지 않는 기능과 FE 구현 과제

| 항목 | 현재 경계 |
| --- | --- |
| FE-K01 영구 선택 복원 | 명세 완료, FE 적용 작업 필요. 기기 인증 아님 |
| 사용자 로그인·키오스크 페어링 | 미구현. 향후 기기 자격증명과 대상 키오스크의 서버 검증 필요 |
| 목록의 첫 항목 자동 선택 | 현재 FE 동작. 명시적 최초 설정으로 변경 필요 |
| 사물함 목록·빈 칸 수·실시간 문 상태 | 공개 FE 조회 API 없음. 칸 배정/완료 API 응답 사용 |
| 파일 업로드·재촬영·사진 삭제 | 없음. 현재 촬영 Pi 두 장 흐름만 제공 |
| 등록 수정·취소·배정 강제 해제 | 없음. 화면 초기화는 서버 작업 취소가 아님 |
| 장치 명령 조회·blocked 복구 | 공개 API 없음. 실패·결과 불명은 운영 확인 |
| 비공개 근거 검토·입력 | 공개 운영 API 없음. 특징 분석과 별도 단계 |
| 반환 코드 평문 재조회 | 없음. 유실 시 새 소유 확인·발급 필요 |
| 물품 상세/수령 상태 GET | 별도 공개 API 없음. 수령 작업 응답 사용 |
| 진행 중 작업의 브라우저 재시작 복구 | FE-K01은 키오스크 ID만 기억. 업무 ID 보존·복구 UX는 별도 설계 |
| Pi 물건 유무 감지 | Pi가 가상 무게 센서로 판단해 MQTT로 발행하고, 백엔드가 구독해 8-1의 두 경로로 읽혀 준다. 완료 판정은 여전히 사용자 선언과 문 닫힘 확인 조합이며(D03·D06), 센서는 어긋난 완료를 막기만 한다(F12-A·F12-B) |
| 경고 실시간 푸시·해소 처리 | 없음. FE가 8-1을 조회한다. WebSocket·SSE와 경고 해소 API는 후속 범위 |
| 장치 명령과 센서의 교차 검증 | 없음. 경고는 저장·표시만 하고 배정 기록과 자동 대조하지 않는다 |
| 후보 검색 확장 | 전체 대상 보관 물품 500건 제한. 인덱스 검색·키오스크 필터·검색 페이지네이션 없음 |

운영 기기 인증 후속 범위는 [FE-K01의 인증 경계](../../docs/web-structure.md#편의-설정과-기기-인증의-경계)를 따른다. 프론트에 인증 헤더나 새로운 요청 필드를 임의로 추가하지 않는다.

## 12. FE 인수 시나리오

아래는 연동 구현 완료 시 확인할 기준이다. 이 목록 자체가 모든 실제 환경 테스트의 통과 기록은 아니다.

| ID | 시나리오 | 통과 기준 |
| --- | --- | --- |
| FE-01 | 기초 목록·빈 카테고리·좌표 없음 | 빈 목록/null 처리, 카테고리 없이 등록 가능 |
| FE-02 | 최초 설정·새로고침·목록 오류 | FE-K01의 선택 유지·재검증·변경 규칙 준수 |
| FE-03 | 등록 생성→사진 1→사진 2 | 같은 등록 ID, 서로 다른 사진 ID, 순서 1·2 |
| FE-04 | 촬영 완료 요청 중복 | 같은 사진 반환, 추가 촬영 안 함 |
| FE-05 | 사진 부족 상태 분석·개방 | 오류 안내, 다음 단계 자동 이동 안 함 |
| FE-06 | 분석 성공·실패·재시도 | 성공 상태 확인 후 개방, 실패를 소유 불일치로 표시하지 않음 |
| FE-07 | 1번 사용 중·2번 비어 있음 | 서버 응답의 2번 안내, 직접 번호 지정 안 함 |
| FE-08 | 모든 칸 사용 중 | `NO_AVAILABLE_LOCKER` 안내, 무한 개방 재시도 안 함 |
| FE-09 | 보관 완료 전 버튼 미조작 | `DOOR_NOT_CLOSED` 안내, 조작 후 사용자 재시도 성공 |
| FE-10 | 검색 단서 없음·후보 없음·500건 초과 | 각 상태를 구분, 서버 오류 화면과 분리 |
| FE-11 | 질문→답변 반복·오래된 질문 재제출 | 최신 질문 ID 유지, 충돌 시 GET으로 복구 |
| FE-12 | 사진 확인 승인·거부·토큰 누락 | 승인 때만 `supported`와 코드 수신, 거부는 `not_confirmed`, 토큰 없는 승인은 거절 |
| FE-13 | 검토 근거 없음·만료·근거 변경 | 성공으로 우회하지 않고 보류 또는 재시작 안내 |
| FE-14 | 코드 발급·중복 발급·코드 유실 | 최초 응답 표시, 같은 시도 재발급 불가 안내 |
| FE-15 | 다른 키오스크·잘못된·만료된 코드 | 정확한 오류 안내, 단말 설정 자동 변경 안 함 |
| FE-16 | 정상 수령 개방→버튼→완료 | `retrieved/retrieved` 확인, 칸 재사용 가능 |
| FE-17 | 수령 개방·완료 중복 | 문 재개방 안 함, 완료 결과 유지, 사용 코드 재사용 거절 |
| FE-18 | 반환 후 등록 GET·같은 칸 재사용 | 200, 기존 사진·분석 유지, `locker_number=null` |
| FE-19 | 장치 통신 단절·비JSON 오류 | 요청 유실/실패 구분 안내, 자동 재개방 금지 |
| FE-20 | 공개 데이터 확인 | 특징 원문·정답·비밀 키·코드 로그 노출 없음 |
| FE-21 | 장치 상태 없음·offline·모르는 경고 코드 | 빈 배열을 정상으로 표시, offline 구분, 모르는 `code`는 `message`로 안내 |

실제 EC2 확인 시에는 21개 API를 모두 기계적으로 호출하지 말고, 위 두 업무 흐름에 필요한 요청을 순서대로 수행한다. 촬영·개방은 실제 장치 동작이며 물건·사물함 앞에서 수행한다.

## 13. 구현 근거

- [등록·반환 라우터와 DTO](../app/api.py), [업무 상태·DB 처리](../app/services.py), [ORM 모델](../app/models.py).
- [상태 확인·오류 응답](../app/main.py), [Pi·S3 어댑터](../app/integrations.py), [사진 분석 연결](../app/photo_analysis.py).
- [MQTT 메시지 해석·저장](../app/device_telemetry.py), [구독자](../app/mqtt_listener.py), [장치 상태 테스트](../tests/test_device_telemetry.py), [MQTT 규약](../../docs/mqtt-protocol.md).
- [소유 확인 라우터](../app/ai_ownership_verification/api.py), [공개 응답 모델](../app/ai_ownership_verification/workflow_schemas.py), [검색](../app/ai_ownership_verification/search.py), [문답·승인 정책](../app/ai_ownership_verification/workflow.py).
- [등록 테스트](../tests/test_registration.py), [반환 테스트](../tests/test_pickup.py), [소유 확인 테스트](../tests/test_ownership_workflow.py).
- [FE 실행·배포](../../frontend/README.md), [LED·버튼 시제품 결정](../../raspberry-pi/docs/decisions/0002-locker-led-button.md).
