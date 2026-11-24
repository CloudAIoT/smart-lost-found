# 분실물 검색·객관식 소유 확인 워크플로

자연어 검색부터 질문·답변, 소유 확인과 반환 코드 발급까지의 구현 계약이다.

## 1. 처리 단계

1. 종류 선택 없이 사용자 설명을 받는다. Gemini가 설명에서 단서를 추출한다. 말하지 않은 값은 null이며, 인용한 단어가 원문에 없으면 오류로 처리한다.
2. 백엔드는 보관 중이고 유효한 배정이 있는 물품의 특징을 Gemini에 보낸다. Gemini는 사용자가 말한 항목만 비교하고, DB의 빈 값은 건너뛴다. 비교할 항목이 하나 이상 있고 전부 일치하는 물품을 반환한다. 동의어나 번역은 일치로 보고, 장소나 색상 일부만 겹치는 경우는 제외한다.
3. 검색 결과가 있으면 서버가 확인 시도를 만든다. 후보가 여럿이면 답변에 따라 가장 많은 후보를 구분할 수 있는 특징을 우선한다. 후보가 하나 남아도 처음 말하거나 답한 적 없는 등록 특징을 확인한다. 비교에 쓰이는 실제 값을 선택지에 넣고 일반 질문은 Gemini가 만든 오답으로 나머지를 채워 총 5개를 섞는다. 구조의 위치 질문은 아래 기준에 따라 서버가 보기를 만든다.
4. 답변과 공개 특징이 다르면 해당 후보를 제외한다. DB에 값이 비어 있는 후보는 남긴다. 검토된 비공개 근거는 독립된 두 그룹에서 답이 어긋나면 해당 후보를 제외한다.
5. 확인 가능한 미질문 특징이 남아 있으면 다음 질문을 제공한다. 질문을 마쳤을 때 후보가 하나로 좁혀지면 해당 물품의 등록 사진 한 장을 보여 준다. 확인할 질문이 처음부터 없고 후보가 하나면 바로 사진 단계로 진행한다.
6. 사용자에게 사진 속 물건이 본인 것인지 묻는다. 맞다고 확인하면 수령 OTP를 발급하고, 아니라고 하면 소유 확인 실패로 종료한다.

검색은 종류·색상·형태·표면 질감·브랜드·문구·구조·구별 특징과 습득 위치를 비교한다. 이전 기록에 저장된 재질도 검색에 사용한다. 최대 100건을 한 배치로 보내고, Gemini는 배치의 각 물품에 포함 또는 제외 판정을 반환한다. 백엔드는 응답 형식과 배치 내 ID의 누락·중복 여부를 확인하고 포함 판정을 후보에 반영한다. 조회 상한인 500건을 넘으면 `search_limit`을 반환한다. 확인 질문에서 서로 다른 등록 값이 5개를 초과하면 다른 특징을 먼저 묻고, 후보가 줄어든 뒤 그 특징을 다시 검토할 수 있다.

구조에서 부품과 위치가 함께 확인될 때는 해당 부품의 위치만 묻는다. `마우스 휠이 가운데 있음`, `Scroll wheel in the center`, `스크롤 휠이 좌측에 있음`은 같은 부품의 위치로 비교한다. 보기는 가운데·왼쪽·오른쪽·앞쪽·뒤쪽을 섞어서 제공한다. 부품이 빠진 `가운데 있음`, 위치가 빠진 `좌우 클릭 버튼이 있음`, 한 부품에 위치가 여러 곳으로 기록된 경우에는 그 값으로 위치 답변을 채점하지 않는다. 구조 원문은 후보 검색에 계속 사용한다.

질문과 특징 설명은 한글로 표시한다. `navy`는 `남색`, `Carhartt`는 `칼하트`로 표시한다. 물품에 적힌 문구나 각인은 등록된 표기를 그대로 쓰고, 영문 이니셜 `MJ`를 확인하는 질문에서는 다른 보기도 `AB`와 같은 영문 표기로 만든다. 서버에서 변환할 수 없는 이름과 특징은 선택된 질문 단위로 Gemini에 번역을 요청한다. 같은 한글 문구로 변환된 보기는 하나로 묶고, 채점할 때는 각 후보의 등록 값을 모두 비교한다. 표시할 수 있는 응답을 끝까지 확보하지 못하면 재시도할 수 있는 오류를 반환한다.

### Gemini 모델 호출 순서

