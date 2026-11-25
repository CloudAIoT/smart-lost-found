# 프론트 수정과 가짜 Pi 통합 시험 계획

2026-11-25 작성. 대상은 현재 작업 폴더의 프론트엔드, EC2 업무 API, 가짜 하드웨어로 실행한 Pi 컨트롤러다. 수령·장치 표시 5건과 경고 처리 3건을 인수 기준으로 삼고, 등록부터 웹 소유 확인·OTP 발급·수령까지의 전체 흐름과 최근 URL 분리를 함께 검증한다.

아래 표는 **실행할 시나리오**다. 기존 자동 시험의 통과를 EC2 종단 시험 통과로 대신하지 않는다. 이번 계획 작성에서는 서버와 DB를 조회했으며, 설정 변경·등록 생성·개방·완료·경고 발행은 실행하지 않았다.

## 현재 환경과 시험 시작 조건

| 확인 항목 | 11월 25일 조회 결과 | 실행에 필요한 조치 |
| --- | --- | --- |
| 로컬 프론트 | `http://127.0.0.1:5173`, `/api`는 `http://52.78.12.168`로 프록시 | 이 연결을 통합 시험에 사용 |
| EC2 | `/health`, `/ready`, `/api/kiosks`, `/api/ai/status` 200 | 설정 여부와 실제 연동 성공을 구분해 기록 |
| 백엔드 장치 선택 | 실행 중 컨테이너의 `DEV_FAKE_DEVICE=false` | 가짜 Pi 컨트롤러에 실제 HTTP를 보내는 구성에서도 false 유지 |
| 외부 연동 | AI·S3·MQTT 설정 존재 | 실제 촬영 저장·분석 요청으로 각각 검증 |
| 실제 Pi | `slf-capture`, `slf-locker` 오프라인 | 실물 대신 PC에 촬영·보관함 컨트롤러 실행 |
| 현재 단말 | Swagger 테스트 키오스크, 1·2번 모두 활성 배정 있음 | 기존 배정을 비우지 않고 새 테스트 키오스크 사용 |
| 기존 개발 물품 | 개발 확인용 키오스크의 901~910번 모두 활성 배정 있음 | 전체 seed 재실행으로 기존 물품·코드를 초기화하지 않음 |
| EC2 자원 | RAM 약 911 MiB, 조회 당시 가용 약 228 MiB, 디스크 여유 약 3.8 GiB | 추가 Pi 프로세스는 PC에서 실행, 부하 시험은 소규모·순차 진행 |
| 촬영 자료 | 저장소 기본 JPEG는 `DEV DUMMY` 안내 이미지. 확인한 기존 테스트 등록 사진도 더미였음 | 실제 AI 성공 검증용으로 같은 물건의 서로 다른 면 사진 2장 준비·내용 확인 |

서버 상태·빈 칸·자원 수치는 실행 직전에 다시 조회한다. `configured=true`만으로 AI 정상이나 S3 쓰기 성공을 판정하지 않는다.

## 시험 방식

| 표기 | 구성 | 검증 가능한 범위 |
| --- | --- | --- |
| I | 현재 로컬 FE → 실제 EC2 API·DB·S3·AI → PC의 가짜 Pi. 센서·경고는 EC2 MQTT 경유 | 실제 요청 계약, 서버 판정, DB 전이, 장치 명령, 화면의 종단 연동 |
| M | 동일 프론트 코드를 Playwright의 격리 브라우저에서 실행하고 API 응답·시간·지연을 제어 | 프론트의 중복 제거, 시각 경계, 희귀 오류, 응답 유실·순서 역전. 서버 동작 검증으로 세지 않음 |
| B | 백엔드·Pi의 기존 테스트와 필요한 계약 시험을 별도 테스트 DB/스키마에서 실행 | 서버 상태 전이·멱등성·오류 계약. 실제 EC2·실물 검증으로 세지 않음 |

`I+M`은 실제 연동과 모의 응답 시험을 각각 기록한다. M의 API는 EC2로 전달하지 않으며 기존 시험 서버 포트 5189를 사용한다. 통합 시험 탭과 모의 시험 탭을 구분한다.

### 가짜 모드의 차이

- `DEV00000`: FE 개발 전용 OTP. 개방·완료 API를 호출하지 않으므로 화면 회귀 시험에만 쓴다.
- `DEV_FAKE_DEVICE=true`: 백엔드의 `FakeDeviceGateway`. 기본 JPEG 반환, 개방 성공, 문 항상 `closed`. HTTP Pi 통신·문 열림·센서 경고를 검증할 수 없고, 기본 사진으로 실제 물품 분석 성공도 보장할 수 없다.
- `PI_FAKE_HARDWARE=true`와 `PI_SIMULATION=true`: Pi 컨트롤러의 실제 HTTP·MQTT 규약을 유지하면서 문·무게·충격을 제어한다. 이번 통합 시험의 기본 방식이다. 백엔드는 `DEV_FAKE_DEVICE=false`여야 이 컨트롤러를 호출한다.

### 준비할 구성

1. 실행별 식별자를 정하고 전용 테스트 키오스크 A·B와 1·2번 칸을 만든다. 기존 Swagger·개발 확인용 데이터와 구분한다. 테스트 물품·등록·배정·경고 ID를 실행 기록에 모은다.
2. PC에서 `raspberry-pi` 컨트롤러를 촬영/보관함 역할로 각각 띄운다. 두 키오스크를 동시에 비교할 시험에서는 B의 별도 컨트롤러 인스턴스 또는 격리된 응답을 사용한다. 센서 저장 파일도 인스턴스마다 다르게 둔다.
3. HTTP는 EC2가 접근할 수 있는 PC Tailscale 주소를 사용하고, MQTT는 기존 EC2 브로커에 연결한다. 테스트 키오스크 토픽에 발행 가능한 계정과 인스턴스별 client ID를 준비한다. 백엔드의 읽기 전용 MQTT 계정을 발행에 쓰지 않는다. API 키는 서버/시험 프로세스 환경에만 둔다. 먼저 EC2에서 Pi의 인증된 `/health`와 역할을 확인한다.
4. 기존 `PI_DEVICES` 항목을 유지하면서 테스트 키오스크 매핑을 추가한다. 적용에는 백엔드 설정 반영과 재시작이 필요하다. 적용 전 원본을 보존하고 적용 후 `/ready`와 기존 매핑을 확인한다. nginx·업무 API 계약 변경은 필요 없다.
5. 기본 `FakeCamera`는 글자만 있는 이미지를 생성한다. 정상 AI 시험은 `create_app(camera=...)`의 기존 주입 지점에 시험용 카메라를 연결해 준비한 물건 사진 두 장을 반환한다. 성공 상태나 분석 특징을 미리 넣지 않고 실제 S3 저장·Gemini 분석을 거친다.
6. 문은 수동 시험 중 저절로 닫히지 않도록 `PI_FAKE_DOOR_OPEN_SECONDS`를 충분히 길게 설정한다. 기본 시험은 `PI_SIMULATION_AUTO=false`로 두고 `put/take/close`를 명시적으로 수행한다. 자동 모드와 `skip`은 별도 시험한다.
7. `/kiosk/settings`에서 A를 선택하고 `/kiosk`를 연다. `/web`은 별도 탭/브라우저, `/admin/devices`는 운영 확인용 탭으로 연다. 업무 중 설정을 바꾸기 위해 진행 화면을 강제로 초기화하지 않는다.

