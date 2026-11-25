# 웹 → 백엔드 API 계약

2026-11-23 현재 구현 기준. 화면별 흐름, 전체 24개 API의 요청·응답 필드, 상태 전이, 오류·중복 요청 처리와 FE 인수 기준은 [프론트 연동을 위한 백엔드 기능 명세](../backend/docs/functional-spec.md)를 따른다. 이 문서는 API 빠른 참조와 운영자 DB 확인 방법을 제공한다. Pi에 보내는 요청은 [장치 규약](device-protocol.md)을 따른다.

업무 API는 모두 `/api` 아래에 있다. 로컬에서는 `LOCAL_DEVELOPMENT=true`, EC2에서는 `BUSINESS_API_ENABLED=true`로 활성화한다. 둘 다 false면 503을 반환한다. 현재 로그인·키오스크 인증은 없으며, 활성화하면 인증 없이 호출할 수 있다. `/health`, `/ready`는 이 설정과 무관하다.

Pi가 없는 환경에서는 `DEV_FAKE_DEVICE=true`로 촬영·개방·문 조회를 개발용 가짜 장치가 대신한다. 촬영 응답은
더미 JPEG 2장이고 문은 항상 `closed`다. 나머지 상태 전이·S3 저장·미리보기 URL은 실제와 같으므로 FE는 계약을
그대로 따라갈 수 있다. 사진 분석은 이 더미 사진을 그대로 읽으므로 분석 결과도 더미 사진의 내용이 된다.
설정은 [EC2 배포](../deploy/README.md)에 있다.

