# 백엔드 기능 명세

개정: 2026-11-16 · 기준: `7c001a8` 및 이미지 처리 `51ea1d8`.

**사용자의 구현 요청과 인덱스 검색 방향을 반영한 현재 계약이다.** 사진 2장 촬영·S3 저장 이후 분석·검색·질문을 연결한다. DB 구조는 [ERD](erd.md), 단계·설정·검증은 [구현 계획](implementation-plan-20261116.md)을 따른다. 실제 Gemini 키를 사용하는 검증은 남아 있다.

## 1. 범위와 현황

| 기능 | 현재 구현 | 이번 목표 |
| --- | --- | --- |
| F01·F02 상태 확인 | /health, /ready | 유지. 키가 없어도 서버 시작·DB readiness 정상 |
| F03 위치·카테고리 조회 | /api/kiosks, /api/categories | 유지 |
| F04 검색 | JSONB GIN 후보 검색 | 단순 단서는 모델 비교 생략, 상세 단서는 후보만 의미 비교 |
| F05 등록 | 선택 카테고리·설명·습득 위치, UUID | 기존 입력 유지, 분석 metadata·검토 근거 저장 추가 |
| F06 촬영·S3·미리보기 | 순차 촬영 2회, 저장·미리보기 완료 | 재구현하지 않음 |
| F07 배정·개방 | 분석 성공 후 별도 API로 개방 | 새 분석 계약 검증 연결 |
| F08 이미지 분석 | GeminiFeatureAnalyzer 연결 | prepare_item → Gemini → parse_item_output → DB |
| F09 질문·답변 | development_v3 개발용 API | 새 특징·검토 근거·버전 연동, development_v3 |
| F10~F12 반환·완료 | 코드·수령·문 닫힘 처리 | 기존 동작 유지. 소유 확인 결과와 반환 코드 발급의 연결은 별도 범위 |
| F13 장치 제어 | 기존 Pi HTTP 어댑터 | 유지. 이번 분석 작업에서 장치를 호출하지 않음 |

이번 구현의 이미지 분석 제공자는 **Gemini**다(사용자 요청). 모델 기본값은 실험 기록을 사용하되 운영 품질이 검증됐다고 보지 않는다. 이미지 파트는 전처리·프롬프트·스키마·파서를 제공하고 BE는 모델 호출·오류·저장·상태를 연결한다.

## 2. F04~F07: 등록과 분석 전후 경계

- F05: POST /api/registrations는 기존 kiosk_id와 선택값 primary_category_id, secondary_description, found_location을 받는다. 상세 설명은 분석의 필수 입력이 아니며 이미지 프롬프트에 넣지 않는다. 카테고리 UUID는 서버가 DB의 name(없으면 code)으로 해석한다.
- F06: 같은 등록의 shot_order 1·2가 DB에 있고 각각 S3 object_key를 가진 상태에서 시작한다. 분석 버튼은 사진을 재업로드·재촬영하지 않는다. FE는 기존 preview로 두 사진을 확인한다.
- F07: 분석 POST가 성공해도 보관함은 열리지 않는다. 별도 POST /api/registrations/{id}/deposit/open → 개방 확인 → POST /deposit/complete의 confirmed=true + 문 closed 뒤 stored가 된다. found_items는 개방 단계에서 생성한다.
- F04: 분석 성공만 한 ready 물품은 분실자 검색 대상이 아니다. 실제 stored 물품만 F09에서 검색한다.

## 3. F08: 두 사진 분석 API

### 3.1 요청·응답

`POST /api/registrations/{registration_id}/analysis`, **요청 본문 없음**, 동기 처리다. 서버가 DB/S3의 두 사진·카테고리를 결정한다. FE가 특징 정답이나 임의 S3 URL을 보내지 않는다.

성공은 기존 RegistrationView의 HTTP 200이며 status=ready, analysis_status=succeeded다. 응답에 `analysis_warnings: string[]`를 추가할 계획이다(미분석·실패 시 []). 기존 등록 관련 응답도 같은 필드를 쓴다. 원본 특징·브랜드 추정·검토 근거·S3 키는 FE 응답에 포함하지 않는다. API 자체가 질문 생성이나 보관함 개방을 실행하지 않는다.

