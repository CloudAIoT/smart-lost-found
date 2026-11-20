# 분실물 검색·객관식 소유 확인 워크플로

자연어 검색부터 질문·답변, 소유 확인과 반환 코드 발급까지의 구현 계약이다.

## 1. 처리 단계

1. 종류 선택 없이 사용자 설명을 받는다. Gemini가 설명에서 단서를 추출한다. 말하지 않은 값은 null이며, 인용한 단어가 원문에 없으면 오류로 처리한다.
2. 백엔드는 DB에서 현재 보관 중이고 유효한 배정이 있는 물품의 특징을 읽는다. Gemini가 사용자 단서와 등록 특징을 비교해 표현과 언어가 달라도 비슷한 물품을 찾는다. 위치 비교에는 사용자의 분실 위치와 등록된 습득 위치를 사용한다.
3. 검색 결과가 있으면 DB에 확인 시도를 만들고 객관식 질문 하나를 제공한다. 후보가 여럿이면 값이 달라 후보를 나눌 수 있는 특징을 우선한다. 공개 특징 질문은 후보 좁히기에 쓴다. 질문마다 등록된 실제 값으로 선택지를 구성하고 Gemini가 생성한 오답으로 남는 자리를 채워 총 5개를 섞어 보여준다. 질문 문구는 고정된 중립 문장을 사용한다.
4. 서버가 사용자 선택을 각 후보의 등록 값과 비교한다. DB에 확인된 값이 없으면 그 후보의 채점을 건너뛴다. 같은 후보에서 서로 다른 비공개 근거 그룹 두 개가 답변과 어긋나면 후보를 제외한다. 처음에 말한 항목과 이미 질문한 항목은 재질문하지 않는다.
5. 확인 가능한 미질문 특징이 남아 있으면 다음 질문을 제공한다. 후보가 모두 제외되거나 더 만들 수 있는 질문이 없으면 판정한다. 확인에 성공하면 분실자 화면에서 반환 코드를 요청할 수 있다.

검색은 종류·색상·형태·표면 질감·브랜드·문구·구조·구별 특징과 습득 위치를 비교한다. 이전 기록에 저장된 재질도 검색에 사용한다. 한 번에 최대 100건씩 비교하며 조회 상한인 500건을 넘으면 `search_limit`을 반환한다. 확인 질문에서 서로 다른 등록 값이 5개를 초과하면 다른 특징을 먼저 묻고, 후보가 줄어든 뒤 그 특징을 다시 검토할 수 있다.

## 2. DB 데이터와 검토 메타데이터

등록 사진 두 장은 이미지 파트의 모듈로 준비한 후 Gemini에 한 번에 보낸다. 검증된 8개 특징을 `registrations.analysis_features`에 저장한다. 검색은 기존의 `data`로 감싼 저장 형식도 읽고, 등록 분석 상태가 성공인 행의 특징을 사용한다. 습득 위치는 별도 필드 `found_location`에서 읽는다. [두 장 분석 규격](../../../imageprocess/docs/backend-handoff.md)

소유 확인에 쓸 특징에는 등록된 값과 일치하는 **별도의 서버 검토 메타데이터**가 필요하다. 아래 구조는 합성 필통 예시이고, 이미지 분석기가 이 값을 자동으로 제공한다고 가정하지 않는다.

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

같은 특징을 여러 필드에 표현한 경우 동일한 `group`으로 관리한다. 질문 유형 하나에 등록된 검토 값이 여러 개라면 그 유형은 건너뛴다. 모델의 특징 출력이나 공개 특징만 있는 물품도 검색할 수 있다. 소유 판정에 쓸 검토 데이터가 없으면 판정을 보류하며, 검토 데이터를 등록하는 운영 절차는 별도 협의가 필요하다.

## 3. API

`LOCAL_DEVELOPMENT=true` 또는 `BUSINESS_API_ENABLED=true`에서 동작한다. 검색과 문답 생성에 Gemini 키와 모델을, 코드 발급에 `RETURN_CODE_TTL_SECONDS`를 설정한다.

