# 프론트 요청: 관리자 키오스크 현황 집계 API

- 작성: 2026-11-26
- 대상 화면: 관리자 장치 관리(`/admin/devices`) 첫 화면
- 관련: [F14 운영 중단과 관리자 조치](../backend/docs/functional-spec.md#f14-운영-중단과-관리자-조치), [docs/api.md 장치 상태와 경고](api.md#장치-상태와-경고-mqtt-수신분)

## 왜 필요한가

관리자 화면을 두 단계로 바꿨다. 첫 화면은 모든 키오스크를 한 번에 보여 주고 키오스크마다 미조치 알람·경고 건수와 운영 중단 여부를 표시한다. 카드를 누르면 같은 화면 안에서 그 키오스크의 장치 상태와 경고 조치 화면으로 바뀐다.

지금 백엔드에는 여러 키오스크를 한 번에 집계해 주는 경로가 없다. `GET /api/admin/alerts`는 `kiosk_id`가 필수라 키오스크 수만큼 호출해야 하고, `GET /api/alerts`는 전체를 내려받을 수 있지만 `limit`이 최대 200이라 미조치 경고가 그보다 많으면 건수가 어긋난다.

프론트는 아래 경로를 먼저 호출하고, 404·405·501이면 `GET /api/alerts?unresolved_only=true&limit=200`으로 직접 세는 폴백을 쓴다. 폴백에서 200건을 채우면 화면에 "일부만 집계했습니다"를 띄운다. 이 경로가 생기면 폴백은 자동으로 쓰이지 않는다.

## 요청

| 메서드 | 경로 | 인증 | 출력 |
| --- | --- | --- | --- |
| GET | `/api/admin/kiosks/overview` | 기존 `/api/admin/*`와 같은 Basic 인증 | 등록된 모든 키오스크의 현황 배열 |

응답 예시:

```json
[
  {
    "kiosk_id": "00000000-0000-0000-0000-000000000001",
    "name": "Swagger 테스트 키오스크",
    "location_description": "공학관 1층 로비",
    "blocked": true,
    "alarm_count": 1,
    "warning_count": 2,
    "last_alert_at": "2026-11-25T11:30:00+09:00",
    "devices": {"total": 2, "online": 1, "offline": 1, "unknown": 0}
  }
]
```

| 필드 | 타입 | 집계 규칙 |
| --- | --- | --- |
| `kiosk_id` | uuid | 키오스크 ID |
| `name`·`location_description` | str | `GET /api/kiosks`와 같은 값. `location_description`은 NULL 허용 |
| `blocked` | bool | `GET /api/kiosks/{id}/operation-status`의 `blocked`와 같은 판정. 미조치 alarm이 하나라도 있으면 true |
| `alarm_count` | int | `resolved_at`이 NULL인 `severity=alarm` 건수. 상한을 두지 않는다 |
| `warning_count` | int | `resolved_at`이 NULL인 `severity=warning` 건수 |
| `last_alert_at` | datetime 또는 null | 미조치 경고 중 가장 최근 `reported_at`. 없으면 null |
| `devices` | 객체 또는 생략 | `locker_number`가 NULL인 장치 행(촬영 장치·보관함 컨트롤러)의 `connection` 집계 |

집계 규칙에서 확인해 둘 것:

- 경고가 하나도 없는 키오스크도 `alarm_count=0`으로 배열에 넣는다. 화면에서 그 키오스크가 빠지면 "모든 키오스크 확인"이 아니다.
- `devices.unknown`은 행이 있는데 `connection`이 NULL인 장치다. 아직 아무 메시지도 받지 못해 행 자체가 없으면 `total`에서 빠진다. 프론트는 `devices`가 없으면 장치 수를 표시하지 않는다.
- `alarm_count`·`warning_count`는 `unresolved_only=true` 기준이다. 조치 완료 이력은 상세 뷰에서 `GET /api/admin/alerts`로 따로 본다.
- 키오스크가 많아지면 페이지네이션이 필요하다. 지금은 `GET /api/kiosks`와 같은 `limit`·`offset`을 받아 두면 충분하다.

## 프론트가 이미 맞춰 둔 것

- `blocked`가 없으면 `alarm_count > 0`으로 판정한다. 두 값이 어긋나면 백엔드 값을 먼저 쓴다.
- 배열이 아닌 응답은 `INVALID_OVERVIEW_RESPONSE`로 화면에 오류를 띄우고 이전 값을 유지한다.
- 401·503은 폴백하지 않고 로그인·설정 오류로 안내한다. 폴백은 404·405·501에서만 한다.
- 첫 화면에서는 5초마다, 상세 뷰에서는 들어갈 때와 조치 직후에만 호출한다.

## 확인 방법

1. 미조치 alarm이 있는 키오스크와 하나도 없는 키오스크를 함께 두고 호출한다. 두 키오스크가 모두 배열에 있어야 하고 `blocked`가 서로 달라야 한다.
2. `POST /api/admin/alerts/{id}/resolve`로 alarm을 조치한 뒤 다시 호출해 `alarm_count`가 줄고 `blocked`가 false가 되는지 본다. 같은 키오스크의 `operation-status` 응답과 값이 같아야 한다.
3. 미조치 경고를 200건 넘게 만들고 호출한다. `alarm_count`가 201 이상으로 정확히 나와야 한다. 프론트 폴백이 못 하는 부분이 이것이다.
4. 인증 헤더 없이 호출해 401 `ADMIN_UNAUTHORIZED`인지 확인한다.