### 3.2 처리 순서

1. 등록 존재·허용 상태, 촬영 순서 [1,2], 서로 다른 이미지 ID, 현재 성공 결과 재사용 가능 여부를 확인한다. 재사용은 같은 사진 ID·fixed_common_v2·유효한 특징일 때만 허용하며 모델을 다시 호출하지 않는다.
2. 실제 실행이 필요한 경우 키/설정과 서버 분석 슬롯을 확인한다. 키 없음은 AI_NOT_CONFIGURED, 슬롯 사용 중은 ANALYSIS_BUSY로 시작 전에 거절한다.
3. 등록을 조건부 갱신해 analyzing/running으로 확보하고 입력 이미지 ID를 기록한다. 이전 features·metadata·검토 근거를 비우고 커밋한다. 같은 등록의 동시 요청은 하나만 진행한다.
4. S3ImageStore.read로 두 JPEG를 순서대로 읽는다. 이미지 처리 Settings를 BE 설정에서 명시적으로 구성하며 imageprocess/.env를 별도로 읽지 않는다.
5. `prepare_item(registration_id, ImageInput 2개, category=DB label)`을 호출한다. 파일·픽셀 한도, EXIF 회전, RGB, 긴 변 1280px 축소를 적용하고 통합 프롬프트·응답 스키마·경고를 받는다.
6. BE의 Gemini 어댑터가 prepared.prompt와 prepared.images 2개를 **한 번의 요청**에 넣는다. response_schema를 지정하고 응답의 완료 여부·텍스트를 확인한다. SDK의 자동 재시도는 끈다.
7. `parse_item_output(prepared, raw_json)`으로 검증한다. 모델은 Features만 반환하며 등록·사진 ID는 호출자가 유지한다. 모델이 ID를 알아맞혔다고 간주하지 않는다.
8. 입력 사진 ID·순서·등록 상태를 다시 확인하고 data를 analysis_features, 버전·모델·경고 등을 analysis_metadata에 저장한다. ready/succeeded와 같은 트랜잭션으로 커밋한다. 준비된 PIL 이미지와 모델 클라이언트·분석 슬롯은 성공/실패 모두 해제한다.

S3·모델 호출을 기다리는 동안 DB 트랜잭션·행 잠금을 유지하지 않는다. 어댑터에 전달할 카테고리는 시작 전 조회하고, 어댑터 내부에서 DB를 다시 조회하지 않는다. FeatureAnalyzer는 keyword category와 슬롯 계약을, AnalysisResult는 분석 metadata를 포함한다.

### 3.3 성공 기준과 두 사진 해석

- fixed_common_v2의 8개 키·타입·길이 검증, 알 수 없는 키·중복 JSON 키·NaN/Infinity 거절.
- 빈 배열은 null. color/shape/surface_texture/visible_text/structure/distinctive_features 중 하나 이상의 관찰값 필요. 전부 null 또는 category/brand만 있으면 INSUFFICIENT_VISUAL_FEATURES.
- 두 사진의 보완 정보는 합치고 같은 부착물은 중복 집계하지 않도록 지시한다. 충돌 필드는 null, 다른 물건이면 전체 null을 요청한다. **동일 물건 판별·사실성 검증기는 아니다.**
- 흐림·어두움은 기본적으로 경고이며 성공과 공존할 수 있다. 언어 규칙·캐릭터 인식의 정확성은 JSON 검증으로 보장하지 않는다.
- 입력 카테고리와 모델 category를 구분한다. primary_category_id는 변경하지 않는다.

### 3.4 오류·재요청