물건 사진이 준비되지 않으면 장치·센서·경고 및 모의 API 시험을 먼저 진행한다. 분석 성공 값을 주입한 시험은 실제 AI를 포함한 전체 파이프라인 통과로 기록하지 않는다. 기존 seed 물품의 901~910번은 기본 가짜 보관함의 1~2번과도 맞지 않으므로 그대로 재사용하지 않는다.

### 화면에서 한 시나리오를 진행하는 방법

1. 사용할 키오스크·칸·등록·물품·코드와 센서 초기값을 확인한다.
2. 사용자 화면에서 버튼을 눌러 업무를 진행한다. 센서 조작은 별도 시험 터미널에서 수행한다.
3. API 경로·메서드·키오스크/업무 ID·상태 코드·오류 코드를 기록한다. 비밀키·OTP 평문·서명 URL은 보고서에서 제외한다.
4. 실제 센서 시험에서는 MQTT 수신 후 `/device-status`에 기대 값이 반영됐는지 확인하고 완료 버튼을 누른다. 고정된 몇 초 대기로 전달 성공을 추정하지 않는다.
5. 화면 결과와 서버 상태를 함께 확인한다. 실패라면 성공 화면·배정 해제·추가 개방이 발생하지 않았는지도 확인한다.
6. 성공/실패/환경 차단/미실행, 증거 경로, 남은 테스트 배정을 기록한 뒤 다음 시나리오로 넘어간다.

## 두 프롬프트의 인수 기준

| 요구사항 | 해당 시나리오 |
| --- | --- |
| 수령 완료 센서 게이트 제거 | P01~P09, S03 |
| pickup-open 처음으로 | P10~P12 |
| 장치 행 `locker_number === null` | D01~D02 |
| `item` 명시적 판정 | D03~D04 |
| `connection` 명시적 판정 | D04~D06 |
| 첫 조회부터 고정 since | A01~A04, A10, A15 |
| 표시 목록과 별개 id 중복 제거·키오스크 격리·페이지네이션 | A05~A09, A11~A15 |
| 경고의 칸 번호 표시·fallback | A16~A18 |

warning 코드별 자동 화면 전환은 이번 인수 대상에 넣지 않는다. 추가 합의한 alarm 정책은 해당 키오스크 전체 업무 중단과 관리자 조치 대기이며, 아래 H 시나리오로 검증한다. 경고를 보관·수령 성공으로 처리하면 실패다. 같은 키오스크의 다른 칸 alarm도 중단 대상이며, 다른 키오스크의 alarm은 영향을 주지 않는다.

## Alarm 운영 중단과 관리자 조치 추가 인수 기준

기존 103개 기본 시나리오에 다음 항목을 추가한다. 기존 A/P 표의 사용자 확인·완료 가능 검증은 warning을 전제로 한다. alarm 시험은 `0007_alert_resolution` 마이그레이션과 새 API를 반영한 백엔드가 필요하다. 기존 공개 HTTP 프록시로 관리자 비밀번호를 전송하지 않으며, 로컬 또는 보호된 전송 경로에서 관리자 시험을 진행한다.

| ID | 조작과 확인 대상 | 기대 결과 | 방식 |
| --- | --- | --- | --- |
| H01 | 페이지 진입 전의 미해결 alarm이 있는 키오스크 접속 | 처음부터 관리자 대기 모달, since로 누락되지 않음 | I+M |
| H02 | 메인·OTP 입력·촬영·보관·수령 중 alarm 수신 | 해당 키오스크 전체 업무 차단, 현재 업무 상태 보존 | I+M |
| H03 | ESC·바깥 클릭·Tab/Enter·처음으로·뒤로가기 시도 | 모달을 사용자 조작으로 해제하지 못함 | M |
| H04 | 미해결 alarm 상태에서 새로고침·설정 URL 직접 접속 | 서버 상태 재조회 후 차단 유지 | I+M |
| H05 | 다른 탭에서 저장된 키오스크 설정 변경 | 현재 중단 중인 화면은 기존 키오스크를 유지 | M |
| H06 | warning 확인 및 다른 키오스크 alarm 수신 | warning 확인 가능, 현재 키오스크 업무 계속 | I+M |
| H07 | UI 폴링 전 업무 API 직접 호출 | 423 KIOSK_ALARM_ACTIVE, 추가 장치 명령·업무 변경 없음 | B+M |
| H08 | 관리자 인증 없음·오류·서버 설정 없음 | 각각 401/503, 해제되지 않음 | B+M |
| H09 | 조치 내용 없음·공백·해결 확인 미체크 | 해결 요청 거부, 관리자 검토만으로 재개하지 않음 | B+M |
| H10 | 관리자 별도 탭에서 조치 완료 | 서버 시각·인증 계정·내용 기록, 다음 상태 조회에 반영 | I+M |
| H11 | alarm 여러 개 중 하나만 해결 | 나머지가 남으면 계속 중단, 모든 alarm 해결 후 재개 | I+M |
| H12 | 관리자 조치 요청 중복·실패·응답 유실 | 최초 감사 기록 보존, 성공 확인 없이 임의 해제하지 않음 | B+M |
| H13 | 같은 문구의 새 id alarm, 200건 초과 alarm | 새 id는 재차단, 미리보기 한도로 차단 해제되지 않음 | B+M |
| H14 | 최초/진행 중 운영 상태 조회 실패 | 확인 대기·차단 유지, 유효한 서버 응답 뒤 재개 | M |
| H15 | 조치 완료 뒤 기존 수령 재시도, present 센서 유지 | 기존 409 ITEM_STILL_INSIDE·다시 열기 유지, 관리자 조치가 완료를 대신하지 않음 | I+B+M |
| H16 | ITEM_MISSING 조치 완료 | missing 물품·배정과 revoked OTP 유지, 정상 수령으로 복원하지 않음 | B |
| H17 | 관리자 warning 해결 후 같은 id 재조회 | 다른 단말에서도 숨김, 새 id warning은 표시 | I+M |
| H18 | 관리자 목록 키오스크 변경·과거 이력·페이지 이동 | 대상과 장치 표 일치, 이전 요청 혼입 없음, 조치 이력 유지 | M |
| H19 | 완료 요청의 문 조회 중 alarm 저장 | 완료 커밋 거부, awaiting 상태 유지 | B |
| H20 | 오래된 alarm·해결 이력 정리 작업 실행 | 자동 보존 기간 정리로 alarm/감사 기록이 삭제되지 않음 | B |

