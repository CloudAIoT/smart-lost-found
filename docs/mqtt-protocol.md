# 장치 → 백엔드 MQTT 규약

- 작성: 라즈베리파이 파트, 2026-11-19
- 상태: Pi 쪽 발행은 구현했다. 브로커와 백엔드 구독은 백엔드 담당과 정한다
- 관련: [결정 기록 0003·0004](../raspberry-pi/docs/decisions/), HTTP 명령은 [통신 규약](device-protocol.md)

Pi가 가상 센서로 본 상태와 이상 상황을 MQTT로 알리는 방법이다. 센서는 실물 대신 가상으로 두고, 시연용 명령으로 상황을 만든다([결정 기록 0004](../raspberry-pi/docs/decisions/0004-virtual-sensors.md)).

## 1. HTTP와 나누는 기준

- 촬영, 보관함 개방, 문 상태 조회는 지금처럼 HTTP다. 결과를 바로 받아야 하고 연동 시험을 마친 흐름이다.
- 장치에서 생기는 변화(센서 상태, 이벤트, 경고, 장치 연결 상태)는 MQTT로 보낸다. 백엔드가 묻지 않아도 Pi가 먼저 알린다.
- 센서 값만으로 보관·수령을 완료하지 않는다(D03·D06). MQTT로 받은 내용은 경고와 화면 표시에 쓴다.

## 2. 브로커

- Mosquitto 2.x를 EC2에 두고 Tailscale 주소에서만 접속을 받는다. 포트는 1883이다.
- 익명 접속을 막고, 장치마다 계정과 주제 권한을 준다. Pi는 자기 키오스크 아래에 보내기만, 백엔드는 받기만 한다.
- 메시지는 모두 QoS 1로 보낸다. 상태(`state`, `status`)는 보존 메시지라서, 백엔드가 나중에 구독해도 지금 상태를 바로 받는다.

설정 예시다. 실제 값은 백엔드 담당이 배포 설정에 맞춰 정한다.

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

## 5. 시연

Pi를 `PI_SIMULATION=true`로 띄우면 시연용 경로(`/sim`)가 열린다. 백엔드용 HTTP 규약과는 따로이며 같은 API 키로 보호한다. 쓰는 방법은 [라즈베리파이 README](../raspberry-pi/README.md#가상-센서와-mqtt)에 있다.

| 경로 | Pi | 하는 일 |
| --- | --- | --- |
| `GET /sim/state` | 두 Pi | 지금 센서 값 |
| `POST /sim/lockers/{n}/item` | 보관함 Pi | 물건 넣기·꺼내기 `{"action": "put", "weight_g": 150}`, `{"action": "take"}` |
| `POST /sim/lockers/{n}/door` | 보관함 Pi | 문 열기(명령 없이 = 강제 개방)·닫기 `{"action": "open"}` |
| `POST /sim/lockers/{n}/shock` | 보관함 Pi | 충격 `{"strength": "strong"}` |
| `POST /sim/capture/item` | 촬영 Pi | 촬영 상자에 물건 넣기·꺼내기 |

닫힌 칸에는 물건을 넣거나 뺄 수 없다(409 `DOOR_CLOSED`). 시연 준비로 물건이 있는 칸을 만들 때만 `"preset": true`를 붙인다.
