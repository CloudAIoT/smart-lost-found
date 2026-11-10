# 분실물 검색·객관식 소유 확인 워크플로

2026-11-10 개발용 구현 계약. 기존 10/14 자유 질문 실험과 별도이다. 사진 선택·반환 코드·보관함 제어는 이 흐름의 범위 밖이다.

## 1. 처리 단계

1. 종류 선택 없이 사용자 설명을 받는다. Gemini는 설명에 실제 들어 있는 짧은 단서를 8개 후보 필드에 배치한다. 말하지 않은 값은 null이며, 인용한 단어가 원문에 없으면 오류로 처리한다.
2. 백엔드는 DB에서 현재 보관 중이고 유효한 배정이 있는 물품의 특징을 읽는다. Gemini가 사용자 단서와 등록 특징을 비교해 표현과 언어가 달라도 비슷한 물품을 찾는다. 위치 비교에는 사용자의 분실 위치와 등록된 습득 위치를 사용한다.
3. 검색 결과가 있으면 DB에 확인 시도를 만들고 객관식 질문 하나를 제공한다. 후보가 여럿이면 값이 달라 후보를 나눌 수 있는 특징을 우선한다. 공개 특징 질문은 후보 좁히기에 쓴다. 질문마다 등록된 실제 값으로 선택지를 구성하고 Gemini가 생성한 오답으로 남는 자리를 채워 총 5개를 섞어 보여준다. 질문 문구는 고정된 중립 문장을 사용한다.
4. 서버가 사용자 선택을 각 후보의 등록 값과 비교한다. DB에 확인된 값이 없으면 그 후보의 채점을 건너뛴다. 같은 후보에서 서로 다른 비공개 근거 그룹 두 개가 답변과 어긋나면 후보를 제외한다. 처음에 말한 항목과 이미 질문한 항목은 재질문하지 않는다.
5. 확인 가능한 미질문 특징이 남아 있으면 다음 질문을 제공한다. 후보가 모두 제외되거나 더 만들 수 있는 질문이 없으면 판정한다. 선재의 백엔드는 내부 함수로 저장된 결과를 읽을 수 있다.

Gemini에 보내는 검색 데이터는 요청 안에서만 쓰는 번호와 등록된 종류·색상·재질·브랜드·문구·구별 특징·습득 위치다. `description` 요약은 검색 비교에서 제외한다. 한 번에 최대 100건씩 비교하고 Gemini가 반환한 유효한 후보를 모두 모은다. 개발용 조회 상한인 500건을 넘으면 `search_limit`을 반환한다. 확인 질문에서 서로 다른 등록 값이 5개를 초과하면 다른 특징을 먼저 묻고, 후보가 줄어든 뒤 그 특징을 다시 검토할 수 있다.

## 2. DB 데이터와 검토 메타데이터

기존 `registrations.analysis_features`에 이미지 분석 시제품의 7개 필드를 직접 저장하거나, 결과의 `data`에 넣은 형태를 읽을 수 있다. 등록 분석 상태가 성공일 때만 해당 특징을 읽는다. 등록 API에 선택 필드 `found_location`을 추가했다. 마이그레이션 `0002_ownership_attempts`가 이 컬럼과 `ownership_attempts` 테이블을 추가한다.

소유 확인에 쓸 특징에는 등록된 값과 일치하는 **별도의 서버 검토 메타데이터**가 필요하다. 아래 구조는 합성 필통 예시이고, 이미지 분석기가 이 값을 자동으로 제공한다고 가정하지 않는다.