FE가 질문 화면을 반복해서 볼 때 쓰는 보관 물품 더미데이터는 [개발 확인용 보관 물품](#개발-확인용-보관-물품-10건)에 있다.

FE의 최초 키오스크 선택·저장·복원은 [FE-K01](web-structure.md#fe-k01-키오스크-최초-1회-설정)을 따른다. 검증·확정된 선택값을 등록 생성과 수령 개방 요청의 기존 `kiosk_id`에 사용하며 API 필드는 추가하지 않는다. 브라우저의 선택값은 호출자 인증 근거가 아니고, 서버→Pi 인증 키를 FE에 전달하지 않는다. 기기 페어링·인증 API는 후속 설계 범위다.

## 등록과 조회

| 메서드 | 경로 | 입력·출력 |
| --- | --- | --- |
| GET | `/api/kiosks?limit=20&offset=0` | 개발용 위치 조회. ID·이름·장소 설명·위도·경도, 좌표는 NULL 허용 |
| GET | `/api/categories` | 등록된 카테고리 ID·code·name. 기본 목록 없음 |
| POST | `/api/registrations` | 아래 본문으로 생성, 201 |
| GET | `/api/registrations/{id}` | 등록 상태·선택 입력·사진 ID/순서 조회. 분석 진행 중에는 `analysis_stage`·`analysis_attempt`·`analysis_elapsed_ms`로 진행 표시 |
| POST | `/api/registrations/{id}/shots/1` | Pi에서 첫 JPEG 촬영·S3 저장 |
| POST | `/api/registrations/{id}/shots/2` | 사용자가 방향을 바꾼 후 둘째 촬영·저장 |
| GET | `/api/registrations/{id}/shots/{1 또는 2}/preview` | `{"url":"..."}` 비공개 S3 임시 서명 URL |
| POST | `/api/registrations/{id}/analysis` | 두 사진의 특징 추출 요청·결과 저장. 3초 안에 끝나지 않으면 `analysis_status=running`으로 응답 |
| POST | `/api/registrations/{id}/deposit/open` | 분석 성공 확인·빈 칸 배정·개방 |
| POST | `/api/registrations/{id}/deposit/complete` | `{"confirmed":true}` 사용자 완료 확인 → 문 조회 → 칸 상태 확인 → 보관 완료 |
| POST | `/api/registrations/{id}/deposit/reopen` | 본문 없음. `ITEM_NOT_DETECTED`로 막힌 뒤 같은 칸을 다시 연다 |

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

수령 완료나 `ITEM_MISSING` 사고 처리 후에는 이 현재 배정 조회에서 해당 칸이 `available`이 된다. 종료된 기록 자체는 DB에 남는다. 특정 등록의 이력을 확인하려면 물품·배정을 연결해 조회한다.

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

수령 완료는 `item_status=retrieved`, `assignment_status=retrieved`, `ended_at` 존재로 확인한다. 강제 개방 뒤 도난은 `item_status=missing`, `assignment_status=missing`, `ended_at` 존재로 확인한다. 등록 상태 `stored`는 두 경우 모두 이력으로 유지된다.

## 개발 확인용 보관 물품 10건

소유 확인 질문 화면은 보관 중인 물건이 있을 때만 질문을 만든다. 반환 코드 발급만으로는 물건이 목록에서 빠지지 않고, `POST /api/pickup/open`을 호출한 시점부터 그 물건은 후보에서 제외된다(`storage_assignments.status`가 `awaiting_pickup`으로 바뀐다). 수령까지 진행한 물건으로는 질문 흐름을 다시 볼 수 없으므로 아래 명령으로 더미 물품 10건을 만들거나 보관 상태로 되돌린다.

```bash
sudo docker exec smart-lost-found-prod-backend-1 python -m app.seed_demo_items
```

`DEV_FAKE_DEVICE=true`가 아니면 실행을 거부한다. 실제 보관함을 쓰는 운영에는 없는 물건을 넣지 않기 위한 조건이다. 로컬에서는 `backend` 디렉터리에서 `python -m app.seed_demo_items`를 실행한다.

행 ID는 uuid5로 고정되어 있어 다시 실행해도 같은 10건을 갱신한다. 재실행은 등록·물품·배정을 `stored`로 되돌리고, 남아 있던 반환 코드를 `revoked`로 바꾼다. 전용 키오스크(이름 `개발 확인용 키오스크`, 좌표 있음)와 901번부터의 보관함을 쓰므로 실제 키오스크의 칸을 차지하지 않는다. `--kiosk UUID`를 주면 기존 키오스크에 붙인다.

| 코드 | 종류 | 설명 입력 예시 | 질문 화면 |
| --- | --- | --- | --- |
| N01 | 수첩 | 수첩을 잃어버렸어요 | 후보 1건. 장식·문구 2문항 |
| B01 | 백팩 | 검은색 나이키 백팩을 잃어버렸어요 | 후보 1건. 장식·브랜드·색상 3문항 |
| P01·P02·P03 | 필통 | 도서관 1층에서 네이비 필통을 잃어버렸어요 | 후보 3건. 장식·문구로 좁힌 뒤 재질·브랜드·색상까지 5문항 |
| W01·W02 | 지갑 | 지갑을 잃어버렸어요 | 후보 2건. 장식·문구로 갈린 뒤 재질·색상까지 4문항 |
| U01·U02 | 우산 | 검은색 우산을 잃어버렸어요 | 후보 2건. 공개 재질(nylon·vinyl)로 갈리는 2문항 |
| T01 | 물병 | 파란 물병을 잃어버렸어요 | 후보 1건. 장식·형태·색상 3문항 |

문항 수는 정답만 고른 경로 기준이다. 질문은 값이 있는 특징 항목마다 하나씩 생기므로 항목마다
특징을 3~5개만 채워 두었다. 보기는 다섯 개가 되도록 Gemini가 오답 보기를 채우며, 더미 물품도
이 호출을 그대로 사용한다. 틀린 보기를 두 번 고르면 `decision=not_confirmed`로 끝난다.

사진은 10건 모두 `app/dev_assets/fake_capture_1.jpg` 한 장을 쓴다. 사진 확인 단계가 동작하도록 실행할 때마다 S3에 올리며, S3 설정이 없으면 경고를 출력하고 나머지만 만든다. 등록의 `secondary_description`은 `개발 확인용 더미 물품`이다.

## 수령 (분실자)

소유 확인을 통과한 물건에 일회성 반환 코드를 발급하고, 키오스크에서 그 코드로 보관함을 연다.
흐름과 "개방 성공 뒤에 코드를 사용 처리한다"는 원칙은 [장치 규약 6.2](device-protocol.md)를 따른다.

| 메서드 | 경로 | 입력·출력 |
| --- | --- | --- |
| POST | `/api/ownership/attempts` | `{"description":"도서관에서 남색 필통을 잃어버렸어요"}` → 질문, 검색 결과, 또는 검색이 길어지면 `pending` |
| GET | `/api/ownership/attempts/{attempt_id}` | 저장된 현재 질문·완료 판정 또는 진행 중 상태. `pending`이면 `retry_after_ms` 간격으로 재조회 |
| POST | `/api/ownership/attempts/{attempt_id}/answers` | `{"question_id":"UUID","option_id":"UUID"}` → 다음 질문, `photo_confirmation`, 또는 최종 판정 |
| GET | `/api/ownership/attempts/{attempt_id}/photo` | 확인용 등록 사진 `image/jpeg`. 응답 헤더 `X-Ownership-Photo-Token`을 다음 요청에 쓴다 |
| POST | `/api/ownership/attempts/{attempt_id}/photo-confirmation` | `{"confirmed":true,"photo_token":"UUID"}` → `{"attempt","return_code"}`. **반환 코드를 받는 정상 경로** |
| POST | `/api/found-items/{found_item_id}/return-code` | 보조 경로. `{"attempt_id":"UUID"}` → 201 `{"found_item_id","code","expires_at","kiosk_id","kiosk_name"}` |
| POST | `/api/pickup/open` | `{"code":"...","kiosk_id":"UUID"}` → 코드 확인·보관함 개방 |
| POST | `/api/pickup/{found_item_id}/complete` | `{"confirmed":true}` → 문 조회 → 칸 상태 확인 → 수령 완료 |
| POST | `/api/pickup/{found_item_id}/reopen` | 본문 없음. `ITEM_STILL_INSIDE`로 막힌 뒤 같은 칸을 다시 연다. 반환 코드는 쓰지 않는다 |

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
- 정상 수령에서 물건 상태는 `stored → retrieved`, 배정 상태는 `stored → awaiting_pickup → retrieved`다. 강제 개방 뒤 `ITEM_MISSING` 사고에서는 물건·배정이 `stored → missing`으로 바뀐다. 등록(`registrations`) 상태는 두 경우 모두 바뀌지 않는다.
- 개방 성공 시 코드를 `used`로 처리한다. 완료된 개방을 재요청하면 문을 다시 열지 않고 같은 결과를 반환하므로 FE의 중복 클릭·새로고침은 안전하다.
- 명확한 장치 실패는 코드를 그대로 두어 다시 시도할 수 있다. 통신 단절 등 결과 불명은 문이 이미 열렸을 수 있어 코드를 `locked`로 두고 같은 코드의 재개방을 막는다. 운영 복구 API는 아직 없다.
- 완료는 `confirmed=true`와 센서 `closed`를 함께 확인한다. 완료 시 배정이 종료되어 해당 칸이 즉시 다시 배정 가능해진다.
- 백엔드는 사용자 완료와 별개로 이번 개방 이후의 가상 무게 상태를 확인한다. 보관인데 `empty`, 수령인데 `present`면 완료를 거절하며, 사용자는 물건을 바로잡고 같은 칸을 다시 열 수 있다. 일반 센서 상태가 완료를 대신하지는 않는다. 단, 강제 개방 뒤 물건이 사라진 `ITEM_MISSING`은 사용자 완료 선언이 있을 수 없는 사고이므로 물품·배정을 `missing`으로 종료하고 미사용 반환 코드를 폐기한다.
- 코드의 대상 키오스크가 요청의 `kiosk_id`와 다르면 거절한다. 이는 대상 검사이고 호출자 인증이 아니다. 키오스크 세션은 여전히 미구현이다.

## 장치 상태와 경고 (MQTT 수신분)

Pi가 [MQTT 규약](mqtt-protocol.md)으로 보낸 센서 상태와 이상 상황을 백엔드가 구독해 저장하고, 아래 두 경로로 읽혀 준다. 일반 상태의 완료 판정은 사용자 확인과 문 조회가 하고(D03·D06), 센서는 완료를 **막을 수만 있다**. 보관 완료는 그 칸의 `item`이 `empty`면 409 `ITEM_NOT_DETECTED`, 수령 완료는 `present`면 409 `ITEM_STILL_INSIDE`로 거절한다. 이번 개방 이후 받은 값이고 그 키오스크의 locker 장치가 `online`일 때만 본다. 그 밖(행 없음·오래된 값·`offline`·MQTT 미설정)은 예전처럼 문 닫힘만으로 완료한다. 예외적으로 `ITEM_MISSING`은 해당 칸의 보관 물품·배정을 `missing`으로 종료하고 미사용 반환 코드를 폐기한다. 활성 보관 배정이 없거나 같은 경고가 재전송되면 업무 상태를 다시 바꾸지 않는다.

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
- 경고 조회는 `unresolved_only` 필터와 `resolved_at`을 지원한다. 오래된 미조치 warning만 기본 14일 후 정리하며 alarm과 조치 이력은 보존한다.
- `GET /api/kiosks/{id}/operation-status`로 `blocked`, `active_alarm_count`, `alarms`를 조회한다. 페이지 진입 시각과 관계없이 모든 미해결 alarm이 해당 키오스크를 차단한다. 업무 변경 요청은 `423 KIOSK_ALARM_ACTIVE`로 거부한다.
- 관리자는 `GET /api/admin/session`, `GET /api/admin/alerts?kiosk_id=...`, `POST /api/admin/alerts/{id}/resolve`를 사용한다. 해결 입력은 `{confirmed:true,note:"조치 내용"}`이며 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 인증이 필요하다. 전체 계약·인증·전환 조건은 [F14](../backend/docs/functional-spec.md#f14-운영-중단과-관리자-조치)를 따른다.
- 관리자 화면 첫 화면은 모든 키오스크의 미조치 알람·경고 건수를 한 번에 보여 준다. 이를 위한 `GET /api/admin/kiosks/overview`는 아직 구현 전이고 계약은 [프론트 요청](frontend-admin-overview-api.md)에 있다. 그때까지 프론트는 `GET /api/alerts?unresolved_only=true&limit=200`으로 직접 센다.
- `MQTT_HOST`를 설정하지 않은 서버에서는 두 경로가 정상 응답하지만 내용이 비어 있다.

## AI 통신 상태

| 메서드 | 경로 | 입력·출력 |
| --- | --- | --- |
| GET | `/api/ai/status` | 모델 호출 실측 소요 시간·결과 코드 분포와 검색 제한값. 공급자를 호출하지 않음 |

사진 분석도 같은 방식이다. `POST /api/registrations/{id}/analysis`가 등록을 `analyzing/running`으로 선점하고 배경에서 분석하며, 3초 안에 끝나지 않으면 그 상태를 그대로 반환한다. FE는 `GET /api/registrations/{id}`를 `analysis_retry_after_ms` 간격으로 호출해 `analysis_stage`(사진 준비·특징 분석·결과 확인)와 경과 시간을 표시한다. 분석 예산은 90초이고, 작업자가 사라진 등록은 조회 시 `draft/failed` + `ANALYSIS_TIMED_OUT`으로 정리되어 다시 요청할 수 있다. 자세한 절차는 [F08](../backend/docs/functional-spec.md#f08-두-사진-분석)에 있다.

소유 확인 검색은 단서 추출·후보 비교·선택지 생성을 이어서 호출하므로 보관 건수와 공급자 응답에 따라 시간이 달라진다. 서버는 확인 시도를 먼저 저장하고 배경에서 검색하며, 3초 안에 끝나지 않으면 `pending`을 돌려준다. FE는 `attempt_id`로 조회를 반복해 진행 상황과 경과 시간을 표시한다. 한 검색의 예산은 25초이고 초과 시 502 `OWNERSHIP_AI_TIMEOUT`이다. 응답 형식과 필드는 [F02-A](../backend/docs/functional-spec.md#f02-a-ai-통신-상태와-소요-시간), 검색 계약은 [F09](../backend/docs/functional-spec.md#6-f09-자연어-검색질문소유-확인)를 따른다.

## 오류

업무/연동 오류: `{"detail":{"code":"ERROR_CODE"}}`. 입력 검증 422는 FastAPI 표준 오류 배열이다. 외부 오류 원문·키·주소를 응답에 복사하지 않는다.

| HTTP | 주요 code |
| --- | --- |
| 403 | `OWNERSHIP_REQUIRED` |
| 404 | `KIOSK_NOT_FOUND`, `CATEGORY_NOT_FOUND`, `REGISTRATION_NOT_FOUND`, `IMAGE_NOT_FOUND`, `ITEM_NOT_FOUND`, `RETURN_CODE_NOT_FOUND`, `OWNERSHIP_PHOTO_UNAVAILABLE` |
| 409 | `OWNERSHIP_PHOTO_NOT_PENDING`, `OWNERSHIP_PHOTO_LOAD_REQUIRED`, `INVALID_REGISTRATION_STATE`, `SHOT_OUT_OF_ORDER`, `TWO_IMAGES_REQUIRED`, `ANALYSIS_REQUIRED`, `NO_AVAILABLE_LOCKER`, `ALLOCATION_CONFLICT`, `DEPOSIT_NOT_OPENED`, `DOOR_NOT_CLOSED`, `ITEM_NOT_DETECTED`, `ITEM_STILL_INSIDE`, `ITEM_NOT_STORED`, `PICKUP_NOT_OPENED`, `RETURN_CODE_EXPIRED`, `RETURN_CODE_ALREADY_USED`, `RETURN_CODE_UNUSABLE`, `RETURN_CODE_IN_PROGRESS`, `RETURN_CODE_WRONG_KIOSK`, `RETURN_CODE_CONFLICT` |
| 502 | `DEVICE_UNREACHABLE`, `INVALID_DEVICE_RESPONSE`, `DEVICE_ROLE_MISMATCH`, `DEVICE_RESPONSE_TOO_LARGE`, `INVALID_IMAGE`, `IMAGE_STORAGE_FAILED`, `INVALID_ANALYSIS_RESULT`, 규약의 Pi 오류 code |
| 503 | `BUSINESS_API_DISABLED`, `AI_NOT_CONFIGURED`, `DEVICE_NOT_CONFIGURED`, `CAPTURE_NOT_CONFIGURED`, `IMAGE_STORAGE_NOT_CONFIGURED`, `RETURN_CODE_NOT_CONFIGURED` |

소유 확인의 만료 410, 현재 질문 충돌 409, 검토 근거 변경, AI 오류 등 전체 오류와 FE 처리 규칙은 [기능 명세의 오류 계약](../backend/docs/functional-spec.md#9-f13-오류지연중복-요청-처리)에 정리한다. 사진 분석에서도 공통 호출기의 `OWNERSHIP_AI_*` 오류가 올 수 있다.

자연어 검색·질문·답변은 `/api/ownership/attempts` 흐름으로 제공한다. 반환 코드는 성공한 동일 물품의 소유 확인이 있어야 발급한다. 별도 물품 검색 목록 API나 `search_stored_items` 공개 API는 없다. 후보 검색은 전체 대상 보관 물품 500건 제한이며, 질문용 검토 근거 등록과 키오스크 기기 인증은 별도 후속 범위다.