| 상황 | 목표 HTTP / code | 상태·동작 |
| --- | --- | --- |
| 등록 없음 | 404 REGISTRATION_NOT_FOUND | 변경 없음 |
| 사진 2장 미완료 | 409 TWO_IMAGES_REQUIRED | 변경 없음 |
| 같은 등록 진행 중·허용하지 않는 상태 | 409 INVALID_REGISTRATION_STATE | 추가 모델 호출 없음 |
| 다른 분석이 슬롯 사용 중 | 409 ANALYSIS_BUSY | 변경 없음 |
| 키 없음 | 503 AI_NOT_CONFIGURED | S3·모델 호출 및 상태 변경 없음 |
| S3 읽기 실패 | 502 IMAGE_STORAGE_FAILED | draft/failed, 사진 유지 |
| 저장 사진 오류·크기/픽셀 초과 | 502 INVALID_IMAGE / IMAGE_TOO_LARGE | draft/failed. FE 업로드 요청이 아니므로 원본 ServiceError HTTP를 그대로 복사하지 않음 |
| 모델 429 / timeout | 502 GEMINI_RATE_LIMITED / GEMINI_TIMEOUT | draft/failed, 자동 재시도 없음 |
| 인증·모델 접근·차단·응답 잘림·기타 외부 실패 | 502 GEMINI_REQUEST_REJECTED / GEMINI_MODEL_NOT_FOUND / GEMINI_RESPONSE_BLOCKED / GEMINI_OUTPUT_TRUNCATED / GEMINI_API_FAILED | draft/failed |
| JSON/특징 검증 실패·관찰 없음 | 502 INVALID_MODEL_OUTPUT / INSUFFICIENT_VISUAL_FEATURES | draft/failed |
| 결과 대상·직렬화 검증 실패 | 502 INVALID_ANALYSIS_RESULT | draft/failed |
| 외부 처리 중 등록/입력 변경 | 409 ANALYSIS_RESULT_STALE | 변경된 행을 덮어쓰지 않음, 운영 확인 후 재시작 |

외부·모듈 오류를 IntegrationError로 변환해 기존 실패 경로에 연결한다. 오류 본문은 기존 {detail:{code}} 형식을 유지한다. SDK timeout을 별도로 식별하도록 보완한다. 알려진 실패 뒤 사용자는 같은 등록·사진으로 명시적 POST 재요청을 할 수 있다. 프로세스 중단·DB 장애는 자동 복구 성공으로 포장하지 않는다.

새 계약의 ready 성공은 재사용한다. 구버전 ready이며 found_items가 아직 없는 경우 같은 POST로 새 규격 재분석을 허용한다. opening 이후·stored 물품의 재분석은 이번 API에서 거절한다.

## 4. F09: 유사 물품 검색 → 질문 → 판정

### 4.1 기존 개발용 API 유지

| 요청 | 입력 | 출력 |
| --- | --- | --- |
| POST /api/ownership/attempts | {description: 사용자 분실 설명} | question / completed / needs_clues / no_candidates / refine_search |
| GET /api/ownership/attempts/{id} | 없음 | 현재 질문 또는 저장된 완료 결과 |
| POST /api/ownership/attempts/{id}/answers | {question_id, option_id} | 다음 질문 또는 completed |

LOCAL_DEVELOPMENT=true에서만 제공하는 현행 제한을 유지한다. BUSINESS_API_ENABLED만으로 공개하지 않는다. 공개 운영의 인증·반환 승인 연결은 이번 검토안의 완료 조건이 아니다.

### 4.2 비슷한 item을 찾는 방법

1. Gemini가 설명에서 **category/color/shape/surface_texture/brand/visible_text/structure/distinctive_features/location** 단서를 추출한다. 사용자가 말한 연속 문자열을 그대로 인용하고, 없는 정보는 null이다. 원문에 없는 단서는 서버가 거절한다. description은 원문 입력이며 더 이상 검색 특징 키가 아니다.
2. 등록 성공 시 `search_features`에 정규화 특징을 저장한다. NFKC·대소문자·공백/기호 정리와 명시적인 한영 별칭을 공통 적용한다. 예: 필통/pencil_case → pencilcase, 검은색/black → black. 형태·문구·구조 등은 전체 정규화 문자열과 단어 토큰을 저장한다. 원본 특징은 보존한다.
   카테고리는 명시적인 검색용 묶음을 사용한다. 실험의 card_holder/card_wallet은 지갑, wireless_earbud_case는 이어폰, baseball_cap은 모자, compact_powder는 화장품으로도 찾는다. 상세 모델 분류는 원본 analysis_features에 남는다. 실제 10종 실험 JSON으로 한글 단서 매칭을 회귀 검증한다.
