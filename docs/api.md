# 로컬 개발 API

업무 API는 LOCAL_DEVELOPMENT=true에서만 사용한다. 공개 인증은 아직 구현하지 않았다.

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | /api/kiosks | 키오스크 위치 조회, limit/offset 지원 |
| GET | /api/categories | 등록된 카테고리 조회, 기본 목록 없음 |
| POST | /api/registrations | 등록 생성, 201 |
| GET | /api/registrations/{id} | 등록 상태와 입력 조회 |

등록 입력 예시:

```json
{"kiosk_id":"00000000-0000-0000-0000-000000000001","secondary_description":"검은색 지갑"}
```

primary_category_id와 secondary_description은 선택값이다. 카테고리 지정 시 유효한 UUID가 필요하다.
등록 응답에는 id, kiosk_id, primary_category_id, secondary_description, status,
analysis_status, images, locker_number가 있다. 최초 상태는 draft/pending이고 images는 빈 배열,
locker_number는 null이다. 생성 요청은 멱등이 아니므로 응답 ID를 후속 조회에 사용한다.
업무 오류는 {"detail":{"code":"..."}}이며 잘못된 참조는 404, 로컬 모드 비활성화는 503이다.
공개 검색과 반환 기능은 보류한다.

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | /api/registrations/{id}/shots/{1 또는 2} | 순차 JPEG 촬영·S3 저장 |
| GET | /api/registrations/{id}/shots/{1 또는 2}/preview | 임시 서명 URL |
| POST | /api/registrations/{id}/analysis | 두 사진의 특징 추출 연결 |

촬영 성공 재요청은 기존 사진을 반환한다. 순서 위반/진행 중은 409이다.
저장/장치 오류는 502, 설정 누락은 503이다. 촬영 실패는 blocked로 보존하며 자동 재촬영하지 않는다.
AI는 교체 가능한 내부 경계이며 기본 연결은 503 AI_NOT_CONFIGURED이다.
분석 성공은 입력 사진 ID·순서와 일치하는 결과에서만 ready가 된다.
테스트용 AI를 실제 장치에 연결하는 실행 옵션은 제공하지 않는다.