11월 25일 로컬 자동 검증: 백엔드 215 통과·3 건너뜀(PostgreSQL 동시성 전용), 프론트 단위 36 통과, 브라우저 29개 시나리오 통과(전체 26개와 추가·변경된 15개 재검증의 고유 시나리오 수), lint/build 통과. 브라우저 회귀는 모의 API 응답이며 백엔드는 SQLite·장치 대역을 사용했다. 이 수치는 위 모든 H 변형이나 실제 EC2/Pi 시험의 완료를 뜻하지 않는다. PostgreSQL 동시성 시험은 별도 PostgreSQL의 `TEST_DATABASE_URL`이 필요하다.

별도 로컬 HTTP 통합 시험 1회 통과: 실제 프론트(5191) → 새 백엔드(8191) → 격리 SQLite·가짜 장치로 수령을 시작했다. MQTT 수신 처리 함수에 alarm/센서 메시지를 주입해 관리자 대기 모달과 423 차단을 확인하고, 별도 관리자 탭의 실제 인증·조치 API로 해제했다. 조치 직후 배정은 `awaiting_pickup`, 개방 횟수는 그대로였다. 이어서 present 센서에서 409 ITEM_STILL_INSIDE → 다시 열기 → empty 센서 → 완료를 수행해 물품·배정 `retrieved`와 관리자 감사 기록을 확인했다. API 응답을 모킹하지 않았으며 실제 MQTT 브로커·Pi HTTP·S3·Gemini를 검증한 시험은 아니다. 시험 프로세스와 DB는 운영 환경과 분리했다.

## 정상 파이프라인

| ID | 조작과 확인 대상 | 기대 결과 | 방식 |
| --- | --- | --- | --- |
| F01 | `/kiosk/settings`에서 A 확정 → 새로고침 → 등록 시작 | A 복원, 등록 POST는 1회이며 본문의 `kiosk_id`가 A | I |
| F02 | 같은 등록에서 1차 촬영 → 방향 안내 → 2차 촬영 → 미리보기 | 두 이미지의 순서·ID·S3 기록 일치, 다른 등록의 사진 혼입 없음 | I |
| F03 | 등록 완료·보관함 열기 선택, 실제 사진 분석이 끝날 때까지 대기 | 분석은 한 번 시작하고 GET으로 진행 확인, `succeeded` 이후에만 `deposit/open`, 응답의 칸 번호 표시 | I |
| F04 | 배정된 칸에 `put 150` → `close` → 센서 반영 확인 → 보관 완료 | 등록·물품·배정 `stored`, 해당 칸 점유, 보관 완료 화면 | I |
| F05 | `/web`에서 실제 사진에 맞는 설명 입력 → 질문에 답변 | 실제 AI 검색·질문 표시, 현재 question/option ID만 제출, 다른 후보의 OTP 없음 | I |
| F06 | 등록 사진을 확인하고 본인 물건 승인 | 사진 응답 토큰을 포함해 승인 1회, 8자리 코드·대상 키오스크·만료 시각 표시, 휴대폰에서 키오스크 모드로 이동하지 않음 | I |
| F07 | A에서 정상 발급 코드 입력 → 수령 개방 | 같은 물품·칸을 열고 `awaiting_pickup`, 정상 발급 코드 사용. `DEV00000` 사용 금지 | I |
| F08 | `take` → `close` → 센서 반영 확인 → 수령 완료 → 처음으로 | 물품·배정 `retrieved`, `ended_at` 존재, 칸 해제, A 설정 유지 | I |
| F09 | 완료한 칸으로 다음 등록 → 이전 OTP 재입력 | 칸 재사용 가능, 이전 코드는 거절, 이전 업무 ID·사진·경고 안내가 새 업무로 새지 않음 | I |

F01~F08은 **같은 물건 한 건**으로 이어서 진행한다. F05에서 검색 실패가 나오면 검색만 다른 seed로 대체해 종단 성공으로 기록하지 않는다. 실제 AI 문구·질문 수는 고정하지 않고 계약·판정·대상 물품의 일치를 확인한다.

## 수령 흐름 수정

| ID | 재현 방법 | 기대 결과 | 방식 |
| --- | --- | --- | --- |
| P01 | device-status가 빈 배열인 상태에서 정상 수령 개방 | 센서 대기 없이 완료 버튼 활성, 클릭하면 complete API 호출 | I+M |
| P02 | device-status만 timeout/502/네트워크 실패 | 완료 요청 가능, 장치 상태 조회 실패를 수령 불가로 단정하지 않음 | M |
| P03 | 10초 상태 폴링 사이에 문을 열고 닫아 open을 FE에 전달하지 않음 | 열림 관측 없이 완료 요청 가능 | I+M |
| P04 | 개방보다 오래된 `updated_at`, 서로 다른 ISO 시간대 표기, 브라우저 시각 ±1일을 각각 적용 | 어떤 경우에도 FE 센서/날짜 조건으로 완료를 잠그지 않음. 최종 성공·거절은 서버 응답에 따름 | M+B |
| P05 | 같은 칸의 새 `ITEM_NOT_TAKEN` 경고가 표시되는 중 완료 선택 | 경고·다시 열기 안내가 있어도 완료 버튼과 요청 경로 유지 | I+M |
| P06 | 문을 열어둔 채 완료 → 서버가 `DOOR_NOT_CLOSED` → 닫고 재시도 | 첫 요청은 실제 서버 409, 성공 화면으로 가지 않음. 닫힌 뒤 같은 물품 완료 성공 | I |
| P07 | 온라인 컨트롤러·개방 이후 `present`·문 closed에서 완료 → 다시 열기 → take/close → 완료 | 409 `ITEM_STILL_INSIDE`, 칸 번호 안내, 같은 칸 reopen, 새 OTP 없이 성공 | I+M |
| P08 | complete 응답을 지연시키고 완료·처음으로·재개방을 연속 클릭 | 처리 중 버튼 비활성, 완료 POST 한 번, 동시에 Home/reopen 요청 없음 | M |
| P09 | complete 502 또는 응답 유실 → 상태 확인/같은 완료 재시도 | 성공을 추정하지 않음. 재시도해도 서버 완료 기록·종료 시각이 중복 변경되지 않음 | M+B |
| P10 | pickup-open에서 처음으로 | 메인 표시, 선택 키오스크 유지, complete/cancel API 호출 0회. 미완료 업무 계속하기로 같은 수령 복귀 | I+M |
| P11 | 409 안내 후 처음으로 → 계속하기 → 다시 열기 → 완료 | 기존 물품·칸·키오스크 유지, 새 개방/코드 발급 없이 복구 | I+M |
| P12 | open/complete/reopen 각각 응답 대기 중 처음으로 | 중복 화면 동작 차단. 요청 결과 전 메인으로 이탈하지 않음 | M |
| P13 | 개발 서버에서 `DEV00000` → Home/계속하기 → 완료 | `locker_number: 0` 개발 흐름 유지, 장치·수령 API 호출 0회 | M |
| P14 | build/preview에서 `DEV00000` 입력 | 개발용 성공 분기를 타지 않음. 실제 OTP와 같은 검증 경로 사용 | M |

