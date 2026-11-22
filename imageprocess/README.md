# 이미지 분석 및 특징 추출

스마트 분실물 보관함에 등록할 사진과 습득자가 선택한 카테고리로 물품 특징을 추출하는 로컬 MVP이다. Qwen을 사용하는 MLX 경로와 Gemini API 경로를 같은 전처리·특징 스키마로 비교할 수 있다. **서비스에 사용할 모델과 배포 방식은 아직 결정하지 않았다.**

현재 백엔드 연결 단위는 **사진 2장 + 카테고리 → 전처리·통합 프롬프트 → 모델 응답 검증 → 공통 필드 7개 + 특이 특징**이다. 이미지 파트는 처리 함수와 입출력 규격을 제공하며 모델 API·HTTP API·AWS 설정·DB 저장은 백엔드 담당자가 연결한다. 습득자의 상세 notes는 요구하지 않는다. 기존 HTTP API는 단일 사진 로컬 실험용이다.

**실제 품질 검증은 사진 1장씩 10종을 분석한 결과**에 근거한다. [10종 실험과 오인식 사례](docs/experiments/single-photo-10-cases.md), [특징 발췌 JSON](docs/experiments/single-photo-10-cases.json), [AWS 백엔드 연결 방법](docs/backend-handoff.md)을 제공한다. 두 사진 처리는 오프라인 테스트로 검증했으며 실제 쌍사진 추론과 EC2 종단 연동은 아직 확인하지 않았다.

## 현재 구현

| 항목 | 구현 내용 |
| --- | --- |
| 입력 | 백엔드용 사진 2장과 ID·카테고리. 단일 사진 실험 도구도 유지 |
| 카테고리 | 한글·영어 입력 지원. 알려진 영어 종류는 한글 힌트로 변환하고 원래 입력은 별도로 보관 |
| 전처리 | 파일·픽셀 한도, EXIF 회전, RGB 변환, 비율 유지 축소, 흐림·어두움 경고 |
| 분석 | 백엔드에 모델 호출을 맡길 수 있는 prepare/parse 함수. 로컬 Qwen/MLX·Gemini 실험 경로도 제공 |
| 출력 | 공통 필드 7개 + 특이 특징 한 필드. 확인 불가 값은 null, 엄격한 JSON 검증 |
| 비교 | 저장된 두 결과의 특징·시간·조건·토큰 사용량을 오프라인으로 비교 |
| 보조 API | `GET /health`, `POST /analyze`; 모델/클라이언트 재사용, 동시 추론 제한 |
| 테스트 | 모델과 SDK를 대역으로 교체하여 CLI·전처리·출력 검증·API·오류 처리를 확인 |

카테고리는 모델의 판단을 돕는 힌트이며 결과를 강제로 덮어쓰지 않는다. 사진과 명백히 다르면 사진을 따르도록 지시한다. 카테고리만으로 재질·브랜드·부착물을 추측하지 않도록 구성했다. **JSON 검증 통과는 특징 내용의 정확성을 보장하지 않는다.**

## 백엔드 연결

저장소 루트에서 `from imageprocess import ImageInput, prepare_item, parse_item_output`으로 가져온다. `prepare_item`은 사진 2장을 검사·전처리하고 통합 프롬프트와 응답 스키마를 제공한다. 백엔드가 모델을 호출한 뒤 `parse_item_output`에 JSON 텍스트를 넘기면 입력 사진 ID·순서를 유지한 결과를 받는다. 이 함수 자체는 네트워크나 모델을 호출하지 않는다. 상세 예제·오류 계약·패키징은 [인수인계 문서](docs/backend-handoff.md)에 있다.

## 입력과 출력 예시

### 단일 사진 실험 입력: 사진 1장과 선택 카테고리

필통 비교 실험에서 사용한 입력은 다음과 같다. 브랜드·색상·재질·핀에 대한 설명은 사전에 입력하지 않고 사진에서 추출하도록 했다.

| 입력 | 실험에서 전달한 값 | 설명 |
| --- | --- | --- |
| 사진 | `case.jpg` 1장 | 검정 Carhartt 필통 사진. 전처리한 이미지를 모델에 전달 |
| 카테고리 | `필통` | 습득자 선택값. 모델에 한글 힌트로 전달하고 원래 입력은 결과에 보관 |
| 상세 notes | 없음 | 물품의 상세 특징을 사용자에게 별도로 입력받지 않음 |

현재 코드는 카테고리를 생략한 이미지 단독 분석도 지원한다. 이 문서의 비교 결과는 두 방식 모두 `필통` 카테고리를 제공한 사례이다. `--backend`와 `--model`은 개발자가 고르는 실행 설정이며 습득자에게 요구하는 물품 정보가 아니다.