| 호출 단계 | 첫 번째 모델 | 두 번째 모델 | 세 번째 모델 |
| --- | --- | --- | --- |
| 사용자 문장의 단서 추출 | `gemini-3.5-flash-lite` | `gemini-3.1-flash-lite` | `gemini-2.5-flash-lite` |
| 배치별 후보 비교, 보기의 한글 변환과 5지선다 오답 생성 | `gemini-4-hydrogen` | `gemini-3.8-flash` | `gemini-3.5-flash-lite` |

단서 추출 요청, 후보 비교 배치, 오답 생성 요청마다 첫 번째 모델부터 시작한다. 호출 오류나 타임아웃이 발생하거나 JSON·단서·배치 ID·보기 검증을 통과하지 못하면 다음 모델을 한 번 호출한다. 정상 응답에서 단서나 후보가 비어 있으면 빈 결과를 사용한다. 실제 보기 다섯 개가 확보되면 오답 생성 호출을 생략한다. Gemini 4 Hydrogen으로 후보를 비교하고 오답을 생성할 때는 추론 수준을 `LOW`로 요청한다.

## 2. DB 데이터와 검토 메타데이터

등록 사진 두 장은 이미지 파트의 모듈로 준비한 후 Gemini에 한 번에 보낸다. 검증된 8개 특징을 `registrations.analysis_features`에 저장한다. 검색은 기존의 `data`로 감싼 저장 형식도 읽고, 등록 분석 상태가 성공인 행의 특징을 사용한다. 습득 위치는 별도 필드 `found_location`에서 읽는다. [두 장 분석 규격](../../../imageprocess/docs/backend-handoff.md)

검토된 비공개 특징을 추가 질문에 사용하는 경우 등록된 값과 일치하는 **서버 검토 메타데이터**를 저장한다. 아래 구조는 합성 필통 예시다.

~~~json
{
  "data": {
    "category": "pencil_case",
    "color": ["black"],
    "shape": "직사각형",
    "surface_texture": "직조 표면",
    "brand": "Carhartt",
    "visible_text": ["MJ"],
    "structure": ["측면 지퍼"],
    "distinctive_features": ["파란 원형 핀"]
  },
  "ownership_evidence": [
    {
      "feature_id": "attachment",
      "field": "distinctive_features",
      "value": "파란 원형 핀",
      "group": "attachment",
      "visibility": "private",
      "verified": true,
      "distinguishing": true
    },
    {
      "feature_id": "mark",
      "field": "visible_text",
      "value": "MJ",
      "group": "mark",
      "visibility": "private",
      "verified": true,
      "distinguishing": true
    }
  ]
}
~~~

같은 특징을 여러 필드에 표현한 경우 동일한 `group`으로 관리한다. 질문 유형 하나에 등록된 검토 값이 여러 개라면 그 유형은 건너뛴다. 자동 추출 특징만 저장한 물품도 검색, 질문, 사진 확인을 진행할 수 있다.

## 3. API

`LOCAL_DEVELOPMENT=true` 또는 `BUSINESS_API_ENABLED=true`에서 동작한다. 검색과 오답 생성에는 `GEMINI_API_KEY`와 위 모델 순서를 사용한다. 키오스크 등록 사진 분석은 `GEMINI_MODEL`을 사용한다. 수령 코드 발급에는 `RETURN_CODE_TTL_SECONDS`를 설정한다.

| 요청 | 본문 | 응답 |
| --- | --- | --- |
| `POST /api/ownership/attempts` | `{"description":"도서관 근처에서 검은색 필통을 잃었어요"}` | `question`, `photo_confirmation`, `completed`, `needs_clues`, `no_candidates`, `search_limit` |
| `GET /api/ownership/attempts/{attempt_id}` | 없음 | 저장된 질문, 사진 확인 단계 또는 완료 상태 |
| `POST /api/ownership/attempts/{attempt_id}/answers` | `{"question_id":"질문 UUID","option_id":"선택지 UUID"}` | 다음 질문, `photo_confirmation` 또는 완료 판정 |
| `GET /api/ownership/attempts/{attempt_id}/photo` | 없음 | 사진 확인 단계의 등록 JPEG와 `X-Ownership-Photo-Token` 응답 헤더 |
| `POST /api/ownership/attempts/{attempt_id}/photo-confirmation` | `{"confirmed":true,"photo_token":"사진 응답 토큰"}` 또는 `{"confirmed":false}` | 완료 상태인 `attempt`와 발급된 `return_code` 또는 `null` |

