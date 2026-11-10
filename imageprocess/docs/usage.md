# 실행 안내

모든 명령은 팀 저장소의 `imageprocess/` 안에서 실행한다. 백엔드와 Python 패키지·의존성을 별도로 관리한다. 아래 실제 분석 명령은 사용자가 필요할 때 실행하며 모델 다운로드나 API 호출을 수반할 수 있다.

## 1. 환경과 의존성

기존 환경을 사용할 때는 다시 생성하지 않는다. `.venv`가 함께 활성화되어 있으면 먼저 `deactivate`로 해제한 뒤 Conda 환경을 활성화한다. VS Code에서도 같은 Conda 인터프리터를 선택한다.

```bash
cd imageprocess
conda activate gen_conda
python -c "import sys, platform; print(sys.executable, sys.version, platform.machine())"
python -m pip install --upgrade -r requirements-dev.txt
```

새 환경이 필요한 경우에만 다음을 실행한다. `environment.yml`은 Python 3.11과 공통 의존성만 설치하므로 모델을 미리 선택하지 않는다. 이름은 `-n`으로 바꿀 수 있다.

```bash
conda env create -f environment.yml -n gen_conda
conda activate gen_conda
```

사용할 분석 방식에 맞춰 추가 설치한다. 두 방식을 비교하려면 Apple Silicon 환경에서 두 파일을 모두 설치한다. 설치만으로 모델을 다운로드하거나 API를 호출하지 않는다.

```bash
# Gemini SDK
python -m pip install --upgrade -r requirements-gemini.txt

# Qwen의 MLX 실행 경로 — Apple Silicon용
python -m pip install --upgrade -r requirements-mlx.txt

# 테스트 도구 및 설치 상태 확인
python -m pip install --upgrade -r requirements-dev.txt
python -m pip check
python -m pytest -q
```

`requirements-mlx.txt`는 Linux 백엔드 환경에 설치하지 않는다. 실제 모델을 사용하지 않는 테스트는 기본·개발 의존성으로 실행할 수 있다. 의존성 파일은 허용 버전 범위이며 완전히 고정된 배포용 lock 파일은 아니다.

## 2. VS Code에서 로컬 설정 작성

처음 설정하는 경우 `.env.example`을 **같은 `imageprocess/` 폴더의 `.env`로 복사**하고 VS Code로 편집한다. 기존 `.env`가 있다면 덮어쓰지 말고 필요한 항목만 수정한다. `.env`는 Git에서 제외한다.

```dotenv
GEMINI_API_KEY=발급받은_실제_키
GEMINI_MODEL_NAME=gemini-3.6-flash
```

위 키 값은 자리 표시자이며 실제 키는 각자 로컬 파일에만 작성한다. 모델 ID는 현재 코드의 비교용 기본값이다. 다른 모델은 본인 계정에서 사용할 수 있는 ID를 지정한다. 환경변수가 `.env`보다 우선하므로 예전 키를 터미널에서 export했다면 `unset GEMINI_API_KEY` 후 다시 실행한다. 설정은 실행한 현재 폴더의 `.env`를 읽는다.

로컬 Qwen 기본 모델은 `mlx-community/Qwen2.5-VL-3B-Instruct-4bit`이다. 첫 실제 분석 시 모델과 processor를 내려받고 이후 다운로드 캐시를 재사용한다. 이미 준비한 모델 디렉터리는 `--model ./models/모델폴더`로 지정할 수 있다.

## 3. 사진에서 특징 추출

자신이 준비한 사진을 입력한다. 아래 `사진경로.jpg`는 자리 표시자이다. 이 작업 공간에서 보존한 기존 사진은 `local/images/case.jpg`에 있으며 GitHub에는 포함하지 않는다.

```bash
python -m app.analyze_image "사진경로.jpg" --backend gemini --category 필통 --output outputs/gemini.json
python -m app.analyze_image "사진경로.jpg" --backend mlx --category 필통 --output outputs/qwen.json
```

CLI는 `--backend` 생략 시 **mlx**를 사용한다. `.env`의 `INFERENCE_BACKEND`로 CLI 기본값이 바뀌지 않으므로 비교 시에는 위처럼 명시한다. `--model 모델ID_또는_경로`로 선택한 backend의 모델만 바꿀 수 있다. Gemini 실행 시 전처리된 사진과 카테고리 문맥이 외부 API로 전송된다.

`--category`는 선택 사항이다. `필통`, `지갑`, `가방`, `우산`, `휴대폰`/`스마트폰`, `열쇠`, `기타`는 각각 영어 힌트로 변환한다. 다른 카테고리는 원문을 힌트로 전달한다. 1–100자의 출력 가능한 한 줄 문자열을 허용하며 상세 notes 옵션은 없다. 결과의 `input_category`는 입력, `data.category`는 모델의 분류이다.

