# 장치 → 백엔드 MQTT 규약

- 작성: 라즈베리파이 파트, 2026-11-19
- 상태: Pi 발행, EC2 브로커, 백엔드 구독·저장·조회 API가 모두 구현되어 있다. 브로커 구축 절차는 [EC2 배포](../deploy/README.md#장치-상태-mqtt-브로커)
- 관련: [결정 기록 0003·0004](../raspberry-pi/docs/decisions/), HTTP 명령은 [통신 규약](device-protocol.md)

Pi가 가상 센서로 본 상태와 이상 상황을 MQTT로 알리는 방법이다. 센서는 실물 대신 가상으로 두고, 시연용 명령으로 상황을 만든다([결정 기록 0004](../raspberry-pi/docs/decisions/0004-virtual-sensors.md)).

## 1. HTTP와 나누는 기준

- 촬영, 보관함 개방, 문 상태 조회는 지금처럼 HTTP다. 결과를 바로 받아야 하고 연동 시험을 마친 흐름이다.
- 장치에서 생기는 변화(센서 상태, 이벤트, 경고, 장치 연결 상태)는 MQTT로 보낸다. 백엔드가 묻지 않아도 Pi가 먼저 알린다.
- 센서 값만으로 보관·수령을 완료하지 않는다(D03·D06). 센서는 완료를 **막을 수만 있다**: 사용자가 선언한 완료와 칸 상태가 어긋나면(보관인데 `empty`, 수령인데 `present`) 거절한다. 단, 강제 개방 뒤 보관 물품이 사라진 `ITEM_MISSING`은 사용자의 완료 선언이 존재할 수 없는 사고이므로, 백엔드가 그 물품과 배정을 `missing`으로 기록한다.

## 2. 브로커

- Mosquitto 2.x를 EC2에 두고 Tailscale 주소에서만 접속을 받는다. 포트는 1883이다. 백엔드는 Compose 내부 네트워크의 `mqtt:1883`으로 붙고, 공개된 포트는 Pi만 쓴다.
- 익명 접속을 막고, 장치마다 계정과 주제 권한을 준다. Pi는 자기 키오스크 아래에 보내기만, 백엔드는 받기만 한다.
- 메시지는 모두 QoS 1로 보낸다. 상태(`state`, `status`)는 보존 메시지라서, 백엔드가 나중에 구독해도 지금 상태를 바로 받는다.

아래가 실제로 쓰는 설정이다. 저장소 사본은 [deploy/mosquitto/](../deploy/mosquitto/)에 있고 서버에는 ssh로 넣는다. `passwords`는 비밀값이라 저장소에 없다.

```
# mosquitto.conf
listener 1883
allow_anonymous false
password_file /mosquitto/config/passwords
acl_file /mosquitto/config/acl
persistence true

# acl
user capture-pi
topic write slf/+/capture/#
topic write slf/+/alerts
user locker-pi
topic write slf/+/locker/#
topic write slf/+/lockers/#
topic write slf/+/alerts
user backend
topic read slf/#
```

## 3. 토픽

모든 토픽은 `slf/{키오스크 ID}/` 아래에 있다. 키오스크 ID는 백엔드 `kiosks.id`와 같고, 개발용 시드 키오스크는 `00000000-0000-0000-0000-000000000001`이다.

| 토픽 | 보내는 Pi | 보존 | 내용 |
| --- | --- | --- | --- |
| `capture/status`, `locker/status` | 촬영 Pi, 보관함 Pi | 예 | 장치 연결 상태. `offline`은 Pi가 갑자기 끊기면 브로커가 대신 보낸다(유언 메시지) |
| `lockers/{칸 번호}/state` | 보관함 Pi | 예 | 칸의 문, 무게, 물건 유무 |
| `lockers/{칸 번호}/events` | 보관함 Pi | 아니오 | 문 열림·닫힘, 충격 |
| `capture/state` | 촬영 Pi | 예 | 촬영 상자의 무게, 물건 유무 |
| `capture/events` | 촬영 Pi | 아니오 | 촬영함 |
| `alerts` | 두 Pi | 아니오 | 이상 상황 경고(4절) |

내용은 모두 JSON이고 `at`(보낸 시각, 시간대 포함)이 붙는다.

```jsonc
// slf/{키오스크 ID}/locker/status
{"status": "online", "at": "2026-11-19T19:24:26+09:00"}

// slf/{키오스크 ID}/lockers/1/state
{"locker_id": 1, "door": "closed", "weight_g": 150.0, "item": "present", "at": "2026-11-19T19:24:27+09:00"}

// slf/{키오스크 ID}/lockers/1/events
{"type": "door_opened", "locker_id": 1, "authorized": true, "weight_g": 0.0, "at": "..."}
{"type": "door_closed", "locker_id": 1, "authorized": true, "before_g": 0.0, "after_g": 150.0, "at": "..."}
{"type": "shock", "locker_id": 1, "strength": "strong", "at": "..."}

// slf/{키오스크 ID}/capture/events
{"type": "captured", "registration_id": "...", "weight_g": 200.0, "item": "present", "at": "..."}
```

- `door`: `open` 또는 `closed`
- `item`: 무게가 10g보다 무거우면 `present`, 아니면 `empty`
- `authorized`: 개방 명령으로 열렸으면 `true`, 명령 없이 열렸으면 `false`
- `door_closed`의 `before_g`, `after_g`: 문이 열릴 때와 닫히고 1초 뒤의 무게

## 4. 경고

```jsonc
// slf/{키오스크 ID}/alerts
{"code": "ITEM_NOT_DEPOSITED", "severity": "warning", "device": "locker", "locker_id": 2,
 "message": "2번 보관함에 물건이 감지되지 않았습니다. 물건을 넣지 않고 문을 닫았을 수 있습니다.",
 "detail": {"before_g": 0.0, "after_g": 0.0}, "at": "2026-11-19T19:24:29+09:00"}
```

| `code` | `severity` | `device` | 언제 | `detail` |
| --- | --- | --- | --- | --- |
| `FORCED_OPEN` | `alarm` | `locker` | 개방 명령 없이 문이 열렸다 | 없음 |
| `ITEM_MISSING` | `alarm` | `locker` | 강제로 열린 칸이 닫힐 때 물건이 사라졌다 | `before_g`, `after_g` |
| `TAMPER` | `warning` | `locker` | 닫힌 칸에 강한 충격이 왔다 | 없음 |
| `ITEM_NOT_DEPOSITED` | `warning` | `locker` | 빈 칸을 개방 명령으로 열었다 닫았는데 그대로 비어 있다 | `before_g`, `after_g` |
| `ITEM_NOT_TAKEN` | `warning` | `locker` | 물건이 있던 칸을 개방 명령으로 열었다 닫았는데 그대로 있다 | `before_g`, `after_g` |
| `CAPTURE_ITEM_LEFT` | `warning` | `capture` | 마지막 촬영 뒤 정해진 시간(기본 120초)이 지나도 촬영 상자에 물건이 있다 | `registration_id`, `weight_g`, `seconds_after_capture` |

- `message`는 사람이 읽을 문장이다. 화면 문구는 `code`를 보고 따로 정해도 된다.
- 보관과 수령은 Pi가 문이 열릴 때의 무게로 구분한다. 빈 칸을 열었으면 보관, 물건이 있는 칸을 열었으면 수령으로 본다. 백엔드는 배정 기록(투입 대기, 수령 대기)과 맞춰 한 번 더 확인할 수 있다.
- 규약에 없는 `code`도 백엔드가 저장하고 화면에 내보낸다. Pi에 새 상황이 생겨도 조용히 사라지지 않는다.

## 4-1. 백엔드가 받는 방식

- 구독 주제는 `slf/+/alerts`, `slf/+/+/status`, `slf/+/lockers/+/state`, `slf/+/capture/state`다. `events`는 지금 구독하지 않는다.
- 상태(`state`, `status`)는 대상마다 한 행으로 덮어쓴다. 보존 메시지라서 백엔드가 다시 붙으면 현재 값이 바로 다시 온다.
- 경고는 행으로 쌓는다. 경고는 보존되지 않으므로, 백엔드는 고정 client id와 유지 세션으로 붙어 꺼져 있던 동안의 QoS 1 경고를 브로커 큐에서 받는다. 재전달된 같은 경고는 `(키오스크, code, 칸, at)`가 같아 한 번만 저장된다.
- 완료 판정은 사용자 확인과 HTTP 문 조회가 그대로 담당하고(D03·D06), 거기에 칸 상태 한 가지를 더 본다. 보관 완료는 `item=empty`면 409 `ITEM_NOT_DETECTED`, 수령 완료는 `item=present`면 409 `ITEM_STILL_INSIDE`로 거절한다. 문이 닫히는 순간 발행되는 보존 상태 메시지는 1초 뒤의 `ITEM_NOT_*` 경고보다 먼저 도착하므로, 완료 요청 시점에 state 행만 봐도 판단할 수 있다. 믿을 수 있는 값일 때만 본다. 그 칸의 값이 이번 개방 이후에 수신됐고, 같은 키오스크의 locker 장치 행(`locker_number` 0)이 `online`이어야 한다. 그 밖은 모두 판단 불가로 보고 예전처럼 문 닫힘만으로 완료한다.
- `ITEM_MISSING` 경고를 처음 저장할 때는 같은 트랜잭션에서 그 칸의 활성 `stored` 배정을 찾는다. 있으면 물품·배정을 `missing`으로 바꾸고 `ended_at`을 기록해 칸을 해제하며, 아직 쓰지 않은 `active/opening` 반환 코드를 `revoked`로 바꾼다. 등록(`registration`)은 이력으로 `stored`를 유지한다. 활성 보관 물품이 없으면 경고만 저장한다. 같은 경고의 QoS 재전송은 경고 고유키에서 전체 트랜잭션이 롤백되어 사고 전이를 반복하지 않는다.
- FE가 읽는 경로는 `GET /api/kiosks/{kiosk_id}/device-status`와 `GET /api/alerts`다. 형식은 [기능 명세 8-1](../backend/docs/functional-spec.md)에 있다.

## 5. 시연

Pi를 `PI_SIMULATION=true`로 띄우면 시연용 경로(`/sim`)가 열린다. 백엔드용 HTTP 규약과는 따로이며 같은 API 키로 보호한다. 쓰는 방법은 [라즈베리파이 README](../raspberry-pi/README.md#가상-센서와-mqtt)에 있다.

| 경로 | Pi | 하는 일 |
| --- | --- | --- |
| `GET /sim/state` | 두 Pi | 지금 센서 값 |
| `POST /sim/lockers/{n}/item` | 보관함 Pi | 물건 넣기·꺼내기 `{"action": "put", "weight_g": 150}`, `{"action": "take"}` |
| `POST /sim/lockers/{n}/door` | 보관함 Pi | 문 열기(명령 없이 = 강제 개방)·닫기 `{"action": "open"}` |
| `POST /sim/lockers/{n}/shock` | 보관함 Pi | 충격 `{"strength": "strong"}` |
| `POST /sim/lockers/{n}/skip-auto` | 보관함 Pi | 자동 모드에서 다음 개방 한 번은 무게를 바꾸지 않기(보관 안 됨·수령 안 됨 시연) |
| `POST /sim/capture/item` | 촬영 Pi | 촬영 상자에 물건 넣기·꺼내기 |

닫힌 칸에는 물건을 넣거나 뺄 수 없다(409 `DOOR_CLOSED`). 시연 준비로 물건이 있는 칸을 만들 때만 `"preset": true`를 붙인다.