P04의 브라우저 시각 시험은 **완료 버튼 게이트 제거**를 판정한다. 경고 since는 페이지 진입 시각을 사용하므로 단말 시각이 틀린 환경의 경고 수신까지 보장한다고 해석하지 않는다.

## 장치 상태 표시

| ID | 재현 방법 | 기대 결과 | 방식 |
| --- | --- | --- | --- |
| D01 | locker 장치 행 `locker_number:null`, capture 장치 행을 조회 | 관리자 표에서 컨트롤러/촬영 장치로 표시, `null번` 금지. 키오스크 상단에 두 장치 표시 | I+M |
| D02 | 정수 칸 1·2와 장치 행을 함께 조회 | 정수는 칸으로 표시, 상단 장치 목록에 칸이 섞이지 않음 | I+M |
| D03 | item을 present → empty → null로 각각 제공 | 감지됨 → 비어 있음 → `-`/상태 미확인. empty를 truthy로 감지 처리하지 않음 | I+M |
| D04 | item present/empty/null × connection online/offline/null 9조합 | 각 항목을 독립 표시, 특히 null 연결을 오프라인으로 단정하지 않음. 실제 계약 밖 조합은 M으로 검사 | M |
| D05 | 장치 online → offline → 재접속 online | 두 화면의 장치 상태가 응답대로 변경, 칸 행의 connection:null은 계속 미확인 | I |
| D06 | 빈 배열, 누락 필드, 상태 조회 실패를 각각 제공 | 빈 상태·미확인/오류를 정상으로 표시하지 않음, 화면이 깨지거나 업무가 잠기지 않음 | M |
| D07 | `/admin/devices` 첫 화면에서 다른 키오스크 카드를 여는 중 이전 응답을 늦게 반환 | 선택한 키오스크와 표/경고의 대상 일치. 불일치하면 별도 결함으로 기록 | M |
| D08 | 관리자 화면 직접 접속·새로고침, 과거 경고를 함께 조회 | 관리 표 정상 표시, 과거 경고 조회에 키오스크의 고정 since 정책을 적용하지 않음 | I+M |
| D09 | 장치 행과 칸 행이 섞인 응답을 관리자 상세 뷰에서 조회 | 라즈베리파이 장치 표에는 연결만, 보관함 표에는 문·무게·물건감지만. 값이 없으면 '상태 모름' | M |
| D10 | 관리자 첫 화면에서 알람·경고가 없는 키오스크를 포함해 조회 | 모든 키오스크가 카드로 표시되고 건수·운영 중단 여부가 상세 뷰와 일치 | M |

## 경고 조회와 중복 제거

| ID | 재현 방법 | 기대 결과 | 방식 |
| --- | --- | --- | --- |
| A01 | 경고가 며칠 전부터 존재하는 키오스크로 새 페이지 진입 | 첫 GET부터 since 존재, 과거 경고는 키오스크에서 표시되지 않음 | I+M |
| A02 | 3회 이상 폴링하고 최신 경고를 추가 | 모든 요청의 since가 최초값과 같음. 응답의 reported_at으로 전진하지 않음 | I+M |
| A03 | main → 입력 → 개방 → Home → 계속하기, 일반 재렌더·시간 표시 갱신 | 같은 페이지 인스턴스의 since/id 기억 유지 | M |
| A04 | 진입시각 T, 먼저 T+20초 경고 수신, 나중에 T+10초 경고 도착 | 늦게 온 새 id도 수신·표시 | I+M |
| A05 | 같은 id를 여러 폴링에서 반복 | 1회 표시, 동일 경고 중복 행 없음 | I+M |
| A06 | 확인으로 지운 id를 2회 이상 다시 응답 | 재등장하지 않음, 서버 해소/삭제 API를 호출하지 않음 | I+M |
| A07 | 5개 표시 제한을 넘는 새 id 7개 수신 후 목록에서 밀린 id 재응답 | 표시 제한 유지, 밀린 id도 재등장하지 않음 | M |
| A08 | 한 API 응답 안에 같은 id를 2~3번 삽입 | 한 번만 처리 | M |
| A09 | 문구·reported_at은 같고 id만 다른 2개 경고 | 별개 경고로 처리 | M |
| A10 | since 직전/정확히 같은 시각/직후, 다른 시간대 표기 조합 | 서버 `reported_at >= since` 경계와 일치, 최초 기준 유지 | M+B |
| A11 | A 조회를 지연 → 유휴 상태에서 B로 변경 → A 응답 도착 | A 응답이 B 화면에 섞이지 않음. A 재진입 시 취소된 응답의 id가 잘못 소진되지 않음 | M |
| A12 | A 경고 확인 → B 경고 확인 → A 복귀 | 키오스크별 표시·확인 상태 구분 | I+M |
| A13 | 200/201/400/401/600개 경고, 마지막 페이지에 늦은 새 id 배치 | limit 200, offset 증가로 전 페이지 확인. 보이는 개수는 5개여도 수신 id는 모두 기억 | M+B |
| A14 | 첫 페이지 성공 후 다음 페이지 실패 → 다음 폴링 성공; 조회 중 확인 클릭 | 부분 실패로 id가 먼저 소진되지 않음. 이미 확인한 경고는 복구 폴링에서도 재등장하지 않음 | M |
| A15 | `/kiosk/settings` 왕복, A/B 변경, 새로고침을 각각 수행 | 내부 이동·키오스크 변경에는 기준 유지. 실제 새 문서 로드는 새 진입시각으로 시작 | M |
| A16 | 알려진 code의 message에만 번호가 있을 때, locker_number 1/2/0 | ALERT_LABELS가 우선해도 별도의 칸 번호 표시. null이 아닌 0도 누락하지 않음 | M |
| A17 | capture 경고 `locker_number:null` | 촬영 경고만 표시, `null번`/가짜 칸 번호 없음 | I+M |
| A18 | 모르는 code + message, message 없는 code, code/message 모두 없는 응답 | message → code → 기본 안내 fallback, 자동 화면 전환 없음 | M |
| A19 | 페이지네이션 도중 새 경고 삽입·동일 시각 다수 경고 | 중복은 제거하고 다음 정상 폴링까지 누락 회복. 스냅샷이 없는 offset 조회의 순간적 변동과 영구 누락을 구분 | M+B |
| A20 | 다른 키오스크·다른 칸·개방 전의 ITEM_NOT_TAKEN/ITEM_NOT_DEPOSITED | 진행 업무의 대상을 바꾸거나 완료 성공으로 만들지 않음. 다른 칸의 재개방 안내가 현재 칸을 가리키지 않음 | I+M |

