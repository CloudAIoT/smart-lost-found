"""Run from backend with python -m app.ai_ownership_verification.experiments.generate_questions."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import httpx
from pydantic import TypeAdapter, ValidationError

from app.config import GeminiSettings
from ..gemini import GeminiError, GeminiQuestionGenerator, PROMPT_PATH
from ..schemas import ExperimentCase


HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "data/question_cases.json"


def load_cases(path: Path = CASES_PATH) -> list[ExperimentCase]:
    cases = TypeAdapter(list[ExperimentCase]).validate_json(path.read_text(encoding="utf-8"), strict=True)
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Case IDs must be nonempty and unique")
    return cases


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_revision() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=HERE, text=True,
            stderr=subprocess.DEVNULL, timeout=3,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def run_cases(
    cases: list[ExperimentCase],
    settings: GeminiSettings,
    *,
    live: bool,
    repeat: int,
    client: httpx.Client,
) -> dict:
    generator = GeminiQuestionGenerator(settings, client)
    if live:
        generator.require_configuration()
    rows = []
    requests = 0
    stop_errors = {"AUTHENTICATION_FAILED", "RATE_LIMITED", "MODEL_UNAVAILABLE", "INVALID_REQUEST"}
    for repetition in range(1, repeat + 1):
        for case in cases:
            row = {
                "case_id": case.case_id,
                "repetition": repetition,
                "expected_status": case.expected_status,
                "eligible_feature_ids": [feature.feature_id for feature in case.item.usable_features],
                "review_points": case.review_points,
                "manual_review": "not_performed",
            }
            if not live:
                row.update({"outcome": "not_run", "planned_api_call": bool(case.item.usable_features)})
            else:
                requests += int(bool(case.item.usable_features))
                try:
                    result = generator.generate(case.item)
                    row.update({
                        "outcome": "completed",
                        "api_called": result.api_called,
                        "result": result.batch.model_dump(),
                        "expected_status_matches": result.batch.status == case.expected_status,
                        "latency_ms": result.latency_ms,
                        "model_version": result.model_version,
                        "usage": result.usage,
                        "raw_text": result.raw_text,
                    })
                except GeminiError as exc:
                    row.update({"outcome": "error", "error_code": exc.code})
            rows.append(row)
            if row.get("error_code") in stop_errors:
                break
        if rows[-1].get("error_code") in stop_errors:
            break
    return {
        "schema_version": "question-experiment-v1",
        "mode": "live" if live else "dry_run",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_revision(),
        "source_hashes": {
            str(path.relative_to(HERE.parent)): file_hash(path)
            for path in (PROMPT_PATH, CASES_PATH, HERE.parent / "gemini.py", HERE.parent / "schemas.py", Path(__file__).resolve())
        },
        "requested_model": settings.gemini_model,
        "settings": {
            "timeout_seconds": settings.gemini_timeout_seconds,
            "max_output_tokens": settings.gemini_max_output_tokens,
            "temperature": "provider_default",
            "automatic_retries": 0,
        },
        "synthetic_data_only": True,
        "repeat": repeat,
        "planned_runs": len(cases) * repeat,
        "planned_api_requests": sum(bool(case.item.usable_features) for case in cases) * repeat,
        "actual_api_requests": requests,
        "cases": [case.model_dump() for case in cases],
        "runs": rows,
        "limitations": [
            "dry_run validates inputs and records the plan; it does not generate questions.",
            "Expected status and automated validation are not a human quality review.",
            "No ownership decision, return code, public endpoint, or locker action is implemented.",
        ],
    }


def markdown_report(report: dict) -> str:
    lines = [
        "# 질문 생성 실험 기록",
        "",
        f"- 실행 모드: {report['mode']}",
        f"- 기록 시각(UTC): {report['created_at']}",
        f"- 요청 모델: {report['requested_model']}",
        f"- 계획 사례 실행 수: {report['planned_runs']}",
        f"- 실제 API 요청 수: **{report['actual_api_requests']}**",
        "",
        "dry_run은 입력 검증과 실행 계획 기록이다. 생성 품질·처리 시간·비용을 측정한 결과가 아니다."
        if report["mode"] == "dry_run"
        else "실제 응답을 기록했다. 상태 일치 여부와 JSON 검증은 질문 품질이나 소유 확인 성공을 증명하지 않는다.",
        "",
        "| 사례 | 회차 | 실행 결과 | 응답 상태/오류 | 수동 검토 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["runs"]:
        detail = row.get("error_code") or row.get("result", {}).get("status", "미실행")
        lines.append(f"| {row['case_id']} | {row['repetition']} | {row['outcome']} | {detail} | 미수행 |")
    lines += [
        "", "## 후속 확인", "",
        "- 실제 호출 후 질문에 정답이나 바꿔 표현한 힌트가 포함되는지 사람이 검토한다.",
        "- 특징과 질문이 대응하는지, 소유자가 답할 수 있는지 확인한다.",
        "- 동일 사례 반복과 프롬프트 변경을 비교한다.",
        "- 답변 평가·승인 기준은 후속 실험과 팀 협의로 정한다.", "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic ownership-question experiment")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate samples without contacting Gemini")
    mode.add_argument("--live", action="store_true", help="Explicitly send synthetic samples to Gemini")
    parser.add_argument("--case", action="append", dest="case_ids", help="Run a specific case ID")
    parser.add_argument("--repeat", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--model", help="Override GEMINI_MODEL for this experiment")
    parser.add_argument("--output", type=Path, help="New .json output path; existing files are never overwritten")
    args = parser.parse_args(argv)
    output = args.output or HERE / "results" / (
        f"questions_{'live' if args.live else 'dry_run'}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json"
    )
    if output.suffix.lower() != ".json":
        parser.error("--output must end in .json")
    if output.exists() or output.with_suffix(".md").exists():
        parser.error("Output already exists; use a new filename")
    try:
        cases = load_cases()
        if args.case_ids:
            unknown = set(args.case_ids) - {case.case_id for case in cases}
            if unknown:
                parser.error("Unknown case ID")
            cases = [case for case in cases if case.case_id in args.case_ids]
        settings = GeminiSettings(**({"gemini_model": args.model} if args.model else {}))
        with httpx.Client(trust_env=False) as client:
            report = run_cases(cases, settings, live=args.live, repeat=args.repeat, client=client)
    except (ValidationError, ValueError):
        print("실험 입력 또는 설정이 올바르지 않습니다. 원본 값은 출력하지 않습니다.")
        return 2
    except GeminiError as exc:
        print(f"실험을 시작하지 못했습니다: {exc.code}")
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    with output.with_suffix(".md").open("x", encoding="utf-8") as stream:
        stream.write(markdown_report(report))
    print(f"mode={report['mode']}; cases={len(cases)}; actual_api_requests={report['actual_api_requests']}")
    print(f"Saved: {output}")
    return 1 if any(row["outcome"] == "error" for row in report["runs"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