~~~json
{
  "data": {
    "category": "pencil_case",
    "color": ["black"],
    "material": "fabric",
    "brand": "Carhartt",
    "visible_text": ["MJ"],
    "distinctive_features": ["파란 원형 핀"],
    "description": "검은색 필통"
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

같은 특징을 여러 필드에 표현한 경우에는 동일한 `group`으로 관리한다. 질문 유형 하나에 등록된 검토 값이 여러 개라면 현재 시제품에서는 그 유형으로 소유 확인 질문을 만들지 않는다. 모델의 특징 출력이나 공개 특징만 있는 물품은 검색 또는 후보 좁히기는 가능하지만, 소유 근거가 부족하면 판정을 보류한다. 메타데이터의 승인·저장 주체와 실제 분석기의 연결은 정민·선재와 협의가 필요하다.

## 3. 개발용 API

`LOCAL_DEVELOPMENT=true`일 때만 동작하며, 운영의 `BUSINESS_API_ENABLED=true`만으로 열리지 않는다. 테스트는 실제 서비스와 분리된 DB에서 실행한다. 로컬 서버에서 사용하려면 DB에 마이그레이션을 적용하고 Gemini 설정 및 보관 완료 샘플 레코드를 준비한다.

| 요청 | 본문 | 응답 |
| --- | --- | --- |
| `POST /api/ownership/attempts` | `{"description":"도서관 근처에서 검은색 필통을 잃었어요"}` | `question`, `completed`, `needs_clues`, `no_candidates`, `search_limit` |
| `GET /api/ownership/attempts/{attempt_id}` | 없음 | 저장된 현재 질문 또는 완료 상태 |
| `POST /api/ownership/attempts/{attempt_id}/answers` | `{"question_id":"질문 UUID","option_id":"선택지 UUID"}` | 다음 질문 또는 `completed`와 개발용 판정 |

질문에는 문구와 선택지 ID·표시값만 포함한다. 원본 특징 전체, 정답 표시, 내부 후보 ID와 답변별 채점은 돌려주지 않는다. 초기 후보 수는 진행 중 갱신해 노출하지 않는다. 이전 질문으로 답변을 다시 보내거나 서로 다른 요청으로 같은 답을 동시에 제출하면 현재 버전과 일치하는 요청만 저장한다. 모델 오류는 소유 확인 실패로 저장하지 않으므로, 다음 질문 생성 실패 후 이전 답변을 다시 제출할 수 있다. 확인 시도는 20분 뒤 만료되며 DB에 남은 기록의 운영상 삭제·보존 정책은 별도로 필요하다.

## 4. 판정과 선재의 백엔드 연결

| 개발용 결과 | 의미 |
| --- | --- |
| `supported` | 남은 후보가 1개이고, 그 후보에서 확인 대상으로 잡은 비공개 특징이 하나 이상 있으며 전부 일치 |
| `not_confirmed` | 서로 다른 비공개 근거 그룹 두 개에서 어긋나 후보가 모두 제외됨 |
| `insufficient_evidence` | 후보가 여럿 남거나 질문 가능한 특징이 소진되는 등 근거가 부족 |

질문 횟수는 확인할 수 있는 특징 수에 따라 달라진다. 후보가 하나여도 남은 특징을 계속 묻는다. 질문을 끝냈을 때 오답이 한 개 있거나 채점할 비공개 특징이 없으면 판정을 보류한다. 공개 질문에서 AI가 생성한 오답을 선택하면 후보를 유지한다. 기존 `development_v1` 시도는 완료 결과를 읽을 수 있고, 진행 중인 경우 `development_v2`로 다시 시작한다.

선재의 백엔드가 같은 프로세스에서 연결할 함수는 `app.ai_ownership_verification.workflow.get_ownership_result(session, attempt_id)`이다. 완료 전에는 `None`을 반환하고, 완료되면 `attempt_id`·`item_id`·`decision`·`policy_version`·일치 근거 그룹 수·답변 수를 담은 `OwnershipResult`를 반환한다. 후보가 하나면 판정 보류에서도 `item_id`를 포함한다. 결과를 사용하는 쪽에서는 `decision`으로 소유 근거 확인 여부를 읽는다. `supported` 결과를 읽을 때는 보관 상태와 일치 근거의 유효성을 다시 확인한다.

## 5. 로컬 검증

`backend`에서 다음 명령을 실행한다. 테스트가 합성 등록 데이터를 넣고 Gemini 응답을 대체하므로 키와 네트워크 연결 없이도 검색부터 인계까지 검증할 수 있다.

~~~powershell
python -m pytest -q tests/test_ownership_workflow.py tests/test_migration.py
~~~

자동 테스트는 전부 정답, 오답 1개와 2개, 공개 특징만 일치, 비공개 근거 없음, 근거 그룹 중복, 이전 질문 재제출, 시도 만료, 다음 질문 생성 실패, 결과 전달 전 근거 변경을 확인한다.

합성 물품 100개로 실제 Gemini를 연결한 결과는 [유사 검색·5지선다 실험 기록](experiments/results/ownership_workflow_v2_100_20261110.md)에 정리했다. 이전 기준의 실행은 [첫 워크플로 실험 기록](experiments/results/ownership_workflow_live_100_20261110.md)에서 확인할 수 있다.

수첩·지갑·필통·우산 등 10개 추가 사용자 사례의 질문, 선택지, 답변과 판정은 [사용자 분실물 찾기 추가 실험](experiments/results/ownership_workflow_user_flows_20261110.md)에 기록했다.