A07·A13은 모든 경고를 동시에 화면에 보여 달라는 요구가 아니다. 현재 표시 제한 5개와 별개로 받은 id를 기억하는지 검사한다. A04의 MQTT `at`은 페이지 진입 이후이며 이미 받은 최신 경고보다 이른 값으로 만든다. MQTT 재전송과 API 동일 id 반복은 각각 다른 계층의 중복 시험이다.

## 가상 센서에서 만드는 예외

| ID | 조작 순서 | 화면과 서버 기대 결과 | 방식 |
| --- | --- | --- | --- |
| S01 | 빈 칸 정상 개방 → 넣지 않고 close → 완료 → 다시 열기 → put/close → 완료 | ITEM_NOT_DEPOSITED 경고, 서버 409 ITEM_NOT_DETECTED, 같은 칸 복구 후 stored | I |
| S02 | 보관 물품 정상 수령 개방 → 꺼내지 않고 close → 완료 → 다시 열기 → take/close → 완료 | ITEM_NOT_TAKEN 경고, 서버 409 ITEM_STILL_INSIDE, 같은 칸 복구 후 retrieved | I |
| S03 | 개방 중 완료, 상태 폴링에는 closed의 오래된 값만 보이게 구성 | 버튼은 클릭 가능, HTTP 문 조회가 open이면 409 DOOR_NOT_CLOSED. FE 폴링 값으로 성공/실패 결정 금지 | I+M |
| S04 | 닫힌 테스트 칸에 strong shock, 이후 weak shock | 강한 충격은 TAMPER 경고와 칸 번호 표시, 약한 충격은 이벤트만 기록하고 새 TAMPER를 만들지 않음 | I+B |
| S05 | 빈 칸을 force-open → close | FORCED_OPEN 빨간 경보, 보관/수령 성공으로 전환하지 않음 | I |
| S06 | 전용 시험 물품 stored → force-open → take → close | ITEM_MISSING 경보, BE 물품·배정 missing/ended_at, 미사용 코드 revoked. 정상 수령 완료 화면 금지 | I+B |
| S07 | capture put → 촬영 → leftover 시간 경과, 이어서 capture take | CAPTURE_ITEM_LEFT, 칸 번호 없음. 경고 확인 후 다음 폴링에서 재등장하지 않음 | I |
| S08 | 가짜 locker 종료·재시작; MQTT만 끊고 HTTP는 유지하는 경우도 별도 실행 | online/offline 표시와 통신 오류를 구분. HTTP 문 닫힘을 읽을 수 있고 센서만 신뢰 불가면 현재 BE 계약대로 처리 | I+B |
| S09 | 자동 모드 true로 정상 개방, 다음 개방 전에 skip 실행 | 정상 put/take 자동 동작, skip은 다음 개방 한 번만 적용. 센서 파일·DB 배정 상태 일치 | I+B |

409 센서 판정은 `locker online`과 **이번 개방 이후 서버가 저장한 칸 상태**가 갖춰져야 재현된다. 오래된 값·행 없음·offline이면 BE는 물건 상태를 판단 불가로 보고 문 닫힘 기준으로 진행한다. 이것을 FE 게이트 실패나 가짜 Pi가 409를 무시한 것으로 판정하지 않는다.

실제 명령은 `raspberry-pi` 작업 디렉터리에서 실행한다. `$lockerUrl`, `$captureUrl`은 해당 시험 인스턴스의 주소이며, 각 터미널의 `PI_SIM_API_KEY`에 해당 역할의 키를 설정한다.

```powershell
python tools/sim.py --url $lockerUrl state
python tools/sim.py --url $lockerUrl locker 1 put 150
python tools/sim.py --url $lockerUrl locker 1 take
python tools/sim.py --url $lockerUrl locker 1 close
python tools/sim.py --url $lockerUrl locker 2 shock
python tools/sim.py --url $lockerUrl locker 2 force-open
python tools/sim.py --url $lockerUrl locker 1 skip
python tools/sim.py --url $captureUrl capture put 200
python tools/sim.py --url $captureUrl capture take
```

명령 예시의 1·2는 고정 배정 요구가 아니다. 실제 API가 배정한 테스트 칸 번호로 바꿔 실행한다. `force-open/take`는 S06처럼 서버 업무 상태를 바꾸므로 전용 테스트 물품에만 사용한다.

## 등록과 보관의 예외

| ID | 상황과 조작 | 기대 결과 | 방식 |
| --- | --- | --- | --- |
| R01 | 카테고리 미선택·기타 공백·정상 입력, categories 조회 실패/빈 배열 | 유효성 안내, 기존 categoriesError 표시, 임의 category UUID 생성 금지 | M |
| R02 | 등록 생성 지연·연속 클릭·응답 유실 | 생성 1회, 결과 불명일 때 자동 재등록 금지, Home이 서버 취소로 동작하지 않음 | M+B |
| R03 | 1차/2차 촬영을 각각 실패시킨 뒤 복귀·재시도 | 같은 registration 유지, 오류가 성공 사진으로 처리되지 않음, 실패한 단계 재시도 | I+M |
| R04 | 1차 사진 전에 2차 API 호출, 1장만으로 분석·개방 호출 | 서버 순서/분석 조건 거절, 화면에서 후속 단계 자동 진행 없음 | M+B |
| R05 | 사진 미리보기 URL 실패·만료·이미지 깨짐 | 잘못된 사진으로 오인하지 않게 표시, 화면 오류 없음. 등록 미리보기와 소유 확인 필수 사진의 정책을 구분 | M |
| R06 | UI 재촬영 후 같은 shot 번호 요청 | 현재 서버는 기존 성공 사진을 반환한다는 계약 확인. 새 촬영이 실제 일어났다고 보고하지 않음. 사용성 차이는 별도 결함/후속 계약으로 기록 | I+B |
| R07 | 분석 running/지연/재시도/실패/timeout, 즉시 성공과 캐시 성공 | 단계·경과 표시, POST 반복 없이 GET 대기, 실패 시 개방 없음. 재시도는 같은 등록 기준 | I+M+B |
| R08 | 시험용 두 칸 모두 점유 후 세 번째 투입, 배정 충돌 | NO_AVAILABLE_LOCKER/ALLOCATION_CONFLICT 안내, 임의 칸 표시·명령 없음 | I+M+B |
| R09 | 투입 개방의 명시적 실패·타임아웃·응답 유실 | 성공 화면 금지, 기존 배정과 blocked/unknown 상태 보존. 자동 재개방으로 실제 동작을 반복하지 않음 | I+M+B |
| R10 | deposit complete/reopen을 각각 지연·실패·연속 클릭 | 요청 중 중복 방지, 실패 시 성공 화면·배정 해제 없음, 동일 등록·칸으로 재시도 | M+B |