### 출력: 공통 필드 7개 + 특이 특징

사진 10장의 자유 추출 결과를 검토하여 다음 공통 필드를 정했다. 결과 JSON의 `data`에 모든 키를 항상 포함하고, 확인하지 못한 값은 JSON `null`로 남긴다.

| 공통 필드 | 타입 | 의미 |
|---|---|---|
| `category` | string 또는 null | 모델이 판단한 물품 종류 |
| `color` | string[] 또는 null | 주요 색상, 비중이 큰 색부터 |
| `shape` | string 또는 null | 전체 형태 |
| `surface_texture` | string 또는 null | 눈에 보이는 질감·광택 |
| `brand` | string 또는 null | 글자·로고·특징적 디자인으로 식별한 브랜드 |
| `visible_text` | string[] 또는 null | 실제 읽히는 문구의 원문 |
| `structure` | string[] 또는 null | 개폐 방식·손잡이 등 주요 구조 |

물품별 세부는 **`distinctive_features: string[] 또는 null` 한 필드**에 모은다. 캐릭터 식별, 부착물의 색·모양·위치, 구체적인 무늬·그림·확인되는 흔적을 담는다. 브랜드나 캐릭터 후보 목록은 주지 않고 모델의 시각 지식으로 식별하도록 유도한다. 캐릭터명은 판단 근거가 된 외형과 함께 쓰며, 구분하기 어려우면 명칭을 단정하지 않고 외형만 기술한다.

아래는 필통 사진을 바탕으로 한 **새 구조 설명용 예시**다. 새 프롬프트로 재추론한 실제 결과가 아니다.

```json
{
  "success": true,
  "backend": "gemini",
  "input_category": "필통",
  "data": {
    "category": "필통",
    "color": ["검정색", "흰색", "주황색"],
    "shape": "세로로 긴 직사각형",
    "surface_texture": "격자 모양의 직조 질감",
    "brand": "Carhartt",
    "visible_text": ["carhartt"],
    "structure": ["측면 지퍼", "상단 직물 고리"],
    "distinctive_features": ["오른쪽 위에 파란색 원형 핀과 은색 날개 모양 핀이 1개씩 부착되어 있다."]
  },
  "warnings": []
}
```

`input_category`는 습득자 입력, `data.category`는 모델 분류이다. JSON 키는 영어로 유지하고 **카테고리·색상·형태·질감·구조·특이 특징 값은 한국어로 전달**한다. 브랜드명과 `visible_text`는 번역하지 않고 원문을 보존한다. 설명 속 고유명사·기술명도 원문을 포함할 수 있다. 모델이 알아본 브랜드나 캐릭터 이름을 사진에 실제로 쓰인 글자처럼 `visible_text`에 넣지 않는다.

모델이 반환한 알려진 영어 카테고리·색상은 파서에서 한국어로 정규화한다. 예를 들어 `pencil_case` → `필통`, `black` → `검정색`이다. 자유로운 한국어 종류도 허용한다. 그 외 설명에 한국어 문자가 없으면 `INVALID_MODEL_OUTPUT`으로 거부하며 임의 번역하거나 추가 API를 호출하지 않는다. 이 검사는 언어의 완전성이나 특징의 사실성을 보장하지 않는다. 문제 생성 담당자는 `result.data.model_dump(mode='json')`으로 특징 객체를 받을 수 있다.

브랜드를 확인할 수 없으면 `"brand": null`, 읽을 수 있는 문구가 없으면 `"visible_text": null`로 남긴다. null은 미확인이라는 뜻이며 해당 특징이 없다는 증거는 아니다. 목록형 필드의 빈 배열도 파싱 후 null로 통일한다. 이전 `material`은 관찰 가능한 `surface_texture`로 대체하고 `description`은 제거했다. 이전 실제 결과는 원본을 유지하며 오프라인 비교 도구에서 계속 읽을 수 있다.

전체 CLI 결과에는 `model_name`, 밀리초 단위 `timings_ms`, 이미지·프롬프트 해시와 생성 설정인 `comparison_metadata`, API 토큰 사용량 `usage`도 저장한다. 새 결과의 `comparison_metadata.feature_schema`와 두 사진 결과의 `schema_version`은 `fixed_common_v3_ko`다. 기존 10종 실험(`fixed_common_v2`)과 Qwen/Gemini 비교의 영어 출력은 당시 기록 그대로 보존하며 한국어 규격으로 재추론한 결과가 아니다. 로컬 HTTP 형식은 [설계 문서](docs/design.md)에 있다.

## 폴더 구성

