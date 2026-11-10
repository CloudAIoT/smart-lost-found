# 이미지 분석 및 특징 추출

스마트 분실물 보관함에 등록할 사진과 습득자가 선택한 카테고리로 물품 특징을 추출하는 로컬 MVP이다. Qwen을 사용하는 MLX 경로와 Gemini API 경로를 같은 전처리·특징 스키마로 비교할 수 있다. **서비스에 사용할 모델과 배포 방식은 아직 결정하지 않았다.**

현재 검증 범위는 **사진 1장 + 카테고리 → 전처리 → 모델 분석 → 특징 JSON 검증·저장**이다. 습득자에게 상세 notes 입력을 요구하지 않는다. AWS 백엔드와의 이미지 전달, 분석 결과 저장 및 서비스 간 통신은 협의 후 구현한다. HTTP API는 로컬 실험용으로 제공하며 팀의 확정 API 계약을 뜻하지 않는다.

## 현재 구현

| 항목 | 구현 내용 |
| --- | --- |
| 입력 | JPEG·PNG·WEBP 사진 1장, 선택적 카테고리 |
| 카테고리 | `필통` 등 기본 한글 카테고리를 영어 힌트로 변환. 원래 입력과 모델 분류를 구분해 보관 |
| 전처리 | 파일·픽셀 한도, EXIF 회전, RGB 변환, 비율 유지 축소, 흐림·어두움 경고 |
| 분석 | Qwen/MLX 또는 Gemini API. 실행 경로 확인용 mock 별도 제공 |
| 출력 | 종류·색상·재질·브랜드·문구·구별되는 특징·설명, 엄격한 JSON 검증 |
| 비교 | 저장된 두 결과의 특징·시간·조건·토큰 사용량을 오프라인으로 비교 |
| 보조 API | `GET /health`, `POST /analyze`; 모델/클라이언트 재사용, 동시 추론 제한 |
| 테스트 | 모델과 SDK를 대역으로 교체하여 CLI·전처리·출력 검증·API·오류 처리를 확인 |

카테고리는 모델의 판단을 돕는 힌트이며 결과를 강제로 덮어쓰지 않는다. 사진과 명백히 다르면 사진을 따르도록 지시한다. 카테고리만으로 재질·브랜드·부착물을 추측하지 않도록 구성했다. **JSON 검증 통과는 특징 내용의 정확성을 보장하지 않는다.**

## 입력과 출력 예시

### 입력: 사진 1장과 습득자가 선택한 카테고리

필통 비교 실험에서 사용한 입력은 다음과 같다. 브랜드·색상·재질·핀에 대한 설명은 사전에 입력하지 않고 사진에서 추출하도록 했다.

| 입력 | 실험에서 전달한 값 | 설명 |
| --- | --- | --- |
| 사진 | `case.jpg` 1장 | 검정 Carhartt 필통 사진. 전처리한 이미지를 모델에 전달 |
| 카테고리 | `필통` | 습득자 선택값. 모델에는 원문과 영어 힌트 `pencil_case`를 전달 |
| 상세 notes | 없음 | 물품의 상세 특징을 사용자에게 별도로 입력받지 않음 |

현재 코드는 카테고리를 생략한 이미지 단독 분석도 지원한다. 이 문서의 비교 결과는 두 방식 모두 `필통` 카테고리를 제공한 사례이다. `--backend`와 `--model`은 개발자가 고르는 실행 설정이며 습득자에게 요구하는 물품 정보가 아니다.

### 출력: 특징 7개를 담은 JSON

추출한 특징은 결과 JSON의 `data`에 들어간다. 아래 예시는 저장된 Gemini 필통 결과를 기준으로 한다.

| 특징 필드 | 타입 | 의미 | 실험 결과 예시 |
| --- | --- | --- | --- |
| `category` | string | 모델이 판단한 물품 종류 | `pencil_case` |
| `color` | string[] | 관찰된 색상 | `["black", "white", "orange"]` |
| `material` | string | 사진에서 확인 가능한 재질 | `fabric` |
| `brand` | string 또는 null | 식별된 브랜드 | `Carhartt` |
| `visible_text` | string[] | 사진에 실제로 보이는 문구 | `["carhartt"]` |
| `distinctive_features` | string[] | 부착물·무늬·형태 등 구별되는 특징 | 로고 패치, 상단 손잡이, 측면 지퍼, 금속 핀 2개 |
| `description` | string | 관찰 특징을 합친 요약 설명 | 검정 천 재질의 필통과 로고·핀·손잡이·지퍼에 대한 설명 |

아래는 실제 Gemini CLI 저장 결과에서 입력·특징·시간 관련 필드를 발췌한 JSON이다. 특징 문자열은 모델 출력 원문을 유지했다.