성공하면 stdout과 `--output` 파일에 UTF-8 JSON을 출력하고 종료 코드 0을 반환한다. 실패하면 오류 JSON과 종료 코드 1을 반환하며 성공 결과 파일을 새로 쓰지 않는다. 이전에 저장한 같은 이름의 파일은 남아 있을 수 있으므로 현재 실행의 종료 코드와 stdout을 먼저 확인한다.

CLI 결과에는 특징 `data`, 품질 `warnings`, `backend`, `model_name`, 비교 조건 `comparison_metadata`, 사용량 `usage`, 구간별 시간 `timings_ms`가 포함된다. Gemini의 `model_load`는 클라이언트 초기화 시간이고 Qwen은 실제 모델 로딩 시간이므로 의미가 다르다.

실제 분석 없이 파일 입력·전처리·저장 경로만 확인하려면 mock를 명시한다.

```bash
python -m app.analyze_image "사진경로.jpg" --backend mock --category 필통 --output outputs/mock.json
```

mock는 `MOCK_INFERENCE` 경고와 unknown 특징을 반환한다. 품질 평가나 실제 분석 성공의 근거로 사용하지 않는다. 실제 분석 실패 시 mock로 자동 전환하지 않는다.

## 4. 저장된 결과 비교

다음 명령은 JSON 파일만 읽으며 모델과 API를 호출하지 않는다.

```bash
python -m app.compare_results outputs/qwen.json outputs/gemini.json --output outputs/comparison.md
```

이 작업 공간에서 보존한 이전 Qwen 결과를 사용할 수도 있다.

```bash
python -m app.compare_results outputs/legacy/result_with_category.json outputs/gemini.json --output outputs/comparison.md
```

원본·전처리 이미지·프롬프트 해시, 전처리 크기와 생성 설정을 함께 확인한다. 예전 결과에 해당 메타데이터가 없으면 `확인 불가`로 표시한다. 보고서는 정확도 점수나 자동 우열을 계산하지 않는다.

## 5. 로컬 HTTP 인터페이스

이미지 특징 추출은 CLI만으로 검증할 수 있다. HTTP 경로가 필요한 경우 아래처럼 실행한다. 포트 8001은 같은 컴퓨터의 백엔드와 충돌을 피하기 위한 예시이다. worker는 1개로 둔다.

```bash
INFERENCE_BACKEND=mock python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1
# 실제 분석 서버는 mock 대신 gemini 또는 mlx를 명시
```

```bash
curl -i http://127.0.0.1:8001/health
curl -i http://127.0.0.1:8001/analyze \
  -H 'X-Request-ID: local-check-01' \
  -F 'image=@local/images/case.jpg' \
  -F 'category=필통'
```

실제 사진이 있는 경로로 바꾼다. 자동 API 문서는 `/docs`와 `/openapi.json`에 있다. 이 인터페이스에는 인증·DB 저장·AWS 연결을 구현하지 않았다. 운영 인터페이스는 팀 협의 대상이다.

## 6. 설정값

전체 예시는 [`.env.example`](../.env.example)에 있다. API의 backend는 `INFERENCE_BACKEND`를 따르고, CLI는 `--backend`를 따른다.

| 변수 | 기본값 | 용도 |
| --- | --- | --- |
| `INFERENCE_BACKEND` | `mlx` | HTTP 서버의 분석 방식: mlx/gemini/mock |
| `MODEL_NAME` | `mlx-community/Qwen2.5-VL-3B-Instruct-4bit` | MLX 모델 ID 또는 디렉터리 |
| `GEMINI_MODEL_NAME` | `gemini-3.6-flash` | Gemini 모델 ID |
| `GEMINI_API_KEY` | 없음 | 로컬 파일 또는 환경변수의 API 키 |
| `GEMINI_TIMEOUT_SECONDS` | `90` | Gemini 응답 대기 제한 |
| `GEMINI_MAX_OUTPUT_TOKENS` | `4096` | Gemini 출력 토큰 한도 |
| `MAX_TOKENS` | `512` | Qwen 생성 토큰 한도 |
| `MAX_IMAGE_SIZE` | `1280` | 전처리 후 최대 변 길이 |
| `MAX_UPLOAD_BYTES` | `10485760` | 이미지 파일 바이트 한도: 10MiB |
| `MAX_IMAGE_PIXELS` | `25000000` | 디코딩 전 픽셀 수 한도 |
| `BLUR_THRESHOLD` | `30` | 축소 이미지의 흐림 판단 기준 |
| `DARKNESS_THRESHOLD` | `25` | 평균 밝기 기준 |
| `REJECT_LOW_QUALITY` | `false` | 기본은 품질 경고, true이면 거부 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | `python -m app.main` 실행 시 사용 |

Uvicorn을 직접 실행할 때 주소와 포트는 명령 옵션으로 지정한다. 여러 worker를 띄우면 프로세스마다 모델이 별도로 로딩된다.
