"""실제 Pi와 실제 S3에 붙여 손으로 확인할 때 쓰는 로컬 백엔드 실행기.

앱 코드는 그대로 돌린다. `--stub-analyzer`를 줄 때만 AI 분석기를 대역으로 바꾼다. AI 구현이
아직 없어 그대로 두면 `/analysis`가 503 AI_NOT_CONFIGURED이고 `/deposit/open`은 409
ANALYSIS_REQUIRED로 막힌다. 대역은 이 스크립트 안에만 있고 백엔드 패키지에는 들어가지 않는다.

실행:
    python tests/manual/local_server.py --stub-analyzer

준비물은 저장소 README의 '실제 Pi와 S3로 손으로 확인하기' 절에 있다.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# 백엔드를 컨테이너가 아니라 이 PC에서 돌린다. backend/.env의 DB_HOST=db는 Compose 안에서만
# 통하는 이름이라, 따로 지정하지 않으면 Compose가 내보낸 127.0.0.1:5432로 붙는다.
os.environ.setdefault("DB_HOST", "127.0.0.1")

import uvicorn  # noqa: E402

from app.api import get_analyzer  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.integrations import AnalysisResult  # noqa: E402
from app.main import app  # noqa: E402

REQUIRED = {
    "s3_bucket": "S3_BUCKET",
    "s3_region": "S3_REGION",
    "preview_ttl_seconds": "PREVIEW_TTL_SECONDS",
    "max_image_bytes": "MAX_IMAGE_BYTES",
    "pi_timeout_seconds": "PI_TIMEOUT_SECONDS",
    "command_ttl_seconds": "COMMAND_TTL_SECONDS",
}


class StubAnalyzer:
    """AI 대역. 특징을 만들지 않고 입력 이미지 ID만 그대로 돌려준다."""

    def analyze(self, registration_id, images):
        return AnalysisResult(image_ids=tuple(value.image_id for value in images),
                              features={"manual_check_only": True})


def check(settings) -> None:
    missing = [name for field, name in REQUIRED.items() if getattr(settings, field) in (None, "")]
    if not settings.pi_devices:
        missing.append("PI_DEVICES")
    if not settings.local_development:
        missing.append("LOCAL_DEVELOPMENT=true")
    if missing:
        raise SystemExit("backend/.env에 다음 값이 필요하다: " + ", ".join(missing))


def main() -> None:
    parser = argparse.ArgumentParser(description="실제 Pi·S3에 붙는 로컬 백엔드")
    parser.add_argument("--stub-analyzer", action="store_true",
                        help="AI 분석기를 대역으로 바꿔 개방까지 진행한다 (손 확인 전용)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BACKEND_PORT", 8000)))
    args = parser.parse_args()

    settings = get_settings()
    check(settings)
    for kiosk_id, device in settings.pi_devices.items():
        print(f"키오스크 {kiosk_id}")
        print(f"  촬영 Pi   {device.get('capture_url')}")
        print(f"  보관함 Pi {device.get('locker_url')}")
    print(f"S3 {settings.s3_bucket} ({settings.s3_region})")
    print(f"DB {settings.db_host}:{settings.db_port}")
    if args.stub_analyzer:
        app.dependency_overrides[get_analyzer] = lambda: StubAnalyzer()
        print("AI 분석기를 대역으로 돌린다. 특징 추출 결과는 확인할 수 없다.")
    print(f"Swagger http://{args.host}:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