```text
imageprocess/
├── __init__.py                  # 백엔드용 처리 함수 공개
├── README.md
├── docs/
│   ├── backend-handoff.md       # 사진 2장·ID·모델 호출 연결 계약
│   ├── feature-schema.json      # 현재 특징 JSON Schema
│   ├── experiments/             # 10종 단일 사진 결과 발췌와 검토
│   ├── usage.md                 # 환경 준비, 사용자 실행 명령, 설정
│   ├── design.md                # 현재 처리 방식·출력·API와 미구현 범위
│   ├── experiments.md           # 기존 Qwen/Gemini 결과 검토와 비교 한계
├── app/
│   ├── analyze_item.py          # 같은 물건의 사진 2장 준비·통합 분석 CLI
│   ├── analyze_image.py         # 단일 사진 실험 CLI
│   ├── analyze_photos.py        # 파일명 카테고리로 폴더 전체를 현재 규격으로 분석
│   ├── explore_features.py      # 이전 자유 필드 탐색 실험
│   ├── compare_results.py       # 저장된 결과 비교 CLI
│   ├── main.py                  # 로컬 FastAPI 진입점
│   ├── api/                     # health/analyze 라우트
│   ├── core/                    # 설정·오류·로그·요청 제한
│   ├── models/                  # 특징 및 응답 스키마
│   └── services/                # 전처리·Qwen·Gemini 어댑터
├── tests/
├── requirements-core.txt       # API·모델 클라이언트 없는 처리 의존성
├── requirements.txt            # 로컬 HTTP/실험 도구 의존성
├── requirements-dev.txt        # 테스트 도구
├── requirements-mlx.txt        # Qwen/MLX 선택 의존성
├── requirements-gemini.txt     # Gemini 선택 의존성
├── environment.yml             # 선택적으로 생성할 Conda 기본 환경
├── pytest.ini
├── .env.example                # 키가 비어 있는 설정 예시
└── .gitignore
```

개인 `.env`, `local/`의 사진·이전 문서, `outputs/`의 실제 결과, 다운로드 모델과 캐시는 Git에서 제외한다. 이 폴더의 `app`은 백엔드의 `app`과 독립적이므로 **명령은 `imageprocess/` 안에서 실행**한다.

## 실행 안내

기존 Conda 환경을 쓰는 경우:

```bash
cd imageprocess
conda activate gen_conda
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

이 테스트는 실제 모델이나 Gemini API를 호출하지 않는다. 분석할 backend의 추가 의존성 설치와 `.env` 설정 후, 사용자가 아래처럼 실행한다. `사진경로`는 실제 파일 경로로 바꾼다.

```bash
# 사진 2장 준비만 확인 — API 호출·모델 다운로드 없음
python -m app.analyze_item first.jpg second.jpg --category 필통 --prepare-only --output outputs/prepared_item.json

# 사진 2장 통합 Gemini 분석 — 사용자가 실행 시 실제 API 호출
python -m app.analyze_item first.jpg second.jpg --category 필통 --backend gemini --output outputs/two_photo_item.json

# 단일 사진 Gemini 실험
python -m app.analyze_image "사진경로.jpg" --backend gemini --category 필통 --output outputs/gemini.json

# photos 전체를 공통 필드 7개 + 특이 특징으로 분석
python -m app.analyze_photos ../photos

# Qwen으로 분석 (Apple Silicon용 MLX)
python -m app.analyze_image "사진경로.jpg" --backend mlx --category 필통 --output outputs/qwen.json