질문에는 문구와 선택지 ID·표시값만 포함한다. 원본 특징 전체, 정답 표시, 내부 후보 ID와 답변별 채점은 돌려주지 않는다. 초기 후보 수는 진행 중 갱신해 노출하지 않는다. 이전 질문으로 답변을 다시 보내거나 서로 다른 요청으로 같은 답을 동시에 제출하면 현재 버전과 일치하는 요청만 저장한다. 모델 오류는 소유 확인 실패로 저장하지 않으므로, 다음 질문 생성 실패 후 이전 답변을 다시 제출할 수 있다. 확인 시도는 20분 뒤 만료되며 DB에 남은 기록의 운영상 삭제·보존 정책은 별도로 필요하다.

## 4. 판정과 선재의 백엔드 연결

| 개발용 결과 | 의미 |
| --- | --- |
| `supported` | 질문을 마친 뒤 후보가 하나 남고, 사용자가 등록 사진 속 물건을 본인 것으로 확인함 |
| `not_confirmed` | 후보가 모두 제외됐거나 여러 개 남음, 검토된 비공개 근거 중 답변이 빠짐, 등록 사진이 없거나 사용자가 아니라고 응답함 |

검토된 비공개 근거가 있는 물품은 해당 질문에 모두 답해야 한다. 독립된 두 그룹에서 오답이 나온 후보는 즉시 제외하며, 오답이 한 그룹에만 있으면 사진 확인 단계에 진입할 수 있다. 공개 특징은 후보를 좁히는 데 사용한다. 검색 결과가 없으면 소유 확인 시도 생성 전에 `no_candidates`를 반환한다.

선재 백엔드에서 조회하는 내부 `OwnershipResult`는 실패 때 `failure_reason`을 함께 제공한다. 두 독립 오답 그룹으로 후보가 모두 사라진 경우 `wrong_evidence_limit`, 그 외의 후보 소진은 `candidates_eliminated`다. 마지막까지 후보가 여럿이면 `multiple_candidates`, 답하지 않은 필수 근거가 있으면 `verification_incomplete`다. 등록 사진이 없으면 `photo_unavailable`, 사용자가 사진을 보고 아니라고 하면 `photo_rejected`다. 이전 버전에서 보류로 끝난 시도는 조회 시 `not_confirmed`로 표시한다. 이전 버전에서 진행 중이던 시도 또는 사진 확인 전에 끝난 시도로 새 OTP가 필요한 경우에는 `development_v6`로 다시 시작한다.

사진은 해당 시도의 후보에 묶인 첫 번째 등록 사진을 사용하며, 첫 번째 촬영 기록이 비어 있으면 두 번째 기록을 사용한다. 백엔드는 사진을 읽은 응답에서만 확인 토큰을 전달한다. 사용자가 맞다고 답하면 서버는 토큰, 시도 만료, 물품 보관 상태와 근거의 유효성을 확인한 뒤 성공 판정과 OTP를 함께 커밋한다. 공개 응답에 확인된 물품 ID와 OTP가 담기며, 같은 시도에서 코드는 한 번 발급된다.

## 5. 로컬 검증

2026년 11월 19일에 합성 물품 100개를 로컬 SQLite에 넣고 실제 Gemini로 분실자 검색, 질문, 사진 확인과 OTP 발급을 실행했다. [실험 결과와 응답 기록](experiments/results/ownership_claimant_100_20261119.md)

`backend`에서 다음 명령을 실행한다. 테스트가 합성 등록 데이터를 넣고 Gemini 응답을 대체하므로 키와 네트워크 연결 없이도 검색부터 인계까지 검증할 수 있다.

~~~powershell
python -m pytest -q tests/test_ownership_workflow.py tests/test_migration.py
~~~

자동 테스트는 전부 정답, 오답 1개 허용, 오답 2개 탈락, 후보 여러 개에서 전부 정답, 자동 등록 특징만으로 사진 확인과 OTP 발급, 근거 그룹 중복, 사진 제공·거절·누락, OTP 중복 발급, 이전 보류 결과 조회, 시도 만료, 다음 질문 생성 실패, 사진 확인 전 근거 변경을 확인한다.

합성 물품 100개로 실제 Gemini를 연결한 결과는 [유사 검색·5지선다 실험 기록](experiments/results/ownership_workflow_v2_100_20261110.md)에 정리했다. 이전 기준의 실행은 [첫 워크플로 실험 기록](experiments/results/ownership_workflow_live_100_20261110.md)에서 확인할 수 있다.

수첩·지갑·필통·우산 등 10개 추가 사용자 사례의 질문, 선택지, 답변과 판정은 [사용자 분실물 찾기 추가 실험](experiments/results/ownership_workflow_user_flows_20261110.md)에 기록했다.