3. PostgreSQL의 `search_features @> {"필드":["토큰"]}` 조건을 OR로 결합해 GIN(jsonb_path_ops)으로 겹치는 특징을 찾는다. 물건·등록·배정 모두 stored, ended_at IS NULL, 분석 succeeded 조건을 함께 적용한다. 알려진 카테고리 충돌은 제외하고 null은 유지한다. 모델 category가 null일 때만 입력 카테고리 name을 fallback으로 쓴다.
4. 종류·색·브랜드만 입력되면 명시적인 정규화 값으로 비교하며 유사 후보 모델 호출을 생략한다. 이 경로에서는 알려진 색 충돌도 DB에서 제외한다. 모델 추정 브랜드 충돌만으로는 제외하지 않는다. 상세/위치 단서가 있으면 조회한 후보에만 Gemini 의미 비교를 한 번 수행한다. 모델에는 요청 내 정수 번호와 특징만 보내고 UUID·S3 사진·검토자 정보는 제외한다.
5. DB는 최대 51개만 읽는다. 50개를 넘으면 `refine_search`를 반환하고 추가 단서를 요청한다. 일부를 잘라 소유 확인으로 넘기지 않는다. 등록 전체 건수에 대한 500개 제한은 없으며 요청 시 전체 특징을 읽거나 재색인하지 않는다.
6. 모델 비교를 한 경우 번호·중복·사용자와 물품 양쪽에 존재하는 matched_fields를 검증한다. 실패하면 질문을 시작하지 않는다. 후보 일치만으로 소유 확인을 통과시키지 않는다.

예: “검은 필통” → category=pencilcase, color=black → 인덱스로 후보 조회 → 검토된 핀·문구로 질문 생성. 단서 추출과 필요한 오답 생성에는 여전히 Gemini 호출이 있다. DB 인덱스만으로 전체 API 지연이 사라지는 것은 아니다.

**검색 한계:** 인덱스는 의미 검색기가 아니다. 별칭 사전에 없는 번역·동의어와 한국어 조사 변화는 후보 단계에서 누락될 수 있다. Gemini는 DB에서 누락된 물건을 복구하지 못한다. 공통 단어로 후보가 많거나 미확인값이 많으면 추가 설명 뒤에도 refine_search가 유지될 수 있다. 이 경우 수동 탐색/별칭 확장을 검토하며 임의 상위 K로 확정하지 않는다. SQLite 테스트는 JSON 조회로 기능만 확인하고 성능은 PostgreSQL에서 확인한다.

### 4.3 질문 생성·채점

최신 11/10 실험의 **5지선다, 한 번에 한 문항** 흐름을 유지한다. 이전 10/14의 자유 질문 최대 3개 실험은 현재 계약이 아니다.