| 요청 | 본문 | 응답 |
| --- | --- | --- |
| `POST /api/ownership/attempts` | `{"description":"도서관 근처에서 검은색 필통을 잃었어요"}` | `question`, `completed`, `needs_clues`, `no_candidates`, `search_limit` |
| `GET /api/ownership/attempts/{attempt_id}` | 없음 | 저장된 현재 질문 또는 완료 상태 |
| `POST /api/ownership/attempts/{attempt_id}/answers` | `{"question_id":"질문 UUID","option_id":"선택지 UUID"}` | 다음 질문 또는 `completed`와 개발용 판정 |
| `POST /api/found-items/{found_item_id}/return-code` | `{"attempt_id":"확인 시도 UUID"}` | 확인된 물품의 반환 코드, 만료 시각과 보관 키오스크 이름 |

질문에는 문구와 선택지 ID·표시값만 포함한다. 원본 특징 전체, 정답 표시, 내부 후보 ID와 답변별 채점은 돌려주지 않는다. 초기 후보 수는 진행 중 갱신해 노출하지 않는다. 이전 질문으로 답변을 다시 보내거나 서로 다른 요청으로 같은 답을 동시에 제출하면 현재 버전과 일치하는 요청만 저장한다. 모델 오류는 소유 확인 실패로 저장하지 않으므로, 다음 질문 생성 실패 후 이전 답변을 다시 제출할 수 있다. 확인 시도는 20분 뒤 만료되며 DB에 남은 기록의 운영상 삭제·보존 정책은 별도로 필요하다.

## 4. 판정과 선재의 백엔드 연결

| 개발용 결과 | 의미 |
| --- | --- |
| `supported` | 남은 후보가 1개이고, 그 후보에서 확인 대상으로 잡은 비공개 특징이 하나 이상 있으며 전부 일치 |
| `not_confirmed` | 서로 다른 비공개 근거 그룹 두 개에서 어긋나 후보가 모두 제외됨 |
| `insufficient_evidence` | 후보가 여럿 남거나 질문 가능한 특징이 소진되는 등 근거가 부족 |

질문 횟수는 확인할 수 있는 특징 수에 따라 달라진다. 후보가 하나여도 남은 특징을 계속 묻는다. 질문을 끝냈을 때 오답이 한 개 있거나 채점할 비공개 특징이 없으면 판정을 보류한다. 공개 질문에서 AI가 생성한 오답을 선택하면 후보를 유지한다. 기존 `development_v1` 시도는 완료 결과를 읽을 수 있고, 진행 중인 경우 `development_v2`로 다시 시작한다.

소유 확인에 성공하면 공개 응답에 `found_item_id`가 담긴다. 코드 발급 요청이 오면 서버는 시도 만료, 소유 확인 성공, 물품 일치, 보관 상태와 근거의 유효성을 확인한다. 확인 시도의 사용 기록과 코드 저장을 함께 커밋하므로 같은 시도로 두 번 발급할 수 없다.

## 5. 로컬 검증

`backend`에서 다음 명령을 실행한다. 테스트가 합성 등록 데이터를 넣고 Gemini 응답을 대체하므로 키와 네트워크 연결 없이도 검색부터 인계까지 검증할 수 있다.

~~~powershell
python -m pytest -q tests/test_ownership_workflow.py tests/test_migration.py
~~~

자동 테스트는 전부 정답, 오답 1개와 2개, 공개 특징만 일치, 비공개 근거 없음, 근거 그룹 중복, 이전 질문 재제출, 시도 만료, 다음 질문 생성 실패, 결과 전달 전 근거 변경을 확인한다.

합성 물품 100개로 실제 Gemini를 연결한 결과는 [유사 검색·5지선다 실험 기록](experiments/results/ownership_workflow_v2_100_20261110.md)에 정리했다. 이전 기준의 실행은 [첫 워크플로 실험 기록](experiments/results/ownership_workflow_live_100_20261110.md)에서 확인할 수 있다.

수첩·지갑·필통·우산 등 10개 추가 사용자 사례의 질문, 선택지, 답변과 판정은 [사용자 분실물 찾기 추가 실험](experiments/results/ownership_workflow_user_flows_20261110.md)에 기록했다.