# 저장된 결과만 비교 — 추론/API 호출 없음
python -m app.compare_results outputs/qwen.json outputs/gemini.json --output outputs/comparison.md
```

설치·키 파일 작성·모델 준비·전체 설정은 [실행 안내](docs/usage.md), 입출력과 로컬 HTTP 인터페이스는 [설계 및 구현 범위](docs/design.md)에 정리했다.

## Qwen / Gemini 비교: 필통 사진 실험

아래는 **이전 특징 규격**으로 검정 Carhartt 필통 사진 1장과 `필통` 카테고리를 분석한 기록이다. 새 규격·새 프롬프트의 성능을 검증한 결과는 아니다. Qwen은 `mlx-community/Qwen2.5-VL-3B-Instruct-4bit`의 로컬 MLX 결과, Gemini는 `gemini-3.6-flash`의 API 결과이다. 원본 사진과 JSON 파일은 로컬에 보존하고 아래에 주요 결과를 요약했다.

### 추출 품질 비교

| 비교 항목 | Qwen 결과 | Gemini 결과 | 사진과 비교한 해석 |
| --- | --- | --- | --- |
| 물품 종류 | `pencil_case` | `pencil_case` | 두 결과 모두 필통으로 분류. 카테고리 힌트를 제공한 조건 |
| 브랜드·문구 | `Carhartt` / `carhartt` | `Carhartt` / `carhartt` | 두 결과 모두 브랜드와 문구 추출 |
| 색상 | `black` | `black`, `white`, `orange` | Gemini는 로고 패치 색상까지 포함. 물품의 주요 색상과 장식 색상을 구분하는 규칙은 필요 |
| 재질 | `unknown` | `fabric` | Gemini는 천 재질로 기술. Qwen의 불확실 표현 자체를 오답으로 세지 않음 |
| 핀·브로치 | `blue pin with white wings` | `Two metal pins attached near the top right` | 사진에는 파란 원형 핀과 별개의 은색 날개 모양 핀이 있음. Qwen은 두 특징을 섞었고 Gemini는 핀 2개를 기술 |
| 물품 구조·장식 | `orange wave logo` 중심 | 로고 패치, 상단 천 손잡이, 측면 지퍼 | Gemini가 손잡이·지퍼와 로고의 부착 형태까지 더 구체적으로 추출 |
| 요약 설명 | 검정 필통·브랜드·핀·로고 중심 | 재질·로고 패치·핀 2개·손잡이·지퍼 포함 | 이 사례에서는 Gemini가 물품 구조와 부착물 수를 더 충실하게 반영 |

예를 들어 Qwen은 서로 다른 핀을 **“흰 날개가 달린 파란 핀”**으로 묶어 표현했다. Gemini는 **“오른쪽 위에 부착된 금속 핀 2개”**로 기술하고, Qwen이 기술하지 않은 **상단 손잡이와 측면 지퍼**도 특징으로 추출했다. 다만 Gemini도 두 핀의 색상·모양을 각각 상세히 기술하지는 않았다.

### 처리 시간 비교

저장된 CLI 결과의 실측값을 초 단위로 환산했다.

| 처리 구간 | Qwen: 로컬 MLX | Gemini: API |
| --- | ---: | ---: |
| 모델/클라이언트 준비 | 3.077초 | 0.395초 |
| 추론·응답 대기 | **5.486초** | **20.749초** |
| 전체 처리 | **8.665초** | **21.260초** |

이 실행에서 Qwen은 더 빨랐고 Gemini는 더 자세한 구조·부착물 특징을 추출했다. Gemini의 추론·응답 대기는 약 **3.8배**, 전체 처리는 약 **2.5배** 길었다. Gemini 시간에는 네트워크 왕복이 포함된다. 준비 시간도 Qwen은 실제 모델 로딩, Gemini는 SDK 클라이언트 초기화로 의미가 다르다.

**사진 1건의 관찰이며 일반적인 정확도나 최종 모델 선택을 뜻하지 않는다.** Qwen의 이전 결과에는 이미지·프롬프트 해시와 생성 설정이 없어 동일 조건을 완전히 확인할 수 없다. 현재 기본 출력 한도는 Qwen 512토큰, Gemini 4096토큰이며 Gemini만 API의 JSON Schema 강제를 사용한다. 따라서 통제된 모델 벤치마크로 해석하지 않는다. 세부 조건과 후속 평가 기준은 [실험 기록](docs/experiments.md)에 정리했다.

## 추가 실험: 공통·특이 특징의 필드 탐색

자유 필드 탐색에 사용한 `app.explore_features`는 당시의 프롬프트와 별도 출력 구조를 유지한다. **현재의 공통 필드 7개와 자율 캐릭터 식별은 `app.analyze_photos`로 확인한다.** 두 명령과 결과 안내는 [특징 필드 탐색](docs/feature-exploration.md)에 정리했다.

## 남은 협의

| 항목 | 협의 내용 |
| --- | --- |
| 프런트엔드 | 카테고리만 받는 방향, 목록·코드, 재촬영 안내 |
| Raspberry Pi | 촬영 방향·해상도·화질, 기존 2장 촬영 설계에 맞는 입력 |
| 백엔드 | 등록 건과 사진 연결, 이미지 전달·호출 시점·결과 저장, 실패·재시도 |
| 소유 확인 AI | 필요한 특징, 확인 불가 값의 활용, 특징 공개 범위 |
| 모델 및 운영 | Qwen/Gemini 선택, 실행 위치, 품질·시간·비용 기준 |

두 사진의 전처리·통합 프롬프트·모델 입력·검증 함수는 구현했다. 실제 두 사진 추론 품질, HTTP 요청 규격·인증·S3·DB·EC2 연결은 백엔드 담당자와 검증해야 한다. 분석기 자체가 보관함을 열거나 소유권을 판정하지 않는다. 이번 변경은 백엔드와 배포 코드를 수정하지 않는다.
