# AWS 백엔드 연결: 사진 2장 처리 모듈

이미지 파트는 전처리·통합 프롬프트·특징 JSON Schema·응답 검증을 제공한다. **HTTP API, 모델 API 호출·키·할당량 정책, S3 접근, DB 저장, EC2 배포는 백엔드 담당자가 연결한다.** 이 변경은 EC2를 배포하거나 새 업무 API를 추가하지 않는다.

## 모듈 사용

저장소 루트가 Python 경로에 있으면 다음처럼 가져온다. 백엔드의 `app`과 충돌하지 않도록 내부 의존성은 상대 import를 사용한다. 이 import와 아래 준비·검증 함수는 Google SDK나 MLX를 로딩하거나 네트워크를 호출하지 않는다.

```python
from imageprocess import ImageInput, ServiceError, prepare_item, parse_item_output

prepared = prepare_item(
    registration_id,
    (
        ImageInput(first_image_id, first_jpeg_bytes),
        ImageInput(second_image_id, second_jpeg_bytes),
    ),
    category=category_code_or_name,
)

# 백엔드 담당자가 구현하는 호출 함수이다. 두 PIL 이미지를 순서대로 한 요청에 전달한다.
raw_json = invoke_model(
    prompt=prepared.prompt,
    images=prepared.images,
    response_schema=prepared.response_schema,
)
result = parse_item_output(prepared, raw_json)
payload = result.model_dump(mode='json')
```

`invoke_model`은 제공되는 라이브러리 함수가 아니라 연결 지점이다. Gemini SDK라면 contents에 `[prepared.prompt, *prepared.images]`, JSON 응답 설정에 `prepared.response_schema`를 사용할 수 있다. UUID는 모델에게 맞히게 하지 않고 호출자가 제공한 값을 결과에 유지한다. 모델 응답은 Features JSON 객체의 텍스트이며 별도 성공 envelope나 ID를 포함하지 않는다.

## 입력

| 값 | 계약 |
|---|---|
| `registration_id` | 등록 UUID |
| `images` | 정확히 두 `ImageInput`, 촬영 순서 1 → 2 |
| `ImageInput.image_id` | 서로 다른 UUID. 각 S3 사진에 대응하는 DB ID |
| `ImageInput.jpeg` | 이미지 원본 bytes. BE는 기존 `S3ImageStore.read`로 받은 JPEG를 전달 |
| `category` | 선택적 code/name. `primary_category_id` UUID를 그대로 모델에 보내지 않고 DB 카테고리로 해석 |

각 사진은 실제 파일 형식·무결성·파일 크기·디코딩 픽셀 수를 검사한 뒤 EXIF 회전, RGB, 비율 유지 축소를 적용한다. 기본 사진당 10MiB·2,500만 픽셀·긴 변 1280px다. 기존 전처리는 JPEG/PNG/WEBP를 지원하며 BE 촬영 입력은 JPEG다. 최대 두 파일의 합계는 기본 20MiB다. HTTP 요청 본문 한도·스트리밍 수신 한도는 API 담당자가 별도로 적용한다.

서버에서 명시적으로 한도를 넘기려면 `imageprocess.app.core.config.Settings(_env_file=None, ...)`를 만들어 `prepare_item(..., settings=...)`로 전달할 수 있다. 원본을 파일로 저장하거나 URL을 다운로드하는 기능은 없다. 호출자는 필요 시 준비된 PIL 이미지를 `close()`하여 해제한다.

## 결과

```json
{
  "schema_version": "fixed_common_v2",
  "registration_id": "11111111-1111-1111-1111-111111111111",
  "image_ids": [
    "22222222-2222-2222-2222-222222222222",
    "33333333-3333-3333-3333-333333333333"
  ],
  "input_category": "필통",
  "data": {
    "category": "pencil_case",
    "color": ["black"],
    "shape": "직사각형",
    "surface_texture": "직조 표면",
    "brand": null,
    "visible_text": null,
    "structure": ["측면 지퍼"],
    "distinctive_features": null
  },
  "warnings": []
}
```

위는 구조 예시다. 실제 2장 추론 결과가 아니다. [특징 JSON Schema](feature-schema.json)와 [10종 단일 사진 실험](experiments/single-photo-10-cases.md)을 함께 참고한다. 공통 키 7개와 특이 특징 키 1개는 모두 필수이며 모르는 값은 null이다. 빈 목록은 null로 정규화한다.