- 검색용 전체 특징과 FE 공개용 특징, 검증용 근거를 분리한다. 비공개 특징도 서버 측 후보 비교에는 쓸 수 있지만 공개 후보 설명으로 반환하지 않는다.
- 후보 좁히기 필드는 color/shape/surface_texture/brand/structure다. 비공개로 지정된 항목은 제외하고, 단일 값·1~100자·후보 간 구별 가능한 경우만 사용한다. visible_text/distinctive_features를 검토 없이 공개 질문에 쓰지 않는다.
- 소유 확인 필드는 distinctive_features/visible_text/structure/surface_texture/shape/brand/color이며 ERD의 검토 근거 조건을 만족해야 한다. category/location 일치 자체는 소유 근거가 아니다.
- 처음 말한 필드와 이미 질문한 필드는 다시 묻지 않는다. 기존 한 필드당 한 문항 제약을 유지하고, 필드 내 여러 근거를 임의 선택하지 않는다.
- 후보를 잘 나누는 필드를 선택한다. 실제 등록된 선택값을 포함하고 부족한 수만 Gemini의 오답 후보로 채워 총 5개를 섞는다. 실데이터·동의어 중복과 다른 비공개 정답 노출을 검사한다. 오답 생성이 실패하면 질문 실패로 처리한다.
- 질문 문구는 새 필드를 포함한 서버 템플릿이다. AI가 자유 서술 질문을 만들거나 답변을 임의 평가하는 구조가 아니다. 서버가 option_id를 저장된 값과 대조한다.
- FE에는 질문 ID·문구·선택지 ID/표시값·진행 상태와 개발용 완료 판정만 제공한다. 정답 표시·내부 item ID·검토 근거·후보별 채점은 공개하지 않는다. 실제 정답이 선택지 중 하나에 포함되는 것은 5지선다 계약 자체의 동작이다.
- 공개 특징 질문은 후보 좁히기에만 사용한다. 확인 가능한 독립 비공개 group 두 개에서 틀리면 후보를 제외한다. 모르는 DB 값은 오답으로 세지 않는다.
- 최종 후보가 1개이고 질문 대상으로 채택한 비공개 근거가 하나 이상이며 모두 맞으면 supported. 후보가 모두 제외되면 not_confirmed. 근거 없음·복수 후보·일부 오답은 insufficient_evidence다. 이 개발 정책을 운영 소유권 정확도로 해석하지 않는다.
- attempt 만료 20분, 버전 조건부 갱신을 유지한다. 모델 오류면 답변 부분 저장을 하지 않는다. 후보별 analysis_id와 근거가 바뀌거나 보관 상태가 바뀌면 재시작 또는 인계 거절한다.

**키만 입력하면 실제 분석·검색·선택지 생성의 연결 검증이 가능하도록 준비한다.** 다만 실제 물건의 supported 판정에는 별도로 사진을 검토한 근거가 필요하다. 검토 CLI와 승인된 합성 fixture를 미리 준비하여 이 조건이 구현을 막는 공란으로 남지 않게 한다. 자동 분석 결과만 있는 실제 물건은 근거 부족으로 끝날 수 있다.

## 5. 기존 보관·수령 경계 유지

F10 반환 코드는 현재 보관 물건에 소유 확인 없이 발급되는 구현이다. 이 사실을 supported와 자동 연결된 것으로 바꾸지 않는다. 반환 발급 정책·인증·반환 코드와 attempt FK 연결은 후속 범위다.

F11 수령 개방은 코드 hash·대상 키오스크·배정을 검사하고 active→opening→used로 처리한다. 명확한 장치 실패는 active, 결과 불명은 locked다. F12 투입·수령 완료는 사용자 confirmed=true와 문 closed가 함께 필요하다. 수령 완료 시 found_items=retrieved, 배정 종료로 칸을 해제한다. F13 장치 명령 ID·중복 억제·Pi 규약은 유지한다. 세부 현재 계약은 [팀 API](../../docs/api.md)를 따른다.

## 6. 검증 근거와 남은 확인

이미지 실험은 Gemini 단일 사진 10종이며 평균 성공 응답 대기 7.89초, 캐릭터 오인식 사례가 있다. 쌍사진 모델 품질·EC2 종단 연동은 미검증이다. 소유 확인 실험은 합성 100개 물품 DB와 실제 Gemini를 쓴 별도 실험이며 실제 사진→특징→질문 통합 검증이 아니다.

이번 작업은 문서 수정만 수행한다. 사용자 검토 후 구현 단계에서 키 없이 API·DB·상태·모델 요청 형태·오류·검색·질문·마이그레이션을 테스트한다. 키 입력 이후 실제 두 사진·모델 접근·할당량·지연·질문 품질을 검증한다. 모델 기본값과 시간 제한은 계획의 시험 설정이며 SLA가 아니다.