```json
{
  "success": true,
  "backend": "gemini",
  "model_name": "gemini-3.6-flash",
  "input_category": "필통",
  "data": {
    "category": "pencil_case",
    "color": ["black", "white", "orange"],
    "material": "fabric",
    "brand": "Carhartt",
    "visible_text": ["carhartt"],
    "distinctive_features": [
      "Carhartt logo patch on front",
      "Top fabric loop handle",
      "Side zipper closure",
      "Two metal pins attached near the top right"
    ],
    "description": "A black fabric pencil case featuring a Carhartt logo patch on the front, two metal pins attached near the top right corner, a top fabric loop handle, and a side zipper closure."
  },
  "warnings": [],
  "timings_ms": {
    "preprocessing": 109.76,
    "model_load": 395.38,
    "inference": 20749.36,
    "total": 21259.9
  }
}
```

`input_category`는 습득자가 입력한 값이고 `data.category`는 모델이 판단한 값이다. 의미 특징은 영어로 생성하고 `visible_text`는 사진의 원문 표기를 유지한다. 불확실한 재질은 `unknown`, 브랜드는 `null`, 확인할 수 없는 목록형 특징은 빈 배열로 남기도록 지시한다.

전체 CLI 파일에는 위 필드 외에 이미지·프롬프트 해시와 생성 설정인 `comparison_metadata`, API 토큰 사용량인 `usage`도 저장한다. `warnings`는 이미지 품질 등의 경고이고 `timings_ms`는 밀리초 단위의 처리 시간이다. 이 예시는 CLI 결과이며 로컬 HTTP 응답 형식은 [설계 문서](docs/design.md)에 정리했다.

## 폴더 구성

```text
imageprocess/
├── README.md
├── docs/
│   ├── usage.md                 # 환경 준비, 사용자 실행 명령, 설정
│   ├── design.md                # 현재 처리 방식·출력·API와 미구현 범위
│   └── experiments.md           # 기존 Qwen/Gemini 결과 검토와 비교 한계
├── app/
│   ├── analyze_image.py         # 사진 파일 분석 CLI
│   ├── compare_results.py       # 저장된 결과 비교 CLI
│   ├── main.py                  # 로컬 FastAPI 진입점
│   ├── api/                     # health/analyze 라우트
│   ├── core/                    # 설정·오류·로그·요청 제한
│   ├── models/                  # 특징 및 응답 스키마
│   └── services/                # 전처리·Qwen·Gemini 어댑터
├── tests/
├── requirements.txt            # 공통 실행 의존성
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
# Gemini로 분석
python -m app.analyze_image "사진경로.jpg" --backend gemini --category 필통 --output outputs/gemini.json

# Qwen으로 분석 (Apple Silicon용 MLX)
python -m app.analyze_image "사진경로.jpg" --backend mlx --category 필통 --output outputs/qwen.json

# 저장된 결과만 비교 — 추론/API 호출 없음
python -m app.compare_results outputs/qwen.json outputs/gemini.json --output outputs/comparison.md
```

설치·키 파일 작성·모델 준비·전체 설정은 [실행 안내](docs/usage.md), 입출력과 로컬 HTTP 인터페이스는 [설계 및 구현 범위](docs/design.md)에 정리했다.

## Qwen / Gemini 비교: 필통 사진 실험

검정 Carhartt 필통 사진 1장과 `필통` 카테고리로 저장한 기존 결과를 비교했다. Qwen은 `mlx-community/Qwen2.5-VL-3B-Instruct-4bit`의 로컬 MLX 결과, Gemini는 `gemini-3.6-flash`의 API 결과이다. 원본 사진과 JSON 파일은 로컬에 보존하고 아래에 주요 결과를 요약했다.

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

## 남은 협의

| 항목 | 협의 내용 |
| --- | --- |
| 프런트엔드 | 카테고리만 받는 방향, 목록·코드, 재촬영 안내 |
| Raspberry Pi | 촬영 방향·해상도·화질, 기존 2장 촬영 설계에 맞는 입력 |
| 백엔드 | 등록 건과 사진 연결, 이미지 전달·호출 시점·결과 저장, 실패·재시도 |
| 소유 확인 AI | 필요한 특징, 확인 불가 값의 활용, 특징 공개 범위 |
| 모델 및 운영 | Qwen/Gemini 선택, 실행 위치, 품질·시간·비용 기준 |

현재 코드는 사진 2장 동시 분석이나 결과 통합을 구현하지 않았다. 특징 필드와 오류 형식도 시제품 규격이며, 등록 성공·보관함 개방 조건과 연결하지 않는다. 합의된 파트 간 규격은 팀 공통 문서에 반영한다.