## 웹 검색과 소유 확인의 예외

| ID | 상황과 조작 | 기대 결과 | 방식 |
| --- | --- | --- | --- |
| W01 | 390px 휴대폰 화면에서 위치 조회, 좌표 없는 키오스크, 위치 권한 거절·지도 로딩 실패 | 목록/안내 사용 가능, 가로 잘림 없음, 현장 방문 안내만 제공 | I+M |
| W02 | 설명 빈값·공백·길이 제한, 선택지 미선택 | 유효한 입력 전 전송 방지, busy 중 중복 제출 없음 | M |
| W03 | 검색 pending → 질문/사진 확인, 지연 중 AI 상태 조회 | 같은 attempt GET으로 대기, 새 검색 POST 반복 없음, configured를 건강 상태로 단정하지 않음 | I+M |
| W04 | no_candidates, 단서 부족, search_limit, completed/not_confirmed를 각각 반환 | 결과 없음·정보 부족·제한·소유 확인 실패를 구분, 네트워크 오류로 오인하지 않음 | M+B |
| W05 | 질문 응답 뒤 이전 question/option 재제출, 잘못된 option | 현재 질문 충돌이면 GET으로 재조회, 이전 질문 답변을 임의로 다음 질문에 제출하지 않음 | M+B |
| W06 | 질문 오답·후보 여러 개 잔존·사진 아니에요 | 계약에 따른 not_confirmed, OTP 발급·수령 허용 없음 | I+M+B |
| W07 | 사진 GET 404·토큰 없음·깨진 JPEG → 다시 불러오기 | 이미지 onLoad와 토큰 확보 전 승인 잠금, 복구 후에만 승인 가능 | M+B |
| W08 | 승인 버튼 연타·승인 응답 유실·동일 시도 재발급 | 코드 중복 발급/자동 재전송 없음, 이미 발급됨 또는 재검색 안내. 평문 OTP 재조회가 가능한 것으로 가정하지 않음 | M+B |
| W09 | attempt 만료, evidence 변경, 물품 수령/분실로 더 이상 사용 불가 | 오류 안내, 성공/OTP 화면 금지, 새 검색으로 복구 가능 | M+B |
| W10 | 검색 진행 중 다시 시작·웹 내부 뒤로가기, 이전 응답 늦게 도착 | 이전 요청 중단/무시, 새 검색/다른 화면을 덮어쓰지 않음 | M |
| W11 | AI timeout·rate limit·인증 오류·응답 형식 오류 | 요청 실패를 본인확인 불합격으로 표시하지 않음, 실제 오류 코드와 재시도/재검색 안내 | M+B |
| W12 | OTP 발급 후 웹 페이지 새로고침·재접속 | 저장소에 OTP를 남기거나 동일 코드 재조회가 가능한 것처럼 표시하지 않음. 기존 코드/서버 업무가 취소되지는 않음 | I+M |

## OTP와 장치 통신 예외

| ID | 상황과 조작 | 기대 결과 | 방식 |
| --- | --- | --- | --- |
| E01 | 8자리 미만·붙여넣기·소문자·공백·미등록 코드 | 입력 정규화/길이 검증, 미등록은 RETURN_CODE_NOT_FOUND, Pi 개방 0회 | I+M+B |
| E02 | A의 정상 코드를 B에서 입력 | RETURN_CODE_WRONG_KIOSK, B 설정 유지, A/B 어디에도 개방 명령 없음 | I |
| E03 | 만료/사용 완료/폐기/잠김 코드, 아직 stored가 아닌 물품 | 해당 오류별 안내, 장치 동작 및 성공 화면 없음 | M+B |
| E04 | 동일 코드로 두 요청이 겹치거나 같은 개방을 다시 요청 | 진행 중/멱등성 계약 준수, 새 물품이나 다른 칸 개방 없음 | M+B |
| E05 | Pi 주소 끊김·잘못된 역할·인증 키·지연·장치 오류 | BE의 502/503 코드로 표시, 실패를 가짜 성공으로 바꾸지 않음. 결과 불명은 기존 코드·명령 상태로 확인하며 새 업무로 초기화하지 않음 | I+M+B |
| E06 | 재개방 실패 후 재시도 | OTP 재발급 없이 같은 물품·칸 유지, 오류가 사라질 때만 후속 완료 진행 | I+M |

## URL 분리와 키오스크 고정

| ID | 상황과 조작 | 기대 결과 | 방식 |
| --- | --- | --- | --- |
| G01 | `/`, `/web`, `/kiosk`, `/kiosk/settings`, `/admin/devices` 직접 접속·새로고침 | 루트는 web, 나머지는 해당 화면. `/docs`, `/health`, `/ready`, `/openapi.json`, `/api/*`는 백엔드 응답 | I+M |
| G02 | 웹·키오스크의 메인/하위 화면·OTP 발급 화면 확인 | 상단 모드 토글·웹의 키오스크 이동·일반 화면 관리 링크 없음. 변경/초기화는 settings에만 존재 | I+M |
| G03 | 저장값 없는 첫 방문, 목록이 한 건인 경우 | 명시적 선택·확정 전 등록/수령 불가. 자동 첫 항목 선택 없음 | M |
| G04 | 저장한 B가 목록 두 번째 페이지에 있음, 새로고침·재접속 | 전체 목록 검증 후 B 복원 | M |
| G05 | 목록 오류·중간 페이지 실패·삭제된 ID·잘못된 UUID | 통신 실패는 설정 보존/재시도, 유효하지 않은 설정은 명시적 재선택. 자동 다른 키오스크 변경 금지 | M |
| G06 | localStorage 읽기/쓰기/삭제 실패, unrelated 키와 함께 초기화 | 메모리 선택 지원·저장 실패 안내, 삭제 실패를 성공으로 표시하지 않음, 다른 키 유지 | M |
| G07 | 입력·촬영·분석·개방·409·오류·Home 후 미완료 각 상태에서 내부 이동·뒤로/앞으로가기 | 현재 업무와 키오스크 고정. 설정 화면으로 이탈하지 않음 | M |
| G08 | 다른 탭에서 설정을 B로 변경하는 동안 A 업무 진행 | 현재 업무는 A 유지, 완료/유휴 상태에서만 재검증·적용 | M |
| G09 | 업무 중 새로고침·주소창 이동·탭 닫기에서 취소/이탈 각각 선택 | 브라우저 beforeunload 확인, 취소하면 유지. 이탈을 선택하면 진행 화면 복원이 보장되지 않으며 서버 취소/완료 요청은 없음 | M |
| G10 | 완료 후 settings에서 변경/초기화, 업무 화면 복귀 | 정상 변경 가능, 새 업무는 새 키오스크 사용. 이전 OTP·사진·물품 ID를 재사용하지 않음 | I+M |

