# 백엔드 설계 명세

개정: 2026-11-16 · 코드 확인 기준 `7c001a8`.

사진 2장 촬영·S3 저장 이후 Gemini 분석과 DB 저장, 인덱스 후보 검색, development_v3 질문을 연결했다. 실제 키를 사용하는 인식 품질·대기 시간 검증은 남아 있다.

2026-11-17 공유 시점에 질문 검증은 담당자 후속 변경의 확인·통합 대상으로 남겨 둔다. 웹 질문·반환 코드 연결을 추가한 원격 브랜치와 이 브랜치의 차이는 [구현 현황](implementation-status.md)의 최신 기록을 따른다.

| 문서 | 역할 |
| --- | --- |
| [기능 명세](functional-spec.md) | 분석 API, 성공·실패, 유사 검색, 5지선다 질문과 판정 |
| [ERD](erd.md) | 실제 10개 테이블, 새 metadata/evidence/search_features 컬럼, JSON 저장·호환 |
| [구현 계획](implementation-plan-20261116.md) | 단계별 변경 파일·키 공란 설정·패키징·오프라인/실호출 검증 |
| [구현 현황](implementation-status.md) | 현재 코드와 다음 목표의 구분, 과거 검증 기록 |
| [연동 계약 기록](integration-agreements.md) | 이미지 모듈 인수인계와 소유 확인 경계 |
| [결정 기록](spec-review.md) | 과거 결정과 이번 사용자 요청의 범위 |
| [팀 API](../../docs/api.md) | 현재 외부 요청·응답 |

## 현재 구현

- 순차 두 사진·S3 저장 이후 분석 POST로 prepare_item → Gemini 한 번 → parse_item_output → 특징·메타데이터·검색 색인을 함께 저장한다.
- 검색은 정규화 특징의 JSONB GIN 인덱스를 사용한다. 50개 초과면 refine_search, 상세 단서의 의미 비교는 후보 안에서만 수행한다.
- 질문은 별도 검토 근거와 analysis_id를 확인하는 development_v3다. 새로운 AI 설정 중 GEMINI_API_KEY만 공란이다.
- 실제 키·운영 DB·SSM·배포 변경은 수행하지 않았다. 최신 테스트 기록은 구현 현황을 따른다.

## 근거

| ID | 원본 | 해석 |
| --- | --- | --- |
| S1 | [웹 구조](../../docs/web-structure.md) | 초기 UI 요구. 최신 개발용 API와 구분 |
| S2 | [장치 규약](../../docs/device-protocol.md) | Pi 촬영·개방·문 상태 |
| S3·S4 | [Pi 개요](../../raspberry-pi/README.md), [단일 카메라 결정](../../raspberry-pi/docs/decisions/0001-single-camera-two-shots.md) | 단일 카메라로 순차 2장 |
| S5 | [소유 확인 워크플로](../app/ai_ownership_verification/workflow.md), [11/10 실험](../app/ai_ownership_verification/experiments/results/ownership_workflow_v2_100_20261110.md) | 합성 DB·Gemini 의미 검색·5지선다. 실사진 연결 검증은 아님 |
| S6 | [ORM](../app/models.py), [API](../app/api.py), [분석 서비스](../app/services.py), [설정](../app/config.py) | 현재 동작의 기준 |
| S7 | [팀 API](../../docs/api.md) | 현재 웹→BE 계약 |
| S8 | [개발 지침](../../docs/development-guide.md) | 저장소 문서·개발 경계 |
| S9 | [이미지 인수인계](../../imageprocess/docs/backend-handoff.md), [특징 스키마](../../imageprocess/docs/feature-schema.json) | 사진 2장 prepare/parse·fixed_common_v2 계약 |
| S10 | [10종 실험](../../imageprocess/docs/experiments/single-photo-10-cases.md) | 단일 사진 성공 결과·오인식. 쌍사진 품질·최종 모델 우열의 근거는 아님 |

원문 문서의 실험 결과는 보존한다. 이번 BE 설계가 이전에 미정이던 항목을 구체화한 경우 그 차이와 검증 범위를 기록한다. 11/01 직렬화 오류 보완의 완료 기록은 [별도 보존 문서](implementation-plan-20261101.md)에 있다.