두 사진용 프롬프트는 하나의 물건에 대한 보완 정보를 합치고, 회전으로 위치가 달라지거나 같은 핀이 두 사진에 나타나도 중복해서 세지 않도록 지시한다. 모순을 해소하지 못한 공통 값은 null, 명백히 다른 물건인 경우 전체 null을 요청한다. 이는 모델 지침이며 실제 동일 물건 판별을 보장하는 검증기는 아니다.

`parse_item_output`은 키·타입·길이, 중복 JSON 키, NaN/Infinity를 검사한다. 색·형태·질감·글자·구조·특이 특징 중 어떤 관찰도 없는 결과는 `INSUFFICIENT_VISUAL_FEATURES`로 거부한다. 카테고리/브랜드만 채워진 결과도 성공으로 반환하지 않는다. 이 검사는 사실성·소유권 검증을 대신하지 않는다.

## 기존 백엔드 경계에 연결

현재 `backend/app/integrations.py`의 `FeatureAnalyzer`는 등록 ID와 JPEG 두 장을 받고 `AnalysisResult(image_ids, features)`를 반환한다. 다음은 담당자가 해당 구현에서 사용할 연결 예시다.

```python
from app.integrations import AnalysisResult, IntegrationError
from imageprocess import ImageInput, ServiceError, prepare_item, parse_item_output

def analyze(registration_id, images):
    try:
        prepared = prepare_item(
            registration_id,
            tuple(ImageInput(image.image_id, image.jpeg) for image in images),
            category=resolve_category_label(registration_id),
        )
        raw = invoke_model(
            prompt=prepared.prompt,
            images=prepared.images,
            response_schema=prepared.response_schema,
        )
        result = parse_item_output(prepared, raw)
    except ServiceError as error:
        raise IntegrationError(error.code) from None
    return AnalysisResult(image_ids=result.image_ids, features=result.data.model_dump(mode='json'))
```

`resolve_category_label`와 `invoke_model`은 백엔드가 구현한다. 모델 호출의 timeout·429·차단 등도 백엔드의 `IntegrationError`로 변환해야 기존 `draft/failed` 복구 흐름이 작동한다. 반환 image_ids는 입력과 같은 순서여야 하며 DB의 `analysis_features`에는 `data`만 저장한다. mock 결과를 등록 성공으로 쓰지 않는다.

API/서버 주소를 고정하거나 Git에 키를 넣지 않는다. 소유 확인 모듈의 verified/visibility/distinguishing 변환도 별도 책임이다. 캐릭터명·브랜드를 인식했다고 자동으로 verified=true로 설정하지 않는다.

## 서버 패키징

백엔드가 처리 함수만 사용하는 경우 `requirements-core.txt`면 된다. 모델 SDK는 담당자가 선택한다. 루트의 imageprocess 패키지 디렉터리를 서버 이미지에 복사하고 그 부모 디렉터리를 Python 경로에 둔다. 예를 들어 backend/app이 `/app/app`인 이미지에서는 다음과 같이 둘 수 있다.

```dockerfile
COPY imageprocess /app/imageprocess
RUN pip install --no-cache-dir -r /app/imageprocess/requirements-core.txt
```

프로젝트의 Dockerfile·배포 워크플로·SSM 설정은 변경하지 않는다. 이미지 처리 오프라인 테스트용 CI만 추가한다. 백엔드 담당자가 의존성 충돌과 메모리를 확인한 뒤 연결한다. 개인 Conda 환경, 사진·출력·캐시가 Docker build context에 섞이지 않도록 allowlist 또는 `.dockerignore`를 적용한다.

## 로컬 검증 도구

`imageprocess/` 안에서 실행한다. 준비 명령은 API 호출이나 모델 다운로드를 하지 않는다. UUID 옵션을 생략하면 로컬 시험용으로 생성하며 BE에서는 실제 DB ID를 전달한다.

```bash
python -m app.analyze_item first.jpg second.jpg --category 필통 --prepare-only --output outputs/prepared_item.json
```

실제 추론을 사용자가 실행할 때만:

```bash
python -m app.analyze_item first.jpg second.jpg --backend gemini --category 필통 --output outputs/two_photo_item.json
```

`--registration-id UUID`, `--image-ids UUID UUID`, `--model`도 지원한다. 로컬 기존 `/analyze`는 단일 사진 실험용이며 이 두 사진용 업무 API를 대신하지 않는다. 실제 두 사진 품질과 EC2 API → S3 → 모델 → DB 흐름은 아직 검증하지 않았다.