현재 강제 새로고침 후에는 키오스크 선택만 복원한다. 미완료 서버 업무 복구는 자동 구현된 것으로 판정하지 않으며 시험 중 남은 ID로 상태를 확인한다. 주소창 이동을 브라우저 제한을 넘어 완전히 차단한다는 합격 기준을 두지 않는다.

## 공통 오류와 계약 전수 확인

| ID | 상황과 조작 | 기대 결과 | 방식 |
| --- | --- | --- | --- |
| C01 | JSON 오류 객체, FastAPI 422 배열, detail 문자열, HTML 502/504, 빈 body·깨진 JSON | 파싱 오류가 원래 요청 오류를 덮지 않음. 화면 유지·오류 안내, 성공 전환 없음 | M+B |
| C02 | 아래 오류 목록을 해당 단계에 하나씩 주입 | 매개변수별 결과 기록, 오류 코드 확인, 후속 촬영/개방/완료가 자동 실행되지 않음 | M+B |
| C03 | GET만 잠시 끊김, POST 전송 전 실패, 서버 처리 후 응답 유실을 분리 | 읽기 복구와 업무 중복 생성을 구분. 일반 retry로 POST를 무조건 반복하지 않음 | M+B |
| C04 | 두 브라우저의 동시 배정·동일 완료, MQTT 사고와 진행 요청의 경합 | 한 칸 중복 배정 없음, 완료 기록 멱등, missing을 정상 수령 성공으로 되돌리지 않음 | B |
| C05 | 최종 lint·단위·브라우저·build, 실행한 백엔드/Pi 테스트 결과 집계 | 실패/skip/환경 차단을 따로 기록. SQLite만 통과한 동시성 시험을 PostgreSQL 통과로 기록하지 않음 | M+B |

C02는 [기능 명세 9장](../backend/docs/functional-spec.md)의 다음 코드들을 각각 실행한다. 개별 코드별 상세 화면 복구가 구현되어 있지 않다면 일반 안내만 보인다는 사실과 막힌 복구 경로를 결함으로 기록한다. 같은 HTTP 상태라는 이유로 한 코드만 검사하지 않는다.

| 단계 | 매개변수화할 코드 |
| --- | --- |
| 공통·목록 | BUSINESS_API_DISABLED, KIOSK_NOT_FOUND, CATEGORY_NOT_FOUND, REGISTRATION_NOT_FOUND, IMAGE_NOT_FOUND, ITEM_NOT_FOUND |
| 등록 순서·배정 | INVALID_REGISTRATION_STATE, SHOT_OUT_OF_ORDER, TWO_IMAGES_REQUIRED, ANALYSIS_REQUIRED, NO_AVAILABLE_LOCKER, ALLOCATION_CONFLICT, DEPOSIT_NOT_OPENED |
| 완료·수령 | DOOR_NOT_CLOSED, ITEM_NOT_DETECTED, ITEM_STILL_INSIDE, ITEM_NOT_STORED, PICKUP_NOT_OPENED |
| 반환 코드 | RETURN_CODE_NOT_FOUND, RETURN_CODE_EXPIRED, RETURN_CODE_ALREADY_USED, RETURN_CODE_UNUSABLE, RETURN_CODE_IN_PROGRESS, RETURN_CODE_WRONG_KIOSK, RETURN_CODE_CONFLICT, RETURN_CODE_NOT_CONFIGURED |
| 소유 확인 | OWNERSHIP_REQUIRED, OWNERSHIP_ATTEMPT_NOT_FOUND, OWNERSHIP_QUESTION_NOT_CURRENT, OWNERSHIP_OPTION_NOT_FOUND, OWNERSHIP_ATTEMPT_EXPIRED, OWNERSHIP_PHOTO_UNAVAILABLE, OWNERSHIP_PHOTO_NOT_PENDING, OWNERSHIP_PHOTO_LOAD_REQUIRED, OWNERSHIP_ATTEMPT_RESTART_REQUIRED, OWNERSHIP_EVIDENCE_CHANGED, OWNERSHIP_ITEM_NO_LONGER_AVAILABLE, OWNERSHIP_RETURN_CODE_ALREADY_ISSUED |
| Pi 설정·HTTP | DEVICE_NOT_CONFIGURED, CAPTURE_NOT_CONFIGURED, DEVICE_UNREACHABLE, INVALID_DEVICE_RESPONSE, DEVICE_ROLE_MISMATCH, DEVICE_RESPONSE_TOO_LARGE |
| Pi 명령 | COMMAND_EXPIRED, INVALID_COMMAND, UNAUTHORIZED, LOCKER_NOT_FOUND, DEVICE_BUSY, CAMERA_ERROR, LOCK_ERROR |
| 저장·사진·분석 형식 | IMAGE_STORAGE_NOT_CONFIGURED, IMAGE_STORAGE_FAILED, INVALID_IMAGE, INVALID_ANALYSIS_RESULT, INVALID_MODEL_OUTPUT, INSUFFICIENT_VISUAL_FEATURES, IMAGE_TOO_LARGE, IMAGE_LOW_QUALITY, INVALID_CATEGORY, INVALID_ITEM_ID, INVALID_IMAGE_COUNT, DUPLICATE_IMAGE_ID |
| AI 설정·통신 | AI_NOT_CONFIGURED, OWNERSHIP_AI_NOT_CONFIGURED, OWNERSHIP_AI_TIMEOUT, OWNERSHIP_AI_UNREACHABLE, OWNERSHIP_AI_REQUEST_FAILED, OWNERSHIP_SEARCH_FAILED, OWNERSHIP_AI_RATE_LIMITED, OWNERSHIP_AI_AUTHENTICATION_FAILED, OWNERSHIP_AI_MODEL_UNAVAILABLE |
| AI 응답 | OWNERSHIP_AI_RESPONSE_TOO_LARGE, OWNERSHIP_AI_INVALID_RESPONSE, OWNERSHIP_AI_INVALID_OPTIONS, OWNERSHIP_AI_INVALID_MATCHES, OWNERSHIP_UNGROUNDED_CLUE |
| 배경 분석 응답 | ANALYSIS_TIMED_OUT, ANALYSIS_FAILED 및 analysis_error_code로 온 외부 연동 오류. HTTP 200 등록 조회의 실패 상태로도 검사 |
| 입력·미지 오류 | 표준 422 검증 배열, INVALID_SHOT_ORDER, 알려지지 않은 code, 오류 code 없는 500/502/504 |

실제 EC2에서 AI 키·S3 권한·브로커 전체를 고장 내 C02를 만들지 않는다. 공급자 장애는 격리된 모의 응답/테스트 대역에서 계약을 검증하고, I에서는 정상 호출과 전용 가짜 Pi의 제어 가능한 장애를 검증한다.

## 서버가 어떻게 판단하는지 확인할 항목

| 시점 | 서버 확인 |
| --- | --- |
| 촬영 | 외부 호출 전 device_commands 생성, 성공 JPEG 검증·S3 저장·item_images 연결. command_id와 registration_id 혼동 금지 |
| 분석 | 동일 등록의 실제 두 이미지로 분석, analysis_status/진행 상태/8개 특징 기록. 분석 성공 전에 개방 금지 |
| 투입 개방 | 빈 칸 배정·개방 명령·awaiting_deposit. 실패하면 배정을 임의 해제하지 않음 |
| 보관 완료 | HTTP 문 closed 확인 → 신뢰할 수 있는 item이 empty면 409 → 통과 시 stored |
| 소유 확인 | 저장된 후보 검색·질문 → 사진 GET 토큰 → 승인과 반환 코드 발급. 코드 평문 재조회 API 없음 |
| 수령 개방 | 코드·만료·키오스크·물품 상태 검사 → 같은 칸 개방 → awaiting_pickup |
| 수령 완료 | HTTP 문 closed 확인 → 신뢰할 수 있는 item이 present면 409 → 통과 시 retrieved/ended_at |
| 경고 | MQTT → DB 저장 → FE GET. 일반 경고로 보관/수령을 성공시키지 않음 |
| ITEM_MISSING | 활성 stored 물품·배정을 missing으로 종료하고 미사용 코드를 폐기. 정상 수령 성공과 별개 |

DB 조회는 이번 실행의 registration_id/found_item_id/kiosk_id로 범위를 제한한다. 상태 전이는 API 응답·DB·가짜 Pi 명령 수를 함께 대조한다. OTP는 해시 저장 여부만 확인하고 평문을 로그에 남기지 않는다.

## 실행 순서와 완료 기준

1. **준비**: 전용 키오스크·사진·컨트롤러·EC2 경로·MQTT·빈 칸 확인. 현재 상태를 기준선으로 기록한다.
2. **화면 수정 인수**: P/D/A의 모의 시험을 먼저 실행한다. 경고 200건 이상·지연·중복·날짜는 자동화해 재현한다.
3. **실제 정상 흐름**: F01~F08 한 물건을 끊김 없이 진행하고 F09로 칸 재사용을 확인한다.
4. **실제 센서 예외**: S01~S09, 잘못된 키오스크/문 열림/재개방 실패를 화면에서 진행한다. ITEM_MISSING은 별도 시험 물품으로 마지막에 실행한다.
5. **나머지 예외**: R/W/E/C와 G를 실행한다. 검색·사진 승인·OTP 발급이 포함된 브라우저 시험을 보강한다.
6. **마무리**: lint/test/test:browser/build와 필요한 BE/Pi 회귀 시험. 새 결함을 고쳤다면 관련 시나리오부터 재실행한다. 시험 키오스크 매핑·프로세스·남은 배정·센서 파일의 유지/복구 상태를 기록한다.

이전 수행 결과는 FE 단위 34개, 브라우저 20개, lint/build 통과다. 경고 helper와 일부 수령·등록·설정 회귀를 다뤘지만, 브라우저 fixture의 등록 GET은 이미 `ready/succeeded`를 반환한다. 따라서 그 시험만으로 실제 분석 시작·S3·Gemini·웹 문답·사진 승인·OTP 발급의 전체 연결을 검증했다고 볼 수 없다. 이번 F/R/W 시험으로 그 빈 구간을 채운다. BE/Pi 시험은 이번 실행에서 별도로 결과를 수집한다.

모든 필수 시나리오에 결과가 있어야 종료한다. M/B만 통과한 항목에는 그 방식을 남기며 I 통과로 승격하지 않는다. 실제 사진이 없거나 EC2 설정을 적용하지 못한 경우에는 해당 I 항목을 환경 차단으로 기록한다.

결과 기록 형식:

| 시나리오와 변형 | 방식 | 사전 상태 | 화면 조작·센서 조작 | 실제 요청과 응답 | DB/명령 수 | 결과 | 증거·결함 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 예: P07 첫 완료 | I | A/배정 칸/online/present/closed | 완료 → 다시 열기 → take/close → 완료 | 409 ITEM_STILL_INSIDE → reopen 200 → complete 200 | 같은 물품 retrieved, ended_at | 미실행 | 실행 시 작성 |

## 근거 코드와 계약

- [프론트 App](../frontend/src/App.jsx), [경고 수신 상태](../frontend/src/kioskAlerts.js), [키오스크 설정](../frontend/src/kioskSettings.js)
- [백엔드 기능 명세 F12 F14 및 오류 계약](../backend/docs/functional-spec.md), [FE-K01](web-structure.md), [DB 확인 방법](api.md)
- [개발용 백엔드 장치](../backend/app/dev_device.py), [완료 서비스](../backend/app/services.py), [센서·경고 저장 및 조회](../backend/app/device_telemetry.py)
- [Pi 실행과 시뮬레이션](../raspberry-pi/README.md), [Pi 생성·카메라 주입](../raspberry-pi/app/main.py), [센서 조작 CLI](../raspberry-pi/tools/sim.py), [MQTT 규약](mqtt-protocol.md)
- [브라우저 기존 fixture](../frontend/tests/browser/fixture.js), [경고 단위 시험](../frontend/tests/kioskAlerts.test.js), [백엔드 시험](../backend/tests), [Pi 시험](../raspberry-pi/tests)
